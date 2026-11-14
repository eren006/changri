"""管理员：后台登记。被指定的人用一次性管理员码在群里「认管理员」，不需要有角色。"""
import secrets
import time

from .db import get_conn

CODE_TTL = 24 * 3600


def make_code(note: str = "") -> str:
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_codes WHERE expires_ts<?", (int(time.time()),))
        code = f"{secrets.randbelow(10**6):06d}"
        while conn.execute("SELECT 1 FROM admin_codes WHERE code=?", (code,)).fetchone():
            code = f"{secrets.randbelow(10**6):06d}"
        conn.execute("INSERT INTO admin_codes(code, note, expires_ts) VALUES(?,?,?)", (code, note[:30], int(time.time()) + CODE_TTL))
    return code


def claim(code: str, bot_id: str, member_openid: str) -> tuple[bool, str]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM admin_codes WHERE code=? AND expires_ts>=?", (code, int(time.time()))).fetchone()
        if not row:
            return False, "管理员码不对或已过期。"
        conn.execute("INSERT OR REPLACE INTO admins(bot_id, member_openid, note, created_ts) VALUES(?,?,?,?)",
                     (bot_id, member_openid, row["note"], int(time.time())))
        conn.execute("DELETE FROM admin_codes WHERE code=?", (code,))
    return True, "已登记为管理员。"


def is_admin(bot_id: str, member_openid: str) -> bool:
    """管理员身份也是「每个机器人各一套」：同一个人用两个机器人时，要分别认一次。"""
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE bot_id=? AND member_openid=?", (bot_id, member_openid)).fetchone() is not None


def list_admins() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins ORDER BY created_ts").fetchall()


def remove(bot_id: str, member_openid: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM admins WHERE bot_id=? AND member_openid=?", (bot_id, member_openid))
