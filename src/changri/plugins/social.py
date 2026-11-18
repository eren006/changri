"""第 1 步：角色、群绑定、短信、礼物（QQ 指令版）。

规则和数据都在 core/ 里，这里只负责解析指令、回话、调用发送。
官方机器人读不到真实群号，群靠「认群码」绑定（见 core/roles.py）。
"""
import nonebot
from nonebot import logger, on_command, on_message, on_type
from nonebot.adapters.qq import Bot, GroupAddRobotEvent, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg
from nonebot.typing import T_State

from changri.core.qqsend import download_images, make_send, recall_group_message, reply_buttons, reply_long, send_to_group  # noqa: E402
from changri.core import admins, blocks, charprofile, formats, forms, gifts, messaging, msgimages, roles, season, sendmsg, windows
from changri.core.buttons import Btn
from changri.core.db import get_config, get_conn, get_int, per_bot

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


def _bid() -> str:
    """当前处理这条消息的机器人 ID。一个平台可以接多个机器人，openid 是每个机器人各一套，所以认人、绑群都要带上它。"""
    return current_bot.get().self_id


def _sender(event: GroupMessageCreateEvent) -> str:
    return event.author.member_openid


def _sender_role(event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(_bid(), event.group_openid, _sender(event))


def _make_send() -> messaging.Send:
    """发消息时按群所属的机器人选发送者（开头的 {{AT:…}} 由 make_send 处理，不处理就会原样显示成一串码）。"""
    return make_send()


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


# ---------- 角色 ----------

create_role = _cmd("创建新角色")


@create_role.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    parts = args.extract_plain_text().split()
    if len(parts) not in (1, 2):
        await create_role.finish("用法：创建新角色 名字 [群号]\n群号可填可不填，只是给管理员看的标签。")
    if not season.running():           # 没有进行中的季度（筹备期也算进行中）就不能建角色
        await create_role.finish("现在没有进行中的季度，暂时不能创建新角色。等管理员开了季度（筹备期就可以）再来。")
    ok, msg = roles.create_role(parts[0], _bid(), event.group_openid, _sender(event), parts[1] if len(parts) == 2 else "")
    await create_role.finish(formats.welcome(roles.get_role_by_name(parts[0])) if ok else msg)


def _make_profile_cmd(prefix: str):
    """修改性别 / 年龄 / 皮相 / 签名 / 简称：五个指令共用。一条消息可以写多行一起改（每行一条）。"""
    m = _cmd(prefix)

    @m.handle()
    async def _(event: GroupMessageCreateEvent, args=CommandArg()):
        role = _sender_role(event)
        if not role:
            await m.finish("你还没有角色。在你的个人群里发「创建新角色 名字」。")
        await m.finish(charprofile.edit(role, prefix + " " + args.extract_plain_text().strip()))
    return m


for _p in charprofile.PREFIXES:
    _make_profile_cmd(_p)


look_wall = _cmd("查看皮相墙", {"皮相墙"})


@look_wall.handle()
async def _():
    await reply_long(look_wall, charprofile.look_wall())


roster_cmd = _cmd("玩家名单", {"角色名单"})


@roster_cmd.handle()
async def _():
    await reply_long(roster_cmd, charprofile.roster())


my_role = _cmd("我的角色", {"我的群"})


@my_role.handle()
async def _(event: GroupMessageCreateEvent):
    role = _sender_role(event)
    if not role:
        await my_role.finish("你还没有角色。在你的个人群里发「创建新角色 名字」。")
    lines = [f"角色：{role['name']}", *charprofile.summary_lines(role)]
    for i, g in enumerate(roles.list_groups(role["id"]), 1):
        kind = "个人群"
        tag = f"群号 {g['real_gid']}" if g["real_gid"] else "未登记群号"
        state = "" if g["group_openid"] else "（还没认群）"
        lines.append(f"{i}. {kind} · {tag}{state}")
    await my_role.finish("\n".join(lines))


mine = _cmd("我的")


@mine.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    """「我的」：角色卡、时间线、待回、数量……集中成一排按钮。「我的xxx」里没有单独注册的（打错的）不理，免得刷屏。"""
    if args.extract_plain_text().strip():
        await mine.finish()
    role = _sender_role(event)
    if not role:
        await mine.finish("你还没有角色。在你的个人群里发「创建新角色 名字」。")
    items = formats.mine()
    await reply_buttons(mine, f"「{role['name']}」\n点下面的按钮查看（也可以直接发：" + "、".join(c for _l, c in items) + "）",
                        [Btn(label, cmd) for label, cmd in items], per_row=4)
    await mine.finish()


change_code = _cmd("换绑码")


@change_code.handle()
async def _(event: GroupMessageCreateEvent):
    role = _sender_role(event)
    if not role:
        await change_code.finish("你还没有角色。")
    code = roles.make_player_code(role["id"])
    await change_code.finish(f"换绑码：{code}（10 分钟内有效）\n把机器人拉进新群，在新群里发「认群 {code}」，原来的个人群会被换掉。")


claim = _cmd("认群")


@claim.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    code = args.extract_plain_text().strip()
    if not code.isdigit() or len(code) != 6:
        await claim.finish("用法：认群 6位认群码")
    _, msg = roles.consume_bind_code(code, _bid(), event.group_openid, _sender(event))
    await claim.finish(msg)


# ---------- 短信 / 礼物 ----------

KIND_NAME = {"sms": "短信", "gift": "礼物"}


async def _social_rule(bot: Bot, event: GroupMessageCreateEvent, state: T_State) -> bool:
    """认「短信 …」「署名短信 …」「【短信】…」「送礼 …」。带署名前缀的，署名得是自己的角色名（或管理员开放了自定义署名），
    否则多半是聊天里碰巧带了「短信」两个字，不理。"""
    p = forms.match_social(event.get_plaintext())
    if not p:
        return False
    if p["sign"]:
        me = roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)
        if not (me and p["sign"] == me["name"]) and get_config("allow_custom_sign", "0") != "1":
            return False
    state["social"] = p
    return True


