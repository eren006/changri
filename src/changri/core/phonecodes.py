"""小手机激活码：每个角色一个，玩家在个人群里发「手机码」领取，也可以在设置平台重发/重置。
码只认角色，重置后旧码立刻失效；角色被清掉（收尾）时码跟着删。"""
import secrets
import time

from .db import get_conn

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # 去掉容易看错的 I O 0 1
LENGTH = 10


def _new_code(conn) -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        if not conn.execute("SELECT 1 FROM phone_codes WHERE code=?", (code,)).fetchone():
            return code


def code_for_role(role_id: int) -> str:
    """有就返回，没有就生成。"""
    with get_conn() as c:
        row = c.execute("SELECT code FROM phone_codes WHERE role_id=?", (role_id,)).fetchone()
        if row:
            return row["code"]
        code = _new_code(c)
        c.execute("INSERT INTO phone_codes(role_id, code, created_ts) VALUES(?,?,?)", (role_id, code, int(time.time())))
        return code


def reset(role_id: int) -> str:
    with get_conn() as c:
        code = _new_code(c)
        c.execute("INSERT INTO phone_codes(role_id, code, created_ts) VALUES(?,?,?) "
                  "ON CONFLICT(role_id) DO UPDATE SET code=excluded.code, created_ts=excluded.created_ts", (role_id, code, int(time.time())))
        return code


def generate_all() -> int:
    """给还没有码的角色都生成一个，返回新生成的个数。"""
    n = 0
    with get_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM roles WHERE id NOT IN (SELECT role_id FROM phone_codes)")]
    for i in ids:
        code_for_role(i)
        n += 1
    return n


def role_for_code(code: str):
    code = (code or "").strip().upper()
    if len(code) != LENGTH:
        return None
    with get_conn() as c:
        return c.execute("SELECT r.* FROM phone_codes p JOIN roles r ON r.id=p.role_id WHERE p.code=?", (code,)).fetchone()


def role_by_id(role_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()


_seen_mem: dict[int, float] = {}


def touch(role_id: int) -> None:
    """记一笔「刚来过」，同一角色 5 分钟内只写一次库。"""
    now = time.time()
    if now - _seen_mem.get(role_id, 0) < 300:
        return
    _seen_mem[role_id] = now
    with get_conn() as c:
        c.execute("INSERT INTO phone_seen(role_id, first_seen, last_seen) VALUES(?,?,?) "
                  "ON CONFLICT(role_id) DO UPDATE SET last_seen=excluded.last_seen", (role_id, int(now), int(now)))


def listing() -> list:
    """后台用：全部角色，带码和最近来过的时间。"""
    with get_conn() as c:
        return c.execute("SELECT r.id, r.name, p.code, s.last_seen FROM roles r LEFT JOIN phone_codes p ON p.role_id=r.id "
                         "LEFT JOIN phone_seen s ON s.role_id=r.id ORDER BY r.id").fetchall()
