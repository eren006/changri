"""公告群 / 后台群 / 水群：都记成「机器人ID|群openid」，因为群 openid 是每个机器人各一套，发的时候要用对应的机器人。

- 公告群（announce_group）：给玩家看的播报（心动信派送、礼物公开播报、点歌……）。
- 后台群（backstage_group）：给管理组看的提醒（QQ 号掉线等）。没设后台群时这些提醒退回发公告群。
- 水群（water_group）：玩家闲聊的群，记下来避免被当成房间放进群池。
"""
from .botconf import configured_bot_ids
from .db import get_config, set_config

ANNOUNCE, BACKSTAGE, WATER = "announce_group", "backstage_group", "water_group"
LABELS = {ANNOUNCE: "公告群", BACKSTAGE: "后台群", WATER: "水群"}

# 设为公告群 / 后台群 / 水群 / 戏群之后跟在回执后面：这些群要收机器人自己发的消息（不是回复指令），QQ 默认不让发，
# 群里没打开的话日志会是「主动消息失败, 无权限」。
PUSH_HINT = ("\n\n⚠️ 还差一步：请群主在 QQ 里打开「机器人主动在群聊内发言」"
             "（群设置 → 群机器人 → 管理 → 点机器人；和总览页「不用 @ 也能回」那张卡片说的是同一处）。"
             "不打开的话，机器人只能回复指令，播报、提醒这类它自己发的消息会被 QQ 拦下。")


def _get(key: str) -> tuple[str, str] | None:
    raw = get_config(key, "")
    if not raw:
        return None
    if "|" in raw:
        bot_id, gid = raw.split("|", 1)
        return bot_id, gid
    ids = configured_bot_ids()           # 旧格式：只存了群 openid，归到第一个机器人
    return (ids[0], raw) if ids else None


def _set(key: str, bot_id: str, group_openid: str) -> None:
    set_config(key, f"{bot_id}|{group_openid}" if group_openid else "")


def get_announce() -> tuple[str, str] | None:
    return _get(ANNOUNCE)


def set_announce(bot_id: str, group_openid: str) -> None:
    _set(ANNOUNCE, bot_id, group_openid)


def get_backstage() -> tuple[str, str] | None:
    return _get(BACKSTAGE)


def set_backstage(bot_id: str, group_openid: str) -> None:
    _set(BACKSTAGE, bot_id, group_openid)


def get_water() -> tuple[str, str] | None:
    return _get(WATER)


def set_water(bot_id: str, group_openid: str) -> None:
    _set(WATER, bot_id, group_openid)


def get_staff_notice() -> tuple[str, str] | None:
    """给管理组的提醒发到哪：后台群优先，没设就退回公告群。"""
    return get_backstage() or get_announce()


def special_label(bot_id: str, group_openid: str) -> str:
    """这个群是不是公告群 / 后台群 / 水群 / 戏群（是就返回名字，否则空串）：这些群不能再当某个人的个人群。"""
    hit = roles_of(bot_id, group_openid)
    if hit:
        return hit[0]
    return "戏群" if get_config("stage_group", "") == f"{bot_id}|{group_openid}" else ""


def roles_of(bot_id: str, group_openid: str) -> list[str]:
    """这个群现在被设成了哪些（公告群/后台群/水群），用来提示「这个群同时也是…」。"""
    return [LABELS[k] for k in (ANNOUNCE, BACKSTAGE, WATER) if _get(k) == (bot_id, group_openid)]
