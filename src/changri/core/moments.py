"""朋友圈（额外扩展，默认关闭）：玩家在小手机里发文字+最多 9 张图，别人点赞、评论、回复；自己能删自己的帖子、评论、图片，楼主能删自己帖子下的任何评论。
图片是浏览器压缩过再传的，服务端还会再处理一遍（去 EXIF、大图长边 1600、缩略图 480，都转 JPEG）直接存库。每人有图片张数额度（删图腾位置，文字保留），全库有总容量上限。
后台「朋友圈」页能看全部、删帖子/评论/图片。"""
import io
import re
import time

from .db import get_config, get_conn, get_int

MAX_IMAGES = 9
MAX_TEXT = 1000
MAX_COMMENT = 300
MAX_FILE = 15 * 1024 * 1024
COMMENT_GAP = 3
_REPEAT = re.compile(r"(.)\1{9,}")


def image_quota() -> int:
    return max(0, get_int("moment_image_quota", 20))


def daily_posts() -> int:
    return max(0, get_int("moment_daily_posts", 20))


def total_limit() -> int:
    return max(1, get_int("moment_total_mb", 300)) * 1024 * 1024


def used_images(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM moment_images WHERE role_id=? AND deleted=0", (role_id,)).fetchone()[0]


def total_bytes() -> int:
    with get_conn() as c:
        return c.execute("SELECT COALESCE(SUM(size_bytes),0) FROM moment_images WHERE deleted=0").fetchone()[0]


def process_image(raw: bytes) -> tuple[bytes, bytes, int, int]:
    """原图字节 → (大图, 缩略图, 宽, 高)，不是图片抛 ValueError。"""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise ValueError("不是能识别的图片")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    def enc(im, edge, q):
        im = im.copy()
        im.thumbnail((edge, edge))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        return buf.getvalue(), im.size
    full, (w, h) = enc(img, 1600, 82)
    thumb, _ = enc(img, 480, 75)
    return full, thumb, w, h


def _tidy(text: str) -> str:
    return _REPEAT.sub(lambda m: m.group(1) * 3, text)          # 连着刷同一个字：压成 3 个


def post(role_id: int, text: str, files: list[bytes]) -> tuple[bool, str, int]:
    from . import season
    text = _tidy((text or "").strip())
    if not text and not files:
        return False, "写点什么，或者配张图", 0
    if len(text) > MAX_TEXT:
        return False, f"最多 {MAX_TEXT} 字", 0
    if len(files) > MAX_IMAGES:
        return False, f"一条最多 {MAX_IMAGES} 张图", 0
    day_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
    lim = daily_posts()
    with get_conn() as c:
        if lim and c.execute("SELECT COUNT(*) FROM moments WHERE role_id=? AND created_ts>=?", (role_id, day_start)).fetchone()[0] >= lim:
            return False, f"今天已经发了 {lim} 条，明天再来", 0
    processed = []
    for raw in files:
        if len(raw) > MAX_FILE:
            return False, "有张图片太大了", 0
        try:
            processed.append(process_image(raw))
        except ValueError as e:
            return False, f"有张图片打不开：{e}", 0
    if processed:
        quota, used = image_quota(), used_images(role_id)
        if used + len(processed) > quota:
            return False, f"图片额度不够：已用 {used}/{quota} 张。可以在「我的朋友圈」删掉以前的图片腾出位置，文字会保留", 0
        if total_bytes() + sum(len(a) + len(b) for a, b, _, _ in processed) > total_limit():
            return False, "存档空间满了，暂时不能发图，请联系管理员清理", 0
    now = int(time.time())
    with get_conn() as c:
        mid = c.execute("INSERT INTO moments(role_id, content, day_label, created_ts) VALUES(?,?,?,?)", (role_id, text, season.day_label() if season.running() else "", now)).lastrowid
        for i, (full, thumb, w, h) in enumerate(processed):
            c.execute("INSERT INTO moment_images(moment_id, role_id, data, thumb, size_bytes, width, height, seq, created_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                      (mid, role_id, full, thumb, len(full) + len(thumb), w, h, i, now))
    return True, "已发布", mid


def feed(viewer_id: int, before: int | None = None, limit: int = 20, role_id: int | None = None) -> tuple[list[dict], bool]:
    q = "SELECT m.*, r.name AS role_name FROM moments m JOIN roles r ON r.id=m.role_id WHERE m.deleted=0"
    args: list = []
    if before:
        q += " AND m.id<?"
        args.append(before)
    if role_id:
        q += " AND m.role_id=?"
        args.append(role_id)
    with get_conn() as c:
        posts = [dict(r) for r in c.execute(q + " ORDER BY m.id DESC LIMIT ?", (*args, limit + 1))]
        more = len(posts) > limit
        posts = posts[:limit]
        for p in posts:
            p["mine"] = p["role_id"] == viewer_id
            p["time"] = time.strftime("%m-%d %H:%M", time.localtime(p["created_ts"]))
            p["images"] = [dict(r) for r in c.execute("SELECT id, width, height, deleted FROM moment_images WHERE moment_id=? ORDER BY seq, id", (p["id"],))]
            lk = list(c.execute("SELECT r.id, r.name FROM moment_likes l JOIN roles r ON r.id=l.role_id WHERE l.moment_id=? ORDER BY l.created_ts", (p["id"],)))
            p["likes"] = [r["name"] for r in lk]
            p["liked"] = any(r["id"] == viewer_id for r in lk)
            p["comments"] = [dict(r, mine=r["role_id"] == viewer_id) for r in c.execute(
                "SELECT c.id, c.role_id, r.name AS role_name, COALESCE(t.name,'') AS reply_to, c.content FROM moment_comments c JOIN roles r ON r.id=c.role_id "
                "LEFT JOIN roles t ON t.id=c.reply_to_id WHERE c.moment_id=? AND c.deleted=0 ORDER BY c.id", (p["id"],))]
    return posts, more


def exists(mid: int) -> bool:
    with get_conn() as c:
        return bool(c.execute("SELECT 1 FROM moments WHERE id=? AND deleted=0", (mid,)).fetchone())


def toggle_like(mid: int, role_id: int) -> tuple[bool, str, bool]:
    """返回 (成功, 提示, 现在是不是赞着)。"""
    if not exists(mid):
        return False, "这条朋友圈不见了", False
    with get_conn() as c:
        if c.execute("SELECT 1 FROM moment_likes WHERE moment_id=? AND role_id=?", (mid, role_id)).fetchone():
            c.execute("DELETE FROM moment_likes WHERE moment_id=? AND role_id=?", (mid, role_id))
            return True, "", False
        c.execute("INSERT INTO moment_likes(moment_id, role_id, created_ts) VALUES(?,?,?)", (mid, role_id, int(time.time())))
    return True, "", True


def comment(mid: int, role_id: int, text: str, reply_to: str = "") -> tuple[bool, str]:
    if not exists(mid):
        return False, "这条朋友圈不见了"
    text = _tidy((text or "").strip())
    if not text:
        return False, "评论不能为空"
    if len(text) > MAX_COMMENT:
        return False, f"评论最多 {MAX_COMMENT} 字"
    now = int(time.time())
    with get_conn() as c:
        last = c.execute("SELECT MAX(created_ts) FROM moment_comments WHERE role_id=?", (role_id,)).fetchone()[0] or 0
        if now - last < COMMENT_GAP:
            return False, "慢一点，隔几秒再评论"
        rid = None
        if reply_to:                                    # 只能回复这条下面评论过的人或楼主，不能随手写个名字
            ok = {r[0]: r[1] for r in c.execute("SELECT DISTINCT c.role_id, r.name FROM moment_comments c JOIN roles r ON r.id=c.role_id WHERE c.moment_id=? AND c.deleted=0", (mid,))}
            owner = c.execute("SELECT m.role_id, r.name FROM moments m JOIN roles r ON r.id=m.role_id WHERE m.id=?", (mid,)).fetchone()
            ok[owner[0]] = owner[1]
            rid = next((k for k, v in ok.items() if v == reply_to and k != role_id), None)
        c.execute("INSERT INTO moment_comments(moment_id, role_id, reply_to_id, content, created_ts) VALUES(?,?,?,?,?)", (mid, role_id, rid, text, now))
    return True, ""


def delete_post(mid: int, role_id: int | None = None) -> bool:
    """role_id=None 是后台删（谁的都行）；否则只能删自己的。"""
    with get_conn() as c:
        row = c.execute("SELECT 1 FROM moments WHERE id=? AND deleted=0" + ("" if role_id is None else " AND role_id=?"), (mid,) if role_id is None else (mid, role_id)).fetchone()
        if not row:
            return False
        c.execute("UPDATE moment_images SET deleted=1, data=x'', thumb=x'', size_bytes=0 WHERE moment_id=? AND deleted=0", (mid,))
        c.execute("UPDATE moments SET deleted=1 WHERE id=?", (mid,))
    return True


def delete_comment(cid: int, role_id: int | None = None) -> bool:
    """自己的评论能删；楼主能删自己帖子下的任何评论；role_id=None 是后台。"""
    with get_conn() as c:
        row = c.execute("SELECT c.id FROM moment_comments c JOIN moments m ON m.id=c.moment_id WHERE c.id=? AND c.deleted=0" + ("" if role_id is None else " AND (c.role_id=? OR m.role_id=?)"),
                        (cid,) if role_id is None else (cid, role_id, role_id)).fetchone()
        if not row:
            return False
        c.execute("UPDATE moment_comments SET deleted=1 WHERE id=?", (cid,))
    return True


def delete_image(img_id: int, role_id: int | None = None) -> bool:
    """图删掉腾出额度，帖子的文字还在。"""
    with get_conn() as c:
        row = c.execute("SELECT id FROM moment_images WHERE id=? AND deleted=0" + ("" if role_id is None else " AND role_id=?"), (img_id,) if role_id is None else (img_id, role_id)).fetchone()
        if not row:
            return False
        c.execute("UPDATE moment_images SET deleted=1, data=x'', thumb=x'', size_bytes=0 WHERE id=?", (img_id,))
    return True


def image(img_id: int, thumb: bool = False) -> bytes | None:
    with get_conn() as c:
        r = c.execute("SELECT data, thumb FROM moment_images WHERE id=? AND deleted=0", (img_id,)).fetchone()
    return bytes(r["thumb" if thumb else "data"]) if r else None


def latest() -> dict | None:
    """发现页那一行用：最新一条。"""
    with get_conn() as c:
        r = c.execute("SELECT m.content, m.created_ts, r.name, (SELECT COUNT(*) FROM moment_images i WHERE i.moment_id=m.id AND i.deleted=0) AS n "
                      "FROM moments m JOIN roles r ON r.id=m.role_id WHERE m.deleted=0 ORDER BY m.id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def stats() -> dict:
    with get_conn() as c:
        posts = c.execute("SELECT COUNT(*) FROM moments WHERE deleted=0").fetchone()[0]
        comments = c.execute("SELECT COUNT(*) FROM moment_comments WHERE deleted=0").fetchone()[0]
        images = c.execute("SELECT COUNT(*) FROM moment_images WHERE deleted=0").fetchone()[0]
    return {"posts": posts, "comments": comments, "images": images, "bytes": total_bytes(), "limit": total_limit()}


def clear_all() -> None:
    with get_conn() as c:
        for t in ("moment_comments", "moment_likes", "moment_images", "moments"):
            c.execute(f"DELETE FROM {t}")
