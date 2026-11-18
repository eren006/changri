"""扩展：关系线（拉线、确认、撤回、查看、统计）。"""
import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from changri.core.qqsend import make_send, reply_buttons, reply_long  # noqa: E402
from changri.core.buttons import Btn
from changri.core import admins, messaging, relations, roles
from changri.core.db import get_config, get_int

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"


def _bid() -> str:
    """当前处理这条消息的机器人 ID（openid 是每个机器人各一套，认人要带上它）。"""
    return current_bot.get().self_id


def _sender(event: GroupMessageCreateEvent) -> str:
    return event.author.member_openid


def _me(event):
    return roles.find_role_for_sender(_bid(), event.group_openid, _sender(event))


def _send_fn() -> messaging.Send:
    return make_send()


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _name_args(args) -> list[str]:
    return args.extract_plain_text().split()


def _admin_only(matcher_name: str):
    return "❌ 权限不足，仅管理员可用。"


# ================= 关系线 =================

async def _notify(bot: Bot, role_id: int, text: str, buttons=None) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, _send_fn(), buttons=buttons)
    return ok


add_rel = _cmd("拉线")


@add_rel.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await add_rel.finish("⚠️ 找不到你的角色登记信息，请先在个人群里「创建新角色」。")
    parts = args.extract_plain_text().strip().split(None, 1)
    if len(parts) != 2:
        await add_rel.finish("格式：拉线 对方名 内容")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await add_rel.finish("❌ 找不到角色")
    ok, hint = relations.add_detail(me["id"], target["id"], target["name"], parts[1].strip())
    if not ok:
        await add_rel.finish(hint)
    new_line, _, stat = hint.partition("\n") if hint.startswith("✨") else ("", "", hint)
    delivered = await _notify(bot, target["id"], f"📝 来自「{me['name']}」的新关系细节：\n{parts[1].strip()}\n\n（使用「查看关系线 {me['name']}」查看完整记录）",
                              buttons=[Btn("回一段", f"拉线 {me['name']} ", enter=False),          # 填进输入框，接着写
                                       Btn("看全部", f"查看关系线 {me['name']}", style="secondary")])
    head = (new_line + "\n") if new_line else ""
    if delivered:
        await add_rel.finish(f"{head}✅ 细节已同步至「{target['name']}」的绑定群\n{stat}")
    await add_rel.finish(f"{head}✅ 细节已记录，但「{target['name']}」尚未绑定可用的群，无法实时同步。\n{stat}")


confirm_rel = _cmd("确认关系线")


@confirm_rel.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    parts = _name_args(args)
    target = roles.get_role_by_name(parts[0]) if parts else None
    if not me or not target or not relations.confirm(me["id"], target["id"]):
        await confirm_rel.finish("未找到该关系线")
    await confirm_rel.finish(f"✅ 你已确认与「{target['name']}」的关系线为完成状态。")


withdraw_rel = _cmd("撤回关系")


@withdraw_rel.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    parts = args.extract_plain_text().strip().split(None, 1)
    if not me or len(parts) != 2:
        await withdraw_rel.finish("格式：撤回关系 对方角色名 要撤回的内容")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await withdraw_rel.finish("没有可撤回的细节记录。")
    res = relations.withdraw(me["id"], target["id"], parts[1].strip())
    if res == "":
        await withdraw_rel.finish("没有可撤回的细节记录。")
    if res is None:
        await withdraw_rel.finish(f"未找到你发送的匹配内容：「{parts[1].strip()}」\n可使用「查看关系线 {target['name']}」查看所有细节。")
    await _notify(bot, target["id"], f"🗑️ 「{me['name']}」撤回了一条发给你的关系细节：\n\"{res}\"")
    await withdraw_rel.finish(f"✅ 已成功撤回你发送的细节：\n\"{res}\"")


view_rel = _cmd("查看关系线")


