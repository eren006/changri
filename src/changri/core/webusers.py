"""设置平台的登录账号。密码只存哈希；多个管理员各有自己的账号。（和 QQ 里的管理员是两回事）"""
import re
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_global_conn as get_conn

NAME_RE = re.compile(r"[0-9A-Za-z_\-一-鿿]{2,20}")
PASSWORD_MIN = 8
_DUMMY = generate_password_hash("dummy-password-for-timing")


def validate(username: str, password: str) -> str:
    """返回错误提示，没问题返回空串。"""
    if not NAME_RE.fullmatch(username):
        return "账号要 2 到 20 个字，只能用汉字、字母、数字、下划线和短横线。"
    if len(password) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 位。"
    return ""


def count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM web_users").fetchone()[0]


def create_user(username: str, password: str, role: str = "admin", note: str = "") -> tuple[bool, str]:
    """role='owner' 只在首次安装时用；库里始终只有一个主账号。"""
    err = validate(username, password)
    if err:
        return False, err
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM web_users WHERE username=?", (username,)).fetchone():
            return False, "这个账号已经有了。"
        if role == "owner" and conn.execute("SELECT 1 FROM web_users WHERE role='owner'").fetchone():
            return False, "主账号已经存在。"
        conn.execute("INSERT INTO web_users(username, password_hash, role, note, created_ts) VALUES(?,?,?,?,?)",
                     (username, generate_password_hash(password), role, note[:30], int(time.time())))
    return True, ""


def generate_admin(note: str = "") -> tuple[str, str]:
    """主账号一键生成一个管理员账号：随机账号名和密码，只在生成这一刻能看到密码。"""
    with get_conn() as conn:
        while True:
            username = f"admin{secrets.randbelow(10000):04d}"
            if not conn.execute("SELECT 1 FROM web_users WHERE username=?", (username,)).fetchone():
                break
    password = secrets.token_urlsafe(9)
    ok, err = create_user(username, password, "admin", note)
    if not ok:
        raise RuntimeError(err)
    return username, password


def reset_password(user_id: int) -> str | None:
    """主账号给别人重置密码，返回新密码。"""
    user = get_user(user_id)
    if not user:
        return None
    password = secrets.token_urlsafe(9)
    with get_conn() as conn:
        conn.execute("UPDATE web_users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))
    return password


def set_disabled(user_id: int, disabled: bool) -> tuple[bool, str]:
    user = get_user(user_id)
    if not user:
        return False, "没有这个账号。"
    if user["role"] == "owner":
        return False, "主账号不能停用。"
    with get_conn() as conn:
        conn.execute("UPDATE web_users SET disabled=? WHERE id=?", (int(disabled), user_id))
    return True, ""


def verify(username: str, password: str):
    """账号或密码不对都返回 None；账号不存在时也做一次哈希比较，避免从耗时看出账号是否存在。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM web_users WHERE username=?", (username,)).fetchone()
        ok = check_password_hash(row["password_hash"] if row else _DUMMY, password)
        if not row or not ok:
            return None
        conn.execute("UPDATE web_users SET last_login_ts=? WHERE id=?", (int(time.time()), row["id"]))
        return row


def get_user(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM web_users WHERE id=?", (user_id,)).fetchone()


def list_users() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM web_users ORDER BY id").fetchall()


def change_password(user_id: int, old: str, new: str) -> tuple[bool, str]:
    user = get_user(user_id)
    if not user or not check_password_hash(user["password_hash"], old):
        return False, "原密码不对。"
    if len(new) < PASSWORD_MIN:
        return False, f"新密码至少 {PASSWORD_MIN} 位。"
    with get_conn() as conn:
        conn.execute("UPDATE web_users SET password_hash=? WHERE id=?", (generate_password_hash(new), user_id))
    return True, ""


def delete_user(user_id: int, acting_id: int) -> tuple[bool, str]:
    user = get_user(user_id)
    if not user:
        return False, "没有这个账号。"
    if user["role"] == "owner":
        return False, "主账号不能删除。"
    if user_id == acting_id:
        return False, "不能删除自己正在用的账号。"
    with get_conn() as conn:
        conn.execute("DELETE FROM web_users WHERE id=?", (user_id,))
    return True, ""
