"""扩展：关系线（拉线、确认、撤回、查看、统计）。"""
import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from changri.core.qqsend import finish_usage, make_send, reply_buttons, reply_long, send_card  # noqa: E402
from changri.core.buttons import Btn
from changri.core import admins, charprofile, messaging, relations, roles, season
from changri.core.db import get_config, get_int

NEED_ROLE = "你还没有角色。在你的个人群里发「创建新角色 名字」就能创建。"


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
    return "❌ 这条指令只有管理员能用。"


# ================= 关系线 =================

async def _notify(bot: Bot, role_id: int, text: str, buttons=None) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, _send_fn(), buttons=buttons)
    return ok


add_rel = _cmd("拉线")


@add_rel.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await add_rel.finish("你还没有角色。在你的个人群里发「创建新角色 名字」就能创建。")
    raw = args.extract_plain_text().strip()
    parts = raw.split(None, 1)
    if not raw and relations.enabled() and not relations.closed_note():   # 只发「拉线」：选人卡片，点名字填好「拉线 名字 」
        from changri.core import datepick
        await send_card(add_rel, *datepick.cmd_card("拉线 · 选一个人", "点名字会填进输入框，后面接着写这一段", me["id"], "拉线"))
    if len(parts) != 2:
        await finish_usage(add_rel, "格式：拉线 对方名 内容\n例：拉线 张三 那天在花园里，你递给我一把伞……", f"拉线 {parts[0]} " if parts else "拉线 ")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await add_rel.finish(f"❌ 找不到叫「{parts[0]}」的角色。")
    ok, hint = relations.add_detail(me["id"], target["id"], target["name"], parts[1].strip())
    if not ok:
        await add_rel.finish(hint)
    new_line, _, stat = hint.partition("\n") if hint.startswith("✨") else ("", "", hint)
    delivered = await _notify(bot, target["id"], f"📝 「{me['name']}」在你们的关系线上写了一段：\n{parts[1].strip()}\n\n（发「查看关系线 {me['name']}」看全部）",
                              buttons=[Btn("回一段", f"拉线 {me['name']} ", enter=False),          # 填进输入框，接着写
                                       Btn("看全部", f"查看关系线 {me['name']}", style="secondary")])
    head = (new_line + "\n") if new_line else ""
    if delivered:
        await add_rel.finish(f"{head}✅ 已经发给「{target['name']}」了。\n{stat}")
    await add_rel.finish(f"{head}✅ 记下了。不过「{target['name']}」还没有能收消息的个人群，TA 暂时收不到提醒。\n{stat}")


confirm_rel = _cmd("确认关系线")


@confirm_rel.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if why := relations.closed_note():
        await confirm_rel.finish("🔒 " + why)
    parts = _name_args(args)
    target = roles.get_role_by_name(parts[0]) if parts else None
    if not me or not target or not relations.confirm(me["id"], target["id"]):
        await confirm_rel.finish(f"你和「{parts[0]}」之间还没有关系线。" if parts else "用法：确认关系线 对方名")
    await confirm_rel.finish(f"✅ 你和「{target['name']}」的关系线标成完成了。")


withdraw_rel = _cmd("撤回关系")


@withdraw_rel.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if why := relations.closed_note():
        await withdraw_rel.finish("🔒 " + why)
    parts = args.extract_plain_text().strip().split(None, 1)
    if not me or len(parts) != 2:
        await withdraw_rel.finish("格式：撤回关系 对方角色名 要撤回的内容")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await withdraw_rel.finish(f"❌ 找不到叫「{parts[0]}」的角色。")
    res = relations.withdraw(me["id"], target["id"], parts[1].strip())
    if res == "":
        await withdraw_rel.finish(f"你还没给「{target['name']}」写过关系线，没有能撤回的。")
    if res is None:
        await withdraw_rel.finish(f"没找到你写过的这段：「{parts[1].strip()}」\n发「查看关系线 {target['name']}」看全部。")
    await _notify(bot, target["id"], f"🗑️ 「{me['name']}」撤回了关系线上的一段：\n\"{res}\"")
    await withdraw_rel.finish(f"✅ 撤回了这段：\n\"{res}\"")


view_rel = _cmd("查看关系线")


@view_rel.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await view_rel.finish("你还没有角色。在你的个人群里发「创建新角色 名字」就能创建。")
    parts = _name_args(args)
    if parts:
        target = roles.get_role_by_name(parts[0])
        rel = relations.get_rel(me["id"], target["id"]) if target else None
        if not rel:
            await view_rel.finish(f"你和「{parts[0]}」之间还没有关系线。")
        ds = relations.details(rel["id"])
        if not ds:
            await view_rel.finish(f"你和「{target['name']}」的关系线还没写内容。")
        text = f"📖 你和「{target['name']}」的关系线（{len(ds)} 段）\n" + "\n".join(f"\n【{d['from_name']}】\n{d['text']}" for d in ds)
        await reply_long(view_rel, text)
        # 细节后面跟一条挂按钮：「接着写」把「拉线 名字 」填进输入框；截止锁上了就只给回列表
        btns = [] if relations.closed_note() else [Btn("接着写", f"拉线 {target['name']} ", enter=False)]
        btns.append(Btn("回列表", "查看关系线", style="secondary"))
        await reply_buttons(view_rel, "接下来", btns, per_row=2, fallback=False)
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
    await reply_buttons(view_rel, reply, [Btn(charprofile.btn_name_of(it["other_name"]), f"查看关系线 {it['other_name']}") for it in items], per_row=3)
    await view_rel.finish()


