"""消息监听：后台「消息监听」页实时滚动看机器人收到的群消息（像海豹的日志），出状况时方便看一眼。

两路来源：
- 官方机器人：它在的群（玩家个人群、公告群、房间群……）里收到的消息；
- QQ 小号：绑定的 QQ 号在群号组（备用群 / 房间群）里听到的消息。
只放在内存里（每个机器人最近 KEEP 条），不进数据库，重启就清空。设置里关掉「消息监听」就不记。
"""
from __future__ import annotations

import collections
import itertools
import threading
import time

from .db import current_bot, get_config

KEEP = 500
_buf: dict[str, collections.deque] = {}
_seq = itertools.count(1)
_lock = threading.Lock()


def enabled() -> bool:
    return get_config("msgmon_enabled", "1") == "1"


def record(src: str, group: str, who: str, text: str, ts: int | None = None) -> None:
    """src：official（官方机器人）/ qq（QQ 小号）。group、who 是给人看的描述。"""
    if not enabled():
        return
    item = {"id": next(_seq), "ts": int(ts or time.time()), "src": src, "group": group, "who": who, "text": (text or "")[:500]}
    with _lock:
        _buf.setdefault(current_bot(), collections.deque(maxlen=KEEP)).append(item)


def since(after: int = 0, limit: int = 200) -> list[dict]:
    with _lock:
        items = [x for x in _buf.get(current_bot(), ()) if x["id"] > after]
    return items[-limit:]


def clear() -> None:
    with _lock:
        _buf.pop(current_bot(), None)


# ---------- 给人看的群名、人名 ----------

def official_group_label(bot_id: str, group_openid: str) -> str:
    from .db import get_conn
    with get_conn() as c:
        r = c.execute("SELECT r.name FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.group_openid=?",
                      (bot_id, group_openid)).fetchone()
        if r:
            return f"{r['name']} 的个人群"
        p = c.execute("SELECT * FROM pool_groups WHERE bot_id=? AND group_openid=?", (bot_id, group_openid)).fetchone()
    key = f"{bot_id}|{group_openid}"
    for k, label in (("announce_group", "公告群"), ("backstage_group", "后台群"), ("water_group", "水群"), ("stage_group", "戏群")):
        if get_config(k, "") == key:
            return label
    if p and p["in_pool"]:
        return _pool_label(p)
    if p and (p["real_gid"] or p["label"]):
        return f"群 {p['label'] or p['real_gid']}"
    return f"群 {group_openid[:8]}…"


def _pool_label(p) -> str:
    from . import rooms
    tag = p["label"] or p["real_gid"] or f"#{p['id']}"
    r = rooms.get(p["room_id"]) if p["room_id"] else None
    return f"房间群 {tag}（R{r['id']} {r['kind']}）" if r and r["status"] == "open" else f"备用群 {tag}"


def qq_group_label(gid: str) -> str:
    from .db import get_conn
    with get_conn() as c:
        p = c.execute("SELECT * FROM pool_groups WHERE real_gid=?", (gid,)).fetchone()
        q = c.execute("SELECT note FROM qq_groups WHERE gid=?", (gid,)).fetchone()
    base = _pool_label(p) if p else f"群 {gid}"
    note = q["note"] if q and q["note"] else ""
    return f"{base}（{note}）" if note and note not in base else base


def qq_sender_label(qq: str, nickname: str) -> str:
    from . import roles
    names = [r["name"] for r in roles.roles_by_qq(qq)]
    who = "/".join(names) if names else (nickname or "")
    return f"{who}（QQ {qq}）" if who else f"QQ {qq}"
