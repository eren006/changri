"""短信 / 礼物 / 心动信带的图片：玩家在 QQ 里发（带图片的消息）或在小手机里选图，存进库里，投递时跟文字一起发给对方，小手机里能看。
后台「图片管理」页可以看占用、单张删除、按天数清理。图片类型按文件头认（不信上传时报的类型），大小有上限。
存之前去掉照片里的 EXIF（拍摄地点、手机型号）：心动信是匿名的，带着这些等于告诉对方是谁。

- 短信：sms_image_enabled 总开关；
- 礼物：再看 gift_image_enabled。带了图的就是「照片礼物」，不走礼品店（规则在 sendmsg）；
- 心动信：单独的 lovemail_image_enabled，默认关（匿名、要放到派送时间、可能被曝光到公告群）。图存在 lovemail_images，函数带 mail_ 前缀。"""
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


def gift_enabled() -> bool:
    return enabled() and get_config("gift_image_enabled", "1") != "0"


def mail_enabled() -> bool:
    return get_config("lovemail_image_enabled", "0") == "1"


def check(data: bytes) -> str:
    """能存返回空串，不能存返回原因。"""
    if not enabled():
        return "现在不允许短信带图片。"
    return check_bytes(data)


def check_bytes(data: bytes) -> str:
    """只看图片本身（开关由调用的地方查）。"""
    if not data:
        return "图片是空的。"
    if len(data) > max_bytes():
        return f"图片太大了（最大 {max_bytes() // 1024 // 1024} MB）。"
    if not sniff(data):
        return "只支持 png、jpg、gif、webp 图片。"
    return ""


def strip_meta(data: bytes) -> bytes:
    """去掉 jpg / png / webp 里的 EXIF（拍摄地点、时间、手机型号）。没有 EXIF 的原样返回，不重新压缩；
    方向信息先转到像素上再去掉，不然竖着拍的会躺倒。GIF 不动（动图重存会坏）。处理不了的原样返回，不拦着发。"""
    mime = sniff(data)
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        return data
    try:
        import io

        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        if not im.getexif() and "exif" not in im.info:
            return data
        fmt = im.format
        im = ImageOps.exif_transpose(im)
        out = io.BytesIO()
        if fmt == "JPEG":
            im.save(out, "JPEG", quality=92, icc_profile=im.info.get("icc_profile"))
        else:
            im.save(out, fmt)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


def save(msg_id: int, data: bytes) -> int | None:
    if check_bytes(data):
        return None
    data = strip_meta(data)
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


# ---------- 心动信的图（lovemail_images） ----------

def mail_check(data: bytes) -> str:
    if not mail_enabled():
        return "现在心动信不能附图片。"
    return check_bytes(data)


def mail_save(mail_id: int, data: bytes) -> int | None:
    if check_bytes(data):
        return None
    data = strip_meta(data)
    with get_conn() as c:
        return c.execute("INSERT INTO lovemail_images(mail_id, mime, size, data, created_ts) VALUES(?,?,?,?,?)",
                         (mail_id, sniff(data), len(data), data, int(time.time()))).lastrowid


def mail_ids_by_mail(mail_ids: list[int]) -> dict[int, list[int]]:
    if not mail_ids:
        return {}
    out: dict[int, list[int]] = {}
    with get_conn() as c:
        for r in c.execute(f"SELECT id, mail_id FROM lovemail_images WHERE mail_id IN ({','.join('?' * len(mail_ids))}) ORDER BY id", mail_ids):
            out.setdefault(r["mail_id"], []).append(r["id"])
    return out


def mail_blobs(mail_id: int) -> list[bytes]:
    with get_conn() as c:
        return [r["data"] for r in c.execute("SELECT data FROM lovemail_images WHERE mail_id=? ORDER BY id", (mail_id,))]


def mail_get(img_id: int):
    """带上信的寄信人、收信人、状态（小手机按这个判断谁能看）。"""
    with get_conn() as c:
        return c.execute("SELECT i.*, m.from_role_id, m.to_role_id, m.status FROM lovemail_images i JOIN lovemails m ON m.id=i.mail_id WHERE i.id=?", (img_id,)).fetchone()


def mail_delete_for(mail_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lovemail_images WHERE mail_id=?", (mail_id,))


def mail_delete(img_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lovemail_images WHERE id=?", (img_id,))


# 还没派送的信（信池里的、静默拉黑等着吞掉的）的图不能按天数清：清了收信人就收不到了
_UNSENT = "(SELECT id FROM lovemails WHERE status IN ('pool','held'))"


def mail_delete_older_than(days: int) -> int:
    with get_conn() as c:
        return c.execute(f"DELETE FROM lovemail_images WHERE created_ts<? AND mail_id NOT IN {_UNSENT}",
                         (int(time.time()) - days * 86400,)).rowcount


def mail_delete_all() -> int:
    with get_conn() as c:
        return c.execute("DELETE FROM lovemail_images").rowcount


def mail_stats() -> dict:
    with get_conn() as c:
        n, total = c.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM lovemail_images").fetchone()
    return {"n": n, "bytes": total, "mb": round(total / 1024 / 1024, 1)}


def mail_listing(limit: int = 60, offset: int = 0) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT i.id, i.size, i.created_ts, m.content, m.signature, m.status, f.name AS from_name, t.name AS to_name "
                         "FROM lovemail_images i JOIN lovemails m ON m.id=i.mail_id JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
                         "ORDER BY i.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(r) for r in rows]
