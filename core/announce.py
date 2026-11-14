"""公告群：记成「机器人ID|群openid」，因为群 openid 是每个机器人各一套，发的时候要用对应的机器人。"""
from .botconf import configured_bot_ids
from .db import get_config, set_config


def get_announce() -> tuple[str, str] | None:
    raw = get_config("announce_group", "")
    if not raw:
        return None
    if "|" in raw:
        bot_id, gid = raw.split("|", 1)
        return bot_id, gid
    ids = configured_bot_ids()           # 旧格式：只存了群 openid，归到第一个机器人
    return (ids[0], raw) if ids else None


def set_announce(bot_id: str, group_openid: str) -> None:
    set_config("announce_group", f"{bot_id}|{group_openid}" if group_openid else "")
