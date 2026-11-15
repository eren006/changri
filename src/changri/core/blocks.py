"""拉黑：单方面屏蔽对方对自己发起的短信/礼物。规则对齐长日系统：
默认不静默（对方会被告知），静默则对方以为发成功；拉黑满 2 小时才能解除；已拉黑再拉不重新计时。
"""
import time

from .db import get_conn, get_int


def block(blocker_id: int, blocked_id: int, silent: bool) -> None:
    now = int(time.time())
    with get_conn() as conn:
        row = conn.execute("SELECT since_ts FROM blocks WHERE blocker_id=? AND blocked_id=?", (blocker_id, blocked_id)).fetchone()
        since = row["since_ts"] if row else now   # 已经拉黑过的再拉（比如换静默模式）不重新计时
        conn.execute(
            "INSERT INTO blocks(blocker_id, blocked_id, silent, since_ts) VALUES(?,?,?,?) "
            "ON CONFLICT(blocker_id, blocked_id) DO UPDATE SET silent=excluded.silent",
            (blocker_id, blocked_id, int(silent), since),
        )


def unblock(blocker_id: int, blocked_id: int) -> tuple[bool, str]:
    hours = get_int("block_undo_hours", 2)
    with get_conn() as conn:
        row = conn.execute("SELECT since_ts FROM blocks WHERE blocker_id=? AND blocked_id=?", (blocker_id, blocked_id)).fetchone()
        if not row:
            return False, "not_blocked"
        wait = row["since_ts"] + hours * 3600 - int(time.time())
        if wait > 0:
            return False, f"wait:{-(-wait // 60)}"
        conn.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (blocker_id, blocked_id))
    return True, ""


def get_block(blocker_id: int, blocked_id: int):
    """blocker 有没有拉黑 blocked，返回记录或 None。"""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM blocks WHERE blocker_id=? AND blocked_id=?", (blocker_id, blocked_id)).fetchone()


def list_blocks(blocker_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT b.*, r.name AS blocked_name FROM blocks b JOIN roles r ON r.id=b.blocked_id "
            "WHERE b.blocker_id=? ORDER BY b.since_ts", (blocker_id,)
        ).fetchall()
