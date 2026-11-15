"""礼品店（管理员在设置平台填的可送礼物清单）和图鉴（我收到过的礼物）。"""
import re
import time

from .db import get_config, get_conn, set_config

_SEP = re.compile(r"[、,，\n]+")


def enabled() -> bool:
    from . import extensions
    return extensions.is_enabled("giftshop")


def _seed() -> None:
    """升级前礼品店是设置里一行字（顿号分开）；第一次用时搬进表里。"""
    if get_config("gift_shop_seeded", "0") == "1":
        return
    names = [x.strip() for x in _SEP.split(get_config("gift_catalog", "")) if x.strip()]
    if not names:
        return                  # 没有老数据：不打标记，以后设置里真有老清单时还能搬
    with get_conn() as c:
        for n in names:
            c.execute("INSERT OR IGNORE INTO gift_shop(name, created_ts) VALUES(?,?)", (n[:30], int(time.time())))
    set_config("gift_shop_seeded", "1")


def items() -> list[dict]:
    """礼品店全部礼物：{id(编号), name, note}，按编号排。"""
    _seed()
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT id, name, note FROM gift_shop ORDER BY id")]


def catalog() -> list[str]:
    return [x["name"] for x in items()] if enabled() else []


def only_preset() -> bool:
    return enabled() and get_config("gift_only_preset", "0") == "1" and bool(items())


def add(name: str, note: str = "") -> tuple[bool, str]:
    name = (name or "").strip()
    if not name or len(name) > 30:
        return False, "礼物名要写，30 字以内。"
    if name.isdigit():
        return False, "礼物名不能是纯数字（数字是编号）。"
    _seed()
    with get_conn() as c:
        if c.execute("SELECT 1 FROM gift_shop WHERE name=?", (name,)).fetchone():
            return False, f"「{name}」已经在礼品店里了。"
        i = c.execute("INSERT INTO gift_shop(name, note, created_ts) VALUES(?,?,?)", (name, (note or "").strip()[:100], int(time.time()))).lastrowid
    return True, f"已上架：{i}. {name}"


def remove(item_id: int) -> bool:
    _seed()
    with get_conn() as c:
        return c.execute("DELETE FROM gift_shop WHERE id=?", (item_id,)).rowcount > 0


def clear() -> int:
    _seed()
    with get_conn() as c:
        return c.execute("DELETE FROM gift_shop").rowcount


def export_text() -> str:
    """每行「编号｜名字｜说明」，能原样粘回导入框。"""
    return "\n".join(f"{x['id']}｜{x['name']}｜{x['note']}" for x in items())


def import_text(text: str) -> tuple[int, list[str]]:
    """一行一件：「名字」或「名字｜说明」（带编号的导出格式「1｜名字｜说明」也认，编号忽略）。已有的跳过。返回 (新增几件, 跳过的说明)。"""
    n, skipped = 0, []
    for raw in text.splitlines():
        parts = [x.strip() for x in re.split(r"[｜|]", raw.strip())]
        if parts and parts[0].isdigit() and len(parts) >= 2:
            parts = parts[1:]
        if not parts or not parts[0]:
            continue
        ok, msg = add(parts[0], parts[1] if len(parts) > 1 else "")
        n += ok
        if not ok:
            skipped.append(msg)
    return n, skipped


def resolve(text: str, by_name_ok: bool = False) -> tuple[str, str]:
    """玩家写的礼物 → (最终礼物名, 错误)。礼品店开着时，写编号就换成对应的名字；
    「只能送礼品店里的东西」开着时，只认编号（不能写自定义的）；手机里点选出来的名字用 by_name_ok=True。"""
    text = (text or "").strip()
    if not enabled():
        return text, ""
    its = items()
    if text.isdigit():
        hit = next((x for x in its if x["id"] == int(text)), None)
        if hit:
            return hit["name"], ""
        if only_preset():
            return "", f"礼品店里没有 {text} 号，发「礼品店」看编号。"
        return text, ""
    if only_preset() and not (by_name_ok and text in [x["name"] for x in its]):
        return "", "现在只能送礼品店里的礼物，要写编号，发「礼品店」看编号。"
    return text, ""


def received(role_id: int) -> list:
    """我收到过的礼物（已送达的），按礼物名合并：名字、数量、最近一次是谁送的。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.title AS title, COUNT(*) AS n, MAX(m.id) AS last_id FROM messages m "
            "WHERE m.to_role_id=? AND m.kind='gift' AND m.status='sent' AND m.error!='已撤回' GROUP BY m.title ORDER BY last_id DESC",
            (role_id,)).fetchall()
