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

from plugins._long import make_send, recall_group_message, reply_long, send_to_group  # noqa: E402
from core import admins, blocks, forms, gifts, messaging, roles, season, sendmsg, windows
from core.db import get_config, get_conn, get_int, per_bot

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
    await create_role.finish(msg)


my_role = _cmd("我的角色", {"我的群"})


@my_role.handle()
async def _(event: GroupMessageCreateEvent):
    role = _sender_role(event)
    if not role:
        await my_role.finish("你还没有角色。在你的个人群里发「创建新角色 名字」。")
    lines = [f"角色：{role['name']}"]
    for i, g in enumerate(roles.list_groups(role["id"]), 1):
        kind = "主群" if g["kind"] == "main" else "备用群"
        tag = f"群号 {g['real_gid']}" if g["real_gid"] else "未登记群号"
        state = "" if g["group_openid"] else "（还没认群）"
        lines.append(f"{i}. {kind} · {tag}{state}")
    await my_role.finish("\n".join(lines))


backup_code = _cmd("备用群码")


@backup_code.handle()
async def _(event: GroupMessageCreateEvent):
    role = _sender_role(event)
    if not role:
        await backup_code.finish("你还没有角色。")
    code = roles.make_player_code(role["id"], "backup")
    await backup_code.finish(f"备用群认群码：{code}（10 分钟内有效）\n把机器人拉进备用群，在那个群里发「绑定备用群 {code}」。")


main_code = _cmd("主群码")


@main_code.handle()
async def _(event: GroupMessageCreateEvent):
    role = _sender_role(event)
    if not role:
        await main_code.finish("你还没有角色。")
    code = roles.make_player_code(role["id"], "main")
    await main_code.finish(f"换个人群的认群码：{code}（10 分钟内有效）\n在新群里发「换主群 {code}」，原来的个人群会被替换。")


claim = _cmd("认群")


@claim.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    code = args.extract_plain_text().strip()
    if not code.isdigit() or len(code) != 6:
        await claim.finish("用法：认群 6位认群码")
    _, msg = roles.consume_bind_code(code, _bid(), event.group_openid, _sender(event))
    await claim.finish(msg)


def _kind_claim(name: str, kind: str, usage: str):
    cmd = _cmd(name)

    @cmd.handle()
    async def _(event: GroupMessageCreateEvent, args=CommandArg()):
        code = args.extract_plain_text().strip()
        if not code.isdigit() or len(code) != 6:
            await cmd.finish(usage)
        _, msg = roles.consume_bind_code(code, _bid(), event.group_openid, _sender(event), kind)
        await cmd.finish(msg)


_kind_claim("绑定备用群", "backup", "用法：绑定备用群 6位认群码\n码在你已绑定的群里发「备用群码」生成，或由管理员在后台登记群号后给你。")
_kind_claim("换主群", "main", "用法：换主群 6位认群码\n码在你已绑定的群里发「主群码」生成。")

unbind = _cmd("解绑备用群")


@unbind.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    role = _sender_role(event)
    text = args.extract_plain_text().strip()
    if not role or not text.isdigit():
        await unbind.finish("用法：解绑备用群 序号（序号看「我的群」）")
    _, msg = roles.unbind_backup(role["id"], int(text))
    await unbind.finish(msg)


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
    if p["bad"]:
        await social_msg.finish(forms.TEMPLATES["短信" if kind == "sms" else "送礼"])
    me = roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)
    if not me:
        await social_msg.finish("你还没有角色，先在个人群里「创建新角色 名字」。")
    target = roles.get_role_by_name(p["to"])
    if not target:
        await social_msg.finish(f"没有叫「{p['to']}」的角色。")
    if target["id"] == me["id"]:
        await social_msg.finish("不能发给自己。")
    ok, text = await sendmsg.send(me, target, kind, p["gift"], p["content"], p["sign"], _make_send())
    await social_msg.finish(text)


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
    t = args.extract_plain_text().strip()
    t = {"送礼物": "送礼"}.get(t, t)
    if t in forms.TEMPLATES:
        await fmt.finish(forms.TEMPLATES[t])
    await fmt.finish("输入「格式短信」「格式送礼」「格式撤回」拿可以复制的模板。")


for _k in ("短信", "送礼", "撤回"):
    def _mk(key):
        c = _cmd("格式" + key)

        @c.handle()
        async def _():
            await c.finish(forms.TEMPLATES[key])
    _mk(_k)


shop = _cmd("礼品店")


@shop.handle()
async def _():
    items = gifts.catalog()
    if not items:
        await shop.finish("礼品店还没有上架礼物，送礼可以随便写礼物名。")
    tip = "只能送这里的礼物。" if gifts.only_preset() else "也可以送别的，随便写。"
    await shop.finish("🎁 礼品店：\n" + "、".join(items) + f"\n\n送礼 对象 礼物 [留言]。{tip}")


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
    "· 想绑备用群：在已绑好的群里发「备用群码」，到新群发「绑定备用群 码」\n"
    "· 发「我的群」可以看自己绑了哪些群；发「格式短信」「格式送礼」拿写法模板"
)


@on_type(GroupAddRobotEvent, priority=10, block=False).handle()
async def _(bot: Bot, event: GroupAddRobotEvent):
    text = (get_config("welcome_text", "") or DEFAULT_WELCOME).replace("{brand}", get_config("brand_name", "") or "长日")
    try:
        await bot.send_to_group(group_openid=event.group_openid, message=text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[欢迎] 发欢迎语失败：{e}")


unclaimed = _cmd("查看未认群")


@unclaimed.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await unclaimed.finish("❌ 权限不足，仅管理员可用。")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT r.name, g.kind, g.real_gid FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.group_openid IS NULL ORDER BY r.id").fetchall()
        n_roles = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
    if not rows:
        await unclaimed.finish(f"所有群位都已认群（共 {n_roles} 个角色）。")
    lines = [f"{r['name']}（{'主群' if r['kind'] == 'main' else '备用群'}，登记群号 {r['real_gid'] or '未填'}）" for r in rows]
    await reply_long(unclaimed, f"还没认群的群位（{len(rows)} 个）：\n" + "\n".join(lines) + "\n\n在设置平台「角色与群」给对应角色生成认群码，发给 TA 在新群里「认群 码」。")


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
