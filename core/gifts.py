"""礼品店（管理员在设置平台填的可送礼物清单）和图鉴（我收到过的礼物）。"""
import re

from .db import get_config, get_conn

_SEP = re.compile(r"[、,，\n]+")


def catalog() -> list[str]:
    return [x.strip() for x in _SEP.split(get_config("gift_catalog", "")) if x.strip()]


def only_preset() -> bool:
    return get_config("gift_only_preset", "0") == "1" and bool(catalog())


def received(role_id: int) -> list:
    """我收到过的礼物（已送达的），按礼物名合并：名字、数量、最近一次是谁送的。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.title AS title, COUNT(*) AS n, MAX(m.id) AS last_id FROM messages m "
            "WHERE m.to_role_id=? AND m.kind='gift' AND m.status='sent' AND m.error!='已撤回' GROUP BY m.title ORDER BY last_id DESC",
            (role_id,)).fetchall()