social_msg = on_message(rule=_social_rule, priority=9, block=True)


@social_msg.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, state: T_State):
    p = state["social"]
    kind = p["kind"]
    images = []
    if msgimages.enabled():                                  # 带图片：当场下载（QQ 的图片链接会过期）
        images = await download_images(event.get_message(), limit=1, max_bytes=msgimages.max_bytes())
    if p["bad"] and not (kind == "sms" and p["to"] and images):       # 只有图片没有文字的短信（短信 对象）也算
        await social_msg.finish(forms.TEMPLATES["短信" if kind == "sms" else "送礼"])
    me = roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)
    if not me:
        await social_msg.finish("你还没有角色，先在个人群里「创建新角色 名字」。")
    target = roles.get_role_by_name(p["to"])
    if not target:
        await social_msg.finish(f"没有叫「{p['to']}」的角色。")
    if target["id"] == me["id"]:
        await social_msg.finish("不能发给自己。")
    ok, text = await sendmsg.send(me, target, kind, p["gift"], p["content"], p["sign"], _make_send(), images)
    await social_msg.finish(text + ("\n🖼 图片已一起发送。" if images and ok else ""))


recall = _cmd("撤回")


@recall.handle()
async def _(event: GroupMessageCreateEvent):
    me = _sender_role(event)
    if not me:
        await recall.finish("你还没有角色。")
    m = messaging.last_recallable(me["id"])
    if not m:
        await recall.finish("没有可以撤回的：只能撤回自己 2 分钟内发出的最近一条短信/礼物。")
    bot = nonebot.get_bots().get(m["delivered_bot"])
    if bot is None:
        await recall.finish("撤回失败：对方群所在的机器人现在不在线。")
    try:
        await recall_group_message(bot, m["delivered_group"], m["delivered_msg_id"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[撤回] 失败：{e}")
        await recall.finish(f"❌ 撤回失败（可能已超过 2 分钟，或那条消息已经不在了）：{str(e)[:80]}")
    messaging.mark_recalled(m["id"])
    await recall.finish(f"✅ 已撤回发给「{m['to_name']}」的{KIND_NAME[m['kind']]}，今天的次数已返还。")


fmt = _cmd("格式")


@fmt.handle()
async def _(args=CommandArg()):
    """「格式」= 菜单，只列开着的功能；「格式短信」（连写或空一格都行）= 直接给模板。"""
    t = args.extract_plain_text().strip()
    if t:
        st, text = formats.template(t)
        if st == "off":
            await fmt.finish(text)
        if st == "ok":
            f = formats.fill(t)             # 「✏️ 填入输入框」：只把指令开头填进输入框，不直接发出
            await reply_buttons(fmt, text, [Btn("填入输入框", f, enter=False)] if f else [], per_row=1)
            await fmt.finish()
    names = formats.available()
    await reply_buttons(fmt, formats.menu_text(names),
                        [Btn(n, formats.fill(n), enter=False) if formats.fill(n) else Btn(n, "格式" + n, style="secondary") for n in names], per_row=4)
    await fmt.finish()


album = _cmd("我的图鉴", {"图鉴", "我的礼物"})


@album.handle()
async def _(event: GroupMessageCreateEvent):
    me = _sender_role(event)
    if not me:
        await album.finish("你还没有角色。")
    rows = gifts.received(me["id"])
    if not rows:
        await album.finish("图鉴还是空的，还没有人送过你礼物。")
    lines = [f"{r['title']} ×{r['n']}" for r in rows]
    await reply_long(album, f"📖 「{me['name']}」收到过的礼物（共 {sum(r['n'] for r in rows)} 件）：\n" + "\n".join(lines))


# ---------- 拉群引导 ----------
# 官方机器人不能替人拉群：玩家自己建群，把卡拉拉进去，在群里发「认群 码」或「创建新角色 名字」。
# 卡拉被拉进群时，自动发一段欢迎和下一步怎么做。

DEFAULT_WELCOME = (
    "你好，我是{brand}的机器人。\n"
    "· 还没有角色：发「创建新角色 名字」，这个群就成为你的个人群\n"
    "· 管理员或你自己生成了认群码：发「认群 6位码」，把这个群绑给你的角色\n"
    "· 想换个人群：在已绑好的群里发「换绑码」，到新群发「认群 码」\n"
    "· 发「我的群」可以看自己绑了哪些群；发「格式短信」「格式送礼」拿写法模板"
)


@on_type(GroupAddRobotEvent, priority=10, block=False).handle()
async def _(bot: Bot, event: GroupAddRobotEvent):
    text = (get_config("welcome_text", "") or DEFAULT_WELCOME).replace("{brand}", get_config("brand_name", "") or "长日")
    try:
        await bot.send_to_group(group_openid=event.group_openid, message=text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[欢迎] 发欢迎语失败：{e}")


# ---------- 重试 ----------

@scheduler.scheduled_job("interval", seconds=60, id="messaging_retry", max_instances=1, coalesce=True)
@per_bot
async def _retry():
    if not nonebot.get_bots():
        return
    try:
        n = await messaging.retry_pending(_make_send())
        if n:
            logger.info(f"[短信] 重试送达 {n} 条")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[短信] 重试出错：{e}")