@view_rel.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await view_rel.finish("请先绑定角色")
    parts = _name_args(args)
    if parts:
        target = roles.get_role_by_name(parts[0])
        rel = relations.get_rel(me["id"], target["id"]) if target else None
        if not rel:
            await view_rel.finish(f"你与「{parts[0]}」之间暂无关系记录。")
        ds = relations.details(rel["id"])
        if not ds:
            await view_rel.finish(f"你与「{target['name']}」虽有关系线，但尚未添加任何细节。")
        text = f"📖 你与「{target['name']}」的关系线（{len(ds)} 段）\n" + "\n".join(f"\n【{d['from_name']}】\n{d['text']}" for d in ds)
        await reply_long(view_rel, text)
        await view_rel.finish()
    items = relations.list_for(me["id"])
    max_rel = get_int("max_relationships_per_user", 20)
    max_total = get_int("max_relationships_total", 0)
    reply = f"📚 「{me['name']}」的关系线列表：\n"
    if not items:
        await view_rel.finish(reply + "（暂无任何记录）")
    split = get_int("forward_split_threshold", 4000)
    pending = 0
    for it in items:
        tag = "【发起】" if it["mine"] else "【收到】"
        chars = f"⚠️{it['chars']}字·将拆分" if it["chars"] > split else f"{it['chars']}字"
        pending += bool(it["pending"])
        reply += f"{'✅' if it['confirmed'] else '⏳'} {tag} 与「{it['other_name']}」({it['count']}条 | {chars}){' 🔴待回' if it['pending'] else ''}\n"
    if pending:
        reply += f"\n🔴 待回复：{pending} 条（对方发了新细节，轮到你回复）"
    reply += f"\n📊 额度占用：发起 {sum(1 for i in items if i['mine'])}/{max_rel}"
    if max_total > 0:
        reply += f"　总数 {len(items)}/{max_total}（发起+收到）"
    reply += "\n💡 输入「查看关系线 名字」查看细节"
    await reply_buttons(view_rel, reply, [Btn(it["other_name"], f"查看关系线 {it['other_name']}") for it in items], per_row=3)
    await view_rel.finish()


rel_stats = _cmd("关系线统计")


@rel_stats.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await rel_stats.finish(_admin_only("关系线统计"))
    rows = relations.stats()
    if not rows:
        await rel_stats.finish("📭 当前没有任何关系线记录")
    await rel_stats.finish("📊 关系线数量\n" + "\n".join(f"{r['name']}：{r['n']}" for r in rows[:50]))


# ---------- 往「我的待回」里登记 ----------
from changri.core import pending  # noqa: E402


def _pending_rels(role) -> list[str]:
    if not relations.enabled():
        return []
    return [f"关系线：「{r['other_name']}」回了你，轮到你回（查看关系线 {r['other_name']}）" for r in relations.list_for(role["id"]) if r["pending"]]


pending.register("关系线", _pending_rels)


# ================= 公知 / 二表 =================

import io  # noqa: E402

from nonebot.adapters.qq import MessageSegment  # noqa: E402

from changri.core.qqsend import download_images  # noqa: E402
from changri.core import sheets  # noqa: E402


def _slices(png: bytes, step: int = 4200) -> list[bytes]:
    """很长的图切成几张发（被动回复同一条消息最多 5 次）。"""
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    if im.height <= step + 600:
        return [png]
    out = []
    for top in range(0, im.height, step):
        buf = io.BytesIO()
        im.crop((0, top, im.width, min(im.height, top + step))).save(buf, "PNG")
        out.append(buf.getvalue())
    return out[:5]


def _render_one(sh: dict, kind: str) -> bytes:
    import time as _t
    from changri.core.render import sheet_image
    imgs = [b for b in (sheets.image_bytes(n) for n in sh["images"]) if b]
    label = sheets.KINDS[kind]
    return sheet_image.render(f"{sh['name']} 的{label}", f"更新于 {_t.strftime('%m-%d %H:%M', _t.localtime(sh['updated_ts']))}",
                              sh["text"], [b[0] for b in imgs])


