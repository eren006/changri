"""NPC：角色表上的一个标记。NPC 不参与出场抽签、漂流瓶、混乱短信的随机对象、群体闹钟、季末报告；
可以不带群（后台「创建 NPC」建出来的 NPC 没有个人群，也不占群池）。"""
import time

from .db import get_conn
from .roles import NAME_MAX, _valid_name


def is_npc(role_id: int) -> bool:
    with get_conn() as c:
        r = c.execute("SELECT is_npc FROM roles WHERE id=?", (role_id,)).fetchone()
    return bool(r and r["is_npc"])


def names() -> list[str]:
    with get_conn() as c:
        return [r["name"] for r in c.execute("SELECT name FROM roles WHERE is_npc=1 ORDER BY id")]


def listing() -> list[dict]:
    """NPC 一览：名字、有没有个人群。"""
    with get_conn() as c:
        rows = c.execute("SELECT r.id, r.name, (SELECT COUNT(*) FROM role_groups g WHERE g.role_id=r.id) AS groups "
                         "FROM roles r WHERE r.is_npc=1 ORDER BY r.id").fetchall()
    from . import charprofile
    out = []
    for r in rows:
        d = dict(r)
        d["profile"] = "　".join(charprofile.summary_lines(r))
        out.append(d)
    return out


def set_flag(name: str, on: bool) -> tuple[bool, str]:
    with get_conn() as c:
        r = c.execute("SELECT id, is_npc FROM roles WHERE name=?", (name,)).fetchone()
        if not r:
            return False, f"找不到角色「{name}」。"
        if bool(r["is_npc"]) == on:
            return False, f"「{name}」本来就{'是' if on else '不是'} NPC。"
        c.execute("UPDATE roles SET is_npc=? WHERE id=?", (1 if on else 0, r["id"]))
    return True, f"「{name}」{'已设为 NPC' if on else '已改回普通角色'}。"


def create(name: str) -> tuple[bool, str]:
    """建一个不带群的 NPC。"""
    name = (name or "").strip()
    if not _valid_name(name):
        return False, f"角色名要在 {NAME_MAX} 字以内，且不能带空格。"
    with get_conn() as c:
        if c.execute("SELECT 1 FROM roles WHERE name=?", (name,)).fetchone() or c.execute("SELECT 1 FROM role_profiles WHERE short=?", (name,)).fetchone():
            return False, f"「{name}」已经有人用了（本名或简称），换一个名字。"
        c.execute("INSERT INTO roles(name, member_openid, created_ts, is_npc) VALUES(?,?,?,1)", (name, "", int(time.time())))
    return True, f"已创建 NPC「{name}」（没有个人群，要进房间时由管理员安排）。"