rel_stats = _cmd("关系线统计")


@rel_stats.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await rel_stats.finish(_admin_only("关系线统计"))
    rows = relations.stats()
    if not rows:
        await rel_stats.finish("📭 现在还没有人拉过关系线")
    await rel_stats.finish("📊 关系线数量\n" + "\n".join(f"{r['name']}：{r['n']}" for r in rows[:50]))


# ---------- 往「我的待回」里登记 ----------
from changri.core import pending  # noqa: E402


def _pending_rels(role) -> list[str]:
    if not relations.enabled():
        return []
    from changri.core.overview import fmt_wait
    waits = {x["rel_id"]: x for x in relations.waiting_lines()}
    out = []
    for r in relations.list_for(role["id"]):
        if r["pending"]:
            w = waits.get(r["rel_id"])
            since = f"，已经等了 {fmt_wait(w['hours'] * 3600)}" if w else ""
            out.append((f"rel:{r['rel_id']}:{r['count']}", f"🔗 关系线：「{r['other_name']}」回了你，轮到你回{since}（查看关系线 {r['other_name']}）"))
    return out


pending.register("关系线", _pending_rels)


# ================= 公知 / 二表 =================

import io  # noqa: E402

from nonebot.adapters.qq import MessageSegment  # noqa: E402

from changri.core.qqsend import download_images  # noqa: E402
from changri.core import sheets  # noqa: E402


PAGE_STEP = 4200          # 一张图最高多少（再高 QQ 里看不清）
PAGE_SLACK = 600          # 最后一段只多出这么点就不再单切一张
MAX_SENDS = 5             # 被动回复同一条消息最多 5 次


def _render_one(sh: dict, kind: str) -> bytes:
    import time as _t
    from changri.core.render import sheet_image
    imgs = [b for b in (sheets.image_bytes(n) for n in sh["images"]) if b]
    label = sheets.KINDS[kind]
    return sheet_image.render(f"{sh['name']} 的{label}", f"更新于 {_t.strftime('%m-%d %H:%M', _t.localtime(sh['updated_ts']))}",
                              sh["text"], [b[0] for b in imgs])


PER_PAGE = MAX_SENDS - 1   # 一页发 4 张图，第 5 次留给「发『查看公知 第2页』看下一页」


