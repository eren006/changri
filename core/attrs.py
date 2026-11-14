"""角色属性（给跑团检定用）：存在这个系统自己的角色上，不依赖存档站。"""
import re

from .db import get_conn

MAX_ATTRS = 60
MAX_NAME = 12
_PAIR = re.compile(r"([A-Za-z一-鿿_]{1,%d}?)\s*[:：=＝]?\s*(\d{1,3})(?!\d)" % MAX_NAME)
_FULL = str.maketrans("０１２３４５６７８９：＝", "0123456789:=")


def norm(name: str) -> str:
    """属性名统一：理智 = SAN，英文不分大小写。"""
    n = name.strip()
    if n in ("理智", "san", "San"):
        return "SAN"
    return n.upper() if n.isascii() else n


def parse_pairs(text: str) -> list[tuple[str, int]]:
    """「力量60 敏捷 70 SAN:55」→ [("力量",60),("敏捷",70),("SAN",55)]；同名取最后一个。"""
    out: dict[str, int] = {}
    for m in _PAIR.finditer(text.translate(_FULL)):
        out[norm(m.group(1))] = int(m.group(2))
    return list(out.items())


def set_attrs(role_id: int, pairs: list[tuple[str, int]]) -> tuple[bool, str]:
    with get_conn() as conn:
        have = {r["name"] for r in conn.execute("SELECT name FROM role_attrs WHERE role_id=?", (role_id,))}
        if len(have | {n for n, _ in pairs}) > MAX_ATTRS:
            return False, f"属性最多 {MAX_ATTRS} 项。"
        for name, value in pairs:
            bounds = conn.execute("SELECT min_value,max_value FROM rpg_attr_defs WHERE name=?", (name,)).fetchone()
            if bounds:
                if bounds["min_value"] is not None:
                    value = max(value, bounds["min_value"])
                if bounds["max_value"] is not None:
                    value = min(value, bounds["max_value"])
            conn.execute("INSERT INTO role_attrs(role_id,name,value) VALUES (?,?,?) "
                         "ON CONFLICT(role_id,name) DO UPDATE SET value=excluded.value", (role_id, name, value))
    return True, ""


def get_attr(role_id: int, name: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM role_attrs WHERE role_id=? AND name=?", (role_id, norm(name))).fetchone()
    return row["value"] if row else None


def list_attrs(role_id: int) -> list[tuple[str, int]]:
    with get_conn() as conn:
        return [(r["name"], r["value"]) for r in conn.execute("SELECT name,value FROM role_attrs WHERE role_id=? ORDER BY rowid", (role_id,))]


def delete_attr(role_id: int, name: str) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM role_attrs WHERE role_id=? AND name=?", (role_id, norm(name))).rowcount > 0


def clear_attrs(role_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM role_attrs WHERE role_id=?", (role_id,)).rowcount
