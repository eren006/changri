"""公用的：常量、找角色、往个人群 / 房间群推消息、邀请文案。"""

import re

import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent

from changri.core import admins, messaging, pool, qqmanager, qqpool, roles, rooms
from changri.core.qqsend import make_send, send_to_group


NEED_ROLE = "你还没有角色。在你的个人群里发「创建新角色 名字」就能创建。"
NOT_ADMIN = "❌ 这条指令只有管理员能用。"
SEP = re.compile(r"[/、，,]")


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _me(bot: Bot, event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


def _is_admin(bot: Bot, event: GroupMessageCreateEvent) -> bool:
    return admins.is_admin(bot.self_id, event.author.member_openid)


def _names(text: str) -> list[str]:
    return [x.strip() for x in SEP.split(text) if x.strip()]


def _resolve(names: list[str]) -> tuple[list, str]:
    found, missing = [], []
    for n in names:
        r = roles.get_role_by_name(n)
        (found if r else missing).append(r or n)
    return found, ("、".join(missing) if missing else "")


async def _push(role_id: int, text: str, at: bool = True, buttons=None) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, make_send(), at, buttons)     # 发到角色群时默认自动 @ 本人（见 messaging.push_to_role_ex）
    return ok


def _qq_hint(role_id: int, room) -> str:
    """房间分到的是 QQ 小号管的群（卡拉不在里面，靠绑定的 QQ 认人），这个人又还没绑 QQ：提醒一句，不然他在群里说话不算到场、发指令也认不出。"""
    if not room or not room["pool_id"]:
        return ""
    g = pool.get(room["pool_id"])
    if not g or not pool.is_qq_only(g) or roles.qqs_of_role(role_id):
        return ""
    return "\n⚠️ 你还没绑 QQ：这个房间群里认人靠 QQ 号，不绑的话说话不算到场、群里发指令也认不出你。先在个人群发「绑定QQ 你的QQ号」。"


async def _push_group(role_id: int, room) -> bool:
    """分配到群号的通知拆成两条里的第二条：不 @、纯文字，只写请入群 + 群号（方便直接复制）。"""
    return await _push(role_id, _group_line(room), at=False)


async def _to_group(bot_id: str, group_openid: str, text: str) -> bool:
    """往房间群里说一句。返回是否发出去了。"""
    if group_openid.startswith(pool.QQ_PREFIX):          # 只有群号的房间群：卡拉不在里面，由监听的 QQ 号在群里说
        err = await qqmanager.room_say(bot_id, group_openid[len(pool.QQ_PREFIX):], text, [])
        if err:
            logger.warning(f"[房间] 群里发话失败：{err}")
        return not err
    bot = nonebot.get_bots().get(bot_id)
    if bot is None:
        return False
    try:
        await send_to_group(bot, group_openid, text)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 发群失败：{e}")
        return False


from changri.core.overview import group_line as _group_line, slot_text as _slot  # noqa: E402


RP_FORMAT_TIP = "✍️ 写戏的格式：先写角色名，再写正文，中间用冒号、空格或换行隔开都行。不按这个格式写的算闲聊，不进存档，也不算字数"


def _room_text(room, who_from: str = "") -> str:
    """通知的第一条：@ 本人 + 类型、时间地点、参与者 + 须知（跟长日系统一样）。群号另发第二条（_push_group）。"""
    mem = "、".join(m["name"] for m in rooms.members(room["id"]))
    head = f"💌 {who_from} 向你发起了{room['kind']}" if who_from else f"💌 {room['kind']}"
    tips = [RP_FORMAT_TIP]
    gi = rooms.group_info(room)
    if gi and not gi[1].startswith(pool.QQ_PREFIX):                     # 官方机器人的群：它查不了群成员，要说过话才算到场
        tips.append("到场 ➜ 进群后随便说一句，才算到场")
    if rooms.is_official(room):                                          # 官约 / 官电：玩家改不了时间，也不能拒（rooms.decline_check）
        tips.append("时间由管理员安排，来不了请联系管理员")
    elif room["kind"] in rooms.NO_DECLINE_KINDS:                         # 心愿这类也不能拒，只写改时间
        tips.append(f"修改时间 ➜ 修改时间线 R{room['id']} D{room['day']} 新时间（个人群里也能发）")
    elif room["kind"] not in rooms.NO_TIMELINE:
        tips += [f"修改时间 ➜ 修改时间线 R{room['id']} D{room['day']} 新时间（个人群里也能发）", f"不想参加 ➜ 拒绝约会 R{room['id']}（只能在开始写戏之前；整场取消，所有人的时间线都拿掉）"]
    tips.append(f"结束互动 ➜ 在约会群里直接发「结束{room['kind']}」")
    return f"{head}\n{_slot(room)}\n参与者：{mem}\n房间号 R{room['id']}\n\n" + "\n".join(tips)


async def _invite(role_id: int, room, who_from: str = "", fix=None) -> tuple[bool, str]:
    """发邀请：第一条 @ + 时间地点人物 + 须知，第二条纯文字「请入群：群号」。fix 可对第一条做替换（官约、官电的标题）。
    返回第一条（邀请正文）有没有发到、没发到的原因。"""
    text = _room_text(room, who_from)
    if fix:
        text = text.replace(*fix)
    ok, info = await messaging.push_to_role(role_id, text + _qq_hint(role_id, room), make_send(), True)
    await _push_group(role_id, room)
    return ok, info


def _why_missed(info: str) -> str:
    """推送没发到的原因，说成管理员看得懂的话。"""
    if "没有可用的群" in info:
        return "TA 还没有认个人群"
    if "不在线" in info:
        return "机器人当时不在线"
    return "可能是 TA 的个人群没开主动发言"


def _missed_line(missed: list[tuple[str, str]]) -> str:
    """[(名字, 原因)] → 「⚠️ 没发到：乙、丙（可能是 TA 的个人群没开主动发言）」，同一个原因的合在一起。没有返回空串。"""
    if not missed:
        return ""
    by: dict[str, list[str]] = {}
    for name, info in missed:
        by.setdefault(_why_missed(info), []).append(name)
    return "⚠️ 没发到：" + "；".join(f"{'、'.join(ns)}（{why}）" for why, ns in by.items()) + "。请私下告诉 TA；没开主动发言的，请 TA 在个人群设置里打开「机器人主动在群聊内发言」。"


async def _announce_in_room(room_id: int) -> None:
    """房间领到群后，在那个群里发一条说明。"""
    r = rooms.get(room_id)
    gi = rooms.group_info(r) if r else None
    if not gi:
        return
    mem = "、".join(m["name"] for m in rooms.members(room_id))
    if gi[1].startswith(pool.QQ_PREFIX):                                 # 房间群：只改群名，不发言
        await qqmanager.room_rename(gi[0], pool.get(r["pool_id"]), qqpool.room_name(r, [m["name"] for m in rooms.members(room_id)]))
        return
    await _to_group(gi[0], gi[1], f"📌 R{room_id} {r['kind']}\n{_slot(r)}\n参与者：{mem}\n参与者请进群后在这里聊；聊完发「结束{r['kind']}」。群里没有的人，进群后随便说一句就算到场。")