def _pages(many: list[dict], kind: str, skip: int = 0, count: int = PER_PAGE) -> tuple[list[bytes], bool]:
    """把一串人的表排成一张张图（每张不超过一屏高）：人短就几个人拼一张，一个人太长就切成几张。
    只返回第 skip+1 到 skip+count 张，和「后面还有没有」。前面的照样要画一遍才知道怎么排，但画完就丢，不留在内存里；
    要的这几张凑齐、确认后面还有，就停，不再往后画。
    以前是全员先拼成一整张再切、只发前几段：20 个人填满时内存涨 1.5 GB，后面的人画了也发不出去。
    很慢（每人约 0.2 秒），要在线程里调。"""
    from PIL import Image
    out: list[bytes] = []
    n = 0                                     # 已经排好的张数
    cur: list = []

    def emit(parts: list) -> None:
        nonlocal n
        if skip <= n < skip + count:          # 只有要发的这几张才真的拼、编码
            w = max(i.width for i in parts)
            img = Image.new("RGB", (w, sum(i.height for i in parts)), "white")
            y = 0
            for i in parts:
                img.paste(i, ((w - i.width) // 2, y))
                y += i.height
            buf = io.BytesIO()
            img.save(buf, "PNG")
            out.append(buf.getvalue())
        n += 1

    for x in many:
        if n >= skip + count:                 # 要的都齐了，后面还有人
            return out, True
        im = Image.open(io.BytesIO(_render_one(x, kind))).convert("RGB")
        if im.height > PAGE_STEP + PAGE_SLACK:                 # 一个人就超过一屏：当前这张先出，他单独切几张
            if cur:
                emit(cur)
                cur = []
            tops = list(range(0, im.height, PAGE_STEP))
            for k, top in enumerate(tops):
                if n >= skip + count:
                    return out, True
                emit([im.crop((0, top, im.width, min(im.height, top + PAGE_STEP)))])
            continue
        if cur and sum(i.height for i in cur) + im.height > PAGE_STEP + PAGE_SLACK:
            emit(cur)
            cur = []
            if n >= skip + count:
                return out, True
        cur.append(im)
    if cur:
        if n >= skip + count:
            return out, True
        emit(cur)
    return out, False


async def _show(matcher, sh: dict | list, kind: str, page: int = 1, again: str = "") -> None:
    """sh 是一个人的一份，或者一串（全员）。按页发：一页 4 张图，后面还有就提示发「{again} 第N页」，谁都不省略。
    画图放到线程里，不卡住机器人。"""
    import asyncio
    label = sheets.KINDS[kind]
    many = sh if isinstance(sh, list) else [sh]
    page = max(1, page)
    try:
        parts, more = await asyncio.to_thread(_pages, many, kind, (page - 1) * PER_PAGE, PER_PAGE)
        if not parts:
            await matcher.send(f"没有第 {page} 页了。" + (f"发「{again}」从头看。" if again else ""))
            return
        for part in parts:
            await matcher.send(MessageSegment.file_image(part))
        if more:
            await matcher.send(f"第 {page} 页。还有，发「{again} 第{page + 1}页」接着看。")
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
        if sheets.closed(kind):                   # 过了公知截止时间：不收也不改（管理员在后台能改文字）
            from changri.core import deadlines
            await cmd.finish(f"🔒 公知已于 {deadlines.label('gongzhi')} 截止，不能再提交或修改了。要改请找管理员。")
        text = args.extract_plain_text().strip()
        images = await download_images(args, limit=sheets.IMG_MAX)
        first = sheets.get(me["id"], kind) is None
        ok, info = sheets.submit(me["id"], kind, text, images)
        if not ok:
            await finish_usage(cmd, f"❌ {info}\n用法：提交{label} 你的内容（可以同时发图片）。重复提交会覆盖上一次。", f"提交{label} ")
        if first or kind == "gongzhi":            # 第一次交「已经上交」，公知再交「修改了公知」；二表重交不发
            head = f"📮 {me['name']} 已经上交{label}" if first else f"✏️ {me['name']} 修改了{label}"
            await _tell_group("announce", head)                       # 公告群：只发这一句
            n_img = len(images or [])
            await _tell_group("backstage", head + (f"\n\n{text}" if text else "") +
                                  (f"\n\n（附 {n_img} 张图，发「查看{label} {me['name']}」看）" if n_img else ""))
        extra = ""
        if kind == "gongzhi" and not sheets.public():
            from changri.core import deadlines
            dl = deadlines.label("gongzhi")
            extra = (f"；公知 {dl} 截止，截止前可以重复提交修改，截止后自动对所有人展示" if dl
                     else "；别人要等管理员开启「展示公知」才能看")
        await cmd.finish(f"✅ 「{me['name']}」的{label}已提交（{info}）。发「查看{label}」可以看到做成的长图{extra}。")
    return cmd


def _make_view(kind: str):
    label = sheets.KINDS[kind]
    cmd = _cmd("查看" + label)

    @cmd.handle()
    async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
        import re
        me = _me(event)
        name = args.extract_plain_text().strip()
        page = 1
        if m := re.fullmatch(r"(.*?)\s*第\s*(\d{1,3})\s*页", name):     # 「查看公知 第2页」「查看公知 名字 第2页」：翻页
            name, page = m.group(1).strip(), int(m.group(2))
        again = f"查看{label}" + (f" {name}" if name else "")
        is_admin = admins.is_admin(_bid(), _sender(event)) if hasattr(admins, "is_admin") else False
        if kind == "gongzhi" and not name and (is_admin or sheets.public()):
            everyone = sheets.all_of(kind)       # 不带名字：看所有人的；带名字：只看这一个人
            if not everyone:
                await cmd.finish("还没有人提交公知。发「提交公知 内容」就能提交。")
            players = [x for x in everyone if not x.get("is_npc")]
            npcs = [x for x in everyone if x.get("is_npc")]
            everyone = players + npcs            # 长图也是玩家在前、NPC 在后
            if len(everyone) > 1 and page == 1:  # 能发按钮：列名字，点谁看谁（带他自己的图），不再拼一整张长图；玩家、NPC 分两条发
                groups = [(t, g) for t, g in (("玩家", players), ("NPC", npcs)) if g]
                sent = False
                for title, group in groups:
                    ok = await reply_buttons(
                        cmd, f"📜 公知 · {title}\n{len(group)} 位，点下面的名字查看（或发「查看公知 名字」）",
                        [Btn(charprofile.btn_name_of(x["name"]), f"查看公知 {x['name']}") for x in group], per_row=3, fallback=False)
                    if not ok:
                        break
                    sent = True
                if sent:
                    await cmd.finish()
            await _show(cmd, everyone, kind, page, again)
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
        await _show(cmd, sh, kind, page, again)
        await cmd.finish()
    return cmd


for _k in sheets.KINDS:
    _make_submit(_k)
    _make_view(_k)