def _stack(pngs: list[bytes]) -> bytes:
    """把几张长图上下拼成一张。"""
    import io
    from PIL import Image
    ims = [Image.open(io.BytesIO(b)).convert("RGB") for b in pngs]
    w = max(i.width for i in ims)
    out = Image.new("RGB", (w, sum(i.height for i in ims)), "white")
    y = 0
    for i in ims:
        out.paste(i, ((w - i.width) // 2, y))
        y += i.height
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


async def _show(matcher, sh: dict | list, kind: str) -> None:
    """sh 是一个人的一份，或者一串（全员，拼成一张长图）。"""
    label = sheets.KINDS[kind]
    many = sh if isinstance(sh, list) else [sh]
    try:
        png = _render_one(many[0], kind) if len(many) == 1 else _stack([_render_one(x, kind) for x in many])
        for part in _slices(png):
            await matcher.send(MessageSegment.file_image(part))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{label}] 画图或发图失败，改发文字：{e}")
        await matcher.send("\n\n".join(f"【{x['name']} 的{label}】\n{x['text'] or '（只有图片，图片没发出来）'}" for x in many))


async def _tell_group(which: str, text: str) -> None:
    """往公告群（announce）或后台群（backstage，带内容给管理员备份）发一条普通消息。
    没设那个群就不发——后台群不能退回公告群，二表内容不能让大家看到。"""
    from changri.core import announce
    from changri.core.qqsend import send_to_group
    a = announce.get_announce() if which == "announce" else announce.get_backstage()
    bot = nonebot.get_bots().get(a[0]) if a else None
    if bot is None:
        return
    try:
        await send_to_group(bot, a[1], text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[公知/二表] {which} 群发不出去：{e}")


def _make_submit(kind: str):
    label = sheets.KINDS[kind]
    cmd = _cmd("提交" + label)

    @cmd.handle()
    async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
        me = _me(event)
        if not me:
            await cmd.finish(NEED_ROLE)
        text = args.extract_plain_text().strip()
        images = await download_images(args, limit=sheets.IMG_MAX)
        first = sheets.get(me["id"], kind) is None
        ok, info = sheets.submit(me["id"], kind, text, images)
        if not ok:
            await cmd.finish(f"❌ {info}\n用法：提交{label} 你的内容（可以同时发图片）。重复提交会覆盖上一次。")
        if first or kind == "gongzhi":            # 第一次交「已经上交」，公知再交「修改了公知」；二表重交不发
            head = f"📮 {me['name']} 已经上交{label}" if first else f"✏️ {me['name']} 修改了{label}"
            await _tell_group("announce", head)                       # 公告群：只发这一句
            n_img = len(images or [])
            await _tell_group("backstage", head + (f"\n\n{text}" if text else "") +
                                  (f"\n\n（附 {n_img} 张图，发「查看{label} {me['name']}」看）" if n_img else ""))
        extra = "；别人要等管理员开启「展示公知」才能看" if kind == "gongzhi" and not sheets.public() else ""
        await cmd.finish(f"✅ 「{me['name']}」的{label}已提交（{info}）。发「查看{label}」可以看到做成的长图{extra}。")
    return cmd


def _make_view(kind: str):
    label = sheets.KINDS[kind]
    cmd = _cmd("查看" + label)

    @cmd.handle()
    async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
        me = _me(event)
        name = args.extract_plain_text().strip()
        is_admin = admins.is_admin(_bid(), _sender(event)) if hasattr(admins, "is_admin") else False
        if kind == "gongzhi" and not name and (is_admin or sheets.public()):
            everyone = sheets.all_of(kind)       # 不带名字：看所有人的；带名字：只看这一个人
            if not everyone:
                await cmd.finish("还没有人提交公知。发「提交公知 内容」就能提交。")
            if len(everyone) > 1 and await reply_buttons(    # 能发按钮：列名字，点谁看谁（带他自己的图），不再拼一整张长图
                    cmd, f"📜 公知\n{len(everyone)} 位，点下面的名字查看（或发「查看公知 名字」）",
                    [Btn(x["name"], f"查看公知 {x['name']}") for x in everyone], per_row=3, fallback=False):
                await cmd.finish()
            await _show(cmd, everyone, kind)
            await cmd.finish()
        target = roles.get_role_by_name(name) if name else me
        if name and not target:
            await cmd.finish(f"❌ 找不到角色「{name}」。")
        if not target:
            await cmd.finish(NEED_ROLE)
        mine = bool(me) and target["id"] == me["id"]
        allowed = mine or is_admin or (kind == "gongzhi" and sheets.public())
        if not allowed:
            await cmd.finish("🔒 公知暂未开放展示，只能看自己的（发「查看公知」）。" if kind == "gongzhi" else "🔒 二表只有本人和管理员能看。")
        sh = sheets.get(target["id"], kind)
        if not sh:
            await cmd.finish(f"「{target['name']}」还没有提交{label}。" + (f"发「提交{label} 内容」就能提交。" if mine else ""))
        await _show(cmd, sh, kind)
        await cmd.finish()
    return cmd


for _k in sheets.KINDS:
    _make_submit(_k)
    _make_view(_k)
