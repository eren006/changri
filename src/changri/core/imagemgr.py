"""图片管理：把几处存图的地方放在一起看、删——短信/礼物图片、心动信图片、公知二表图片、出场图片、朋友圈图片。"""
import re

from . import msgimages, sheets
from .db import get_conn

SOURCES = [("sms", "短信与礼物"), ("lovemail", "心动信"), ("sheet", "公知、二表"), ("entrance", "出场"), ("moments", "朋友圈")]
KIND_NAME = {"gongzhi": "公知", "erbiao": "二表"}
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def valid(src: str) -> bool:
    return src in {s for s, _ in SOURCES}


def counts() -> dict:
    out = {"sms": msgimages.stats()["n"], "lovemail": msgimages.mail_stats()["n"]}
    with get_conn() as c:
        out["sheet"] = c.execute("SELECT COUNT(*) FROM sheet_images").fetchone()[0]
        out["entrance"] = c.execute("SELECT COUNT(*) FROM entrance_images").fetchone()[0]
        out["moments"] = c.execute("SELECT COUNT(*) FROM moment_images WHERE deleted=0").fetchone()[0]
    return out


def sizes() -> dict:
    """各处占的空间（MB）。公知二表的图是文件，按文件大小估。"""
    out = {"sms": msgimages.stats()["mb"], "lovemail": msgimages.mail_stats()["mb"]}
    with get_conn() as c:
        out["moments"] = round(c.execute("SELECT COALESCE(SUM(size_bytes),0) FROM moment_images WHERE deleted=0").fetchone()[0] / 1048576, 1)
        out["entrance"] = round((c.execute("SELECT COALESCE(SUM(LENGTH(data)),0) FROM entrance_images").fetchone()[0]) / 1048576, 1)
        names = [r["filename"] for r in c.execute("SELECT filename FROM sheet_images")]
    total = 0
    d = sheets.folder()
    for n in names:
        if _NAME_RE.fullmatch(n):
            try:
                total += (d / n).stat().st_size
            except OSError:
                pass
    out["sheet"] = round(total / 1048576, 1)
    return out


def listing(src: str, limit: int, offset: int) -> list[dict]:
    """统一成 {id, title, sub, size_kb} 的列表，最新的在前。"""
    if src == "sms":
        return [{"id": r["id"], "title": f"{r['from_name']} → {r['to_name']}", "sub": f"{'礼物' if r['kind'] == 'gift' else '短信'} · {r['content'][:30]}",
                 "ts": r["created_ts"], "size_kb": round(r["size"] / 1024)} for r in msgimages.listing(limit, offset)]
    if src == "lovemail":
        wait = {"pool": " · 等待派送", "held": " · 等待派送"}
        return [{"id": r["id"], "title": f"{r['from_name']} → {r['to_name']}", "sub": f"署名「{r['signature']}」{wait.get(r['status'], '')} · {(r['content'] or '（只有图）')[:30]}",
                 "ts": r["created_ts"], "size_kb": round(r["size"] / 1024)} for r in msgimages.mail_listing(limit, offset)]
    with get_conn() as c:
        if src == "sheet":
            rows = c.execute("SELECT i.id, i.kind, i.filename, r.name FROM sheet_images i JOIN roles r ON r.id=i.role_id ORDER BY i.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            out = []
            d = sheets.folder()
            for r in rows:
                try:
                    kb = round((d / r["filename"]).stat().st_size / 1024) if _NAME_RE.fullmatch(r["filename"]) else 0
                except OSError:
                    kb = 0
                out.append({"id": r["id"], "title": r["name"], "sub": KIND_NAME.get(r["kind"], r["kind"]), "ts": 0, "size_kb": kb})
            return out
        if src == "moments":
            rows = c.execute("SELECT i.id, i.size_bytes, i.created_ts, r.name, m.content FROM moment_images i JOIN roles r ON r.id=i.role_id JOIN moments m ON m.id=i.moment_id "
                             "WHERE i.deleted=0 ORDER BY i.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            return [{"id": r["id"], "title": r["name"], "sub": "朋友圈 · " + (r["content"] or "")[:30], "ts": r["created_ts"], "size_kb": round(r["size_bytes"] / 1024)} for r in rows]
        rows = c.execute("SELECT i.id, LENGTH(i.data) AS n, r.name FROM entrance_images i JOIN roles r ON r.id=i.role_id ORDER BY i.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [{"id": r["id"], "title": r["name"], "sub": "出场图片", "ts": 0, "size_kb": round(r["n"] / 1024)} for r in rows]


def raw(src: str, img_id: int) -> tuple[bytes, str] | None:
    if src == "sms":
        r = msgimages.get(img_id)
        return (r["data"], r["mime"]) if r else None
    if src == "lovemail":
        r = msgimages.mail_get(img_id)
        return (r["data"], r["mime"]) if r else None
    if src == "moments":
        from changri.core import moments
        b = moments.image(img_id)
        return (b, msgimages.sniff(b) or "image/jpeg") if b else None
    with get_conn() as c:
        if src == "sheet":
            r = c.execute("SELECT filename FROM sheet_images WHERE id=?", (img_id,)).fetchone()
            return sheets.image_bytes(r["filename"]) if r else None
        r = c.execute("SELECT data FROM entrance_images WHERE id=?", (img_id,)).fetchone()
    return (r["data"], msgimages.sniff(r["data"]) or "image/jpeg") if r else None


def _drop_sheet_files(c, where: str, args: tuple) -> None:
    d = sheets.folder()
    for r in c.execute(f"SELECT filename FROM sheet_images {where}", args).fetchall():
        if _NAME_RE.fullmatch(r["filename"]):
            try:
                (d / r["filename"]).unlink()
            except OSError:
                pass


def delete(src: str, img_id: int) -> None:
    if src == "sms":
        msgimages.delete(img_id)
    elif src == "lovemail":
        msgimages.mail_delete(img_id)
    elif src == "sheet":
        with get_conn() as c:
            _drop_sheet_files(c, "WHERE id=?", (img_id,))
            c.execute("DELETE FROM sheet_images WHERE id=?", (img_id,))
    elif src == "moments":
        from changri.core import moments
        moments.delete_image(img_id)
    elif src == "entrance":
        with get_conn() as c:
            c.execute("DELETE FROM entrance_images WHERE id=?", (img_id,))


def delete_all(src: str) -> int:
    if src == "sms":
        return msgimages.delete_all()
    if src == "lovemail":
        return msgimages.mail_delete_all()
    if src == "moments":
        with get_conn() as c:
            return c.execute("UPDATE moment_images SET deleted=1, data=x'', thumb=x'', size_bytes=0 WHERE deleted=0").rowcount
    with get_conn() as c:
        if src == "sheet":
            _drop_sheet_files(c, "", ())
            return c.execute("DELETE FROM sheet_images").rowcount
        return c.execute("DELETE FROM entrance_images").rowcount
