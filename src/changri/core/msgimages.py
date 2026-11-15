"""短信/礼物带的图片：玩家在 QQ 里发（带图片的消息）或在小手机里选图，存进库里，投递时跟文字一起发给对方，小手机里能看。
后台「短信图片」页可以看占用、单张删除、按天数清理。图片类型按文件头认（不信上传时报的类型），大小有上限。"""
import time

from .db import get_config, get_conn

DEFAULT_MAX_MB = 5


def enabled() -> bool:
    return get_config("sms_image_enabled", "1") != "0"


def max_bytes() -> int:
    try:
        return max(1, int(get_config("sms_image_max_mb", str(DEFAULT_MAX_MB)))) * 1024 * 1024
    except ValueError:
        return DEFAULT_MAX_MB * 1024 * 1024


def sniff(data: bytes) -> str:
    """认文件头：png/jpeg/gif/webp 返回类型，其他返回空串。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def check(data: bytes) -> str:
    """能存返回空串，不能存返回原因。"""
    if not enabled():
        return "现在不允许短信带图片。"
    if not data:
        return "图片是空的。"
    if len(data) > max_bytes():
        return f"图片太大了（最大 {max_bytes() // 1024 // 1024} MB）。"
    if not sniff(data):
        return "只支持 png、jpg、gif、webp 图片。"
    return ""


def save(msg_id: int, data: bytes) -> int | None:
    if check(data):
        return None
    with get_conn() as c:
        return c.execute("INSERT INTO message_images(msg_id, mime, size, data, created_ts) VALUES(?,?,?,?,?)",
                         (msg_id, sniff(data), len(data), data, int(time.time()))).lastrowid


def ids_for(msg_id: int) -> list[int]:
    with get_conn() as c:
        return [r["id"] for r in c.execute("SELECT id FROM message_images WHERE msg_id=? ORDER BY id", (msg_id,))]


def ids_by_msg(msg_ids: list[int]) -> dict[int, list[int]]:
    if not msg_ids:
        return {}
    out: dict[int, list[int]] = {}
    with get_conn() as c:
        for r in c.execute(f"SELECT id, msg_id FROM message_images WHERE msg_id IN ({','.join('?' * len(msg_ids))}) ORDER BY id", msg_ids):
            out.setdefault(r["msg_id"], []).append(r["id"])
    return out


def get(img_id: int):
    with get_conn() as c:
        return c.execute("SELECT i.*, m.from_role_id, m.to_role_id FROM message_images i JOIN messages m ON m.id=i.msg_id WHERE i.id=?", (img_id,)).fetchone()


def blobs(msg_id: int) -> list[bytes]:
    with get_conn() as c:
        return [r["data"] for r in c.execute("SELECT data FROM message_images WHERE msg_id=? ORDER BY id", (msg_id,))]


def delete(img_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM message_images WHERE id=?", (img_id,))


def delete_older_than(days: int) -> int:
    with get_conn() as c:
        return c.execute("DELETE FROM message_images WHERE created_ts<?", (int(time.time()) - days * 86400,)).rowcount


def delete_all() -> int:
    with get_conn() as c:
        return c.execute("DELETE FROM message_images").rowcount


def stats() -> dict:
    with get_conn() as c:
        n, total = c.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM message_images").fetchone()
    return {"n": n, "bytes": total, "mb": round(total / 1024 / 1024, 1)}


def listing(limit: int = 60, offset: int = 0) -> list[dict]:
    """最新的在前；不带图片内容，只带谁发给谁。"""
    with get_conn() as c:
        rows = c.execute("SELECT i.id, i.size, i.mime, i.created_ts, m.id AS msg_id, m.kind, m.content, f.name AS from_name, t.name AS to_name "
                         "FROM message_images i JOIN messages m ON m.id=i.msg_id JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
                         "ORDER BY i.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(r) for r in rows]
