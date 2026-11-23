"""公知和二表：每个角色各一份，可以是文字和图片，反复提交就整份覆盖。
文字存数据库；图片存成文件放在这个机器人的工作区里（data/bots/<AppID>/sheets/），数据库只记文件名。
图片收进来时先验证是真的图片、缩到最长边 2000 像素以内、统一存成 JPEG（带透明的存 PNG），
文件名由程序生成（不用玩家给的任何名字），所以不会出现路径穿越或奇怪的文件类型。"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

from . import db
from .db import get_config, get_conn

KINDS = {"gongzhi": "公知", "erbiao": "二表"}
TEXT_MAX = 3000
IMG_MAX = 9
SIDE_MAX = 2000
_NAME_RE = re.compile(r"\d{1,9}_(gongzhi|erbiao)_\d{1,2}_\d{9,12}\.(jpg|png)")


def folder() -> Path:
    app = db.current_bot()
    base = db.workspace_path(app).parent if app and not db.LEGACY_SINGLE else db.DB_PATH.parent
    d = base / "sheets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def public() -> bool:
    """展示公知：开着，玩家才能看别人的公知：「查看公知」是所有人的，「查看公知 角色名」只看这一个人。默认关。"""
    return get_config("gongzhi_public", "0") == "1"


def normalize_image(data: bytes) -> tuple[bytes, str] | None:
    """验证并压缩一张图片。不是图片、太大、打不开都返回 None。返回 (字节, 扩展名)。"""
    try:
        from .imgload import open_upload
        im = open_upload(data, (SIDE_MAX, SIDE_MAX))      # 先看像素数再解码（见 imgload）；太大、打不开都抛错，下面当 None
        if max(im.size) > SIDE_MAX:
            im.thumbnail((SIDE_MAX, SIDE_MAX))
        out = io.BytesIO()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im.convert("RGBA").save(out, "PNG", optimize=True)
            return out.getvalue(), "png"
        im.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue(), "jpg"
    except Exception:  # noqa: BLE001
        return None


def remove_files(role_id: int, kind: str | None = None) -> None:
    with get_conn() as c:
        q, a = "SELECT filename FROM sheet_images WHERE role_id=?", [role_id]
        if kind:
            q += " AND kind=?"
            a.append(kind)
        names = [r["filename"] for r in c.execute(q, a)]
    d = folder()
    for n in names:
        if _NAME_RE.fullmatch(n):
            try:
                (d / n).unlink()
            except OSError:
                pass


def submit(role_id: int, kind: str, text: str, images: list[bytes] | None = None) -> tuple[bool, str]:
    """提交（或覆盖）一份。返回 (成功, 提示)。文字和图片至少要有一样。"""
    if kind not in KINDS:
        return False, "没有这种表。"
    text = (text or "").strip()[:TEXT_MAX]
    good = []
    skipped = 0
    for raw in (images or [])[:IMG_MAX]:
        n = normalize_image(raw)
        if n:
            good.append(n)
        else:
            skipped += 1
    if not text and not good:
        return False, "没有内容：请在指令后面写文字，或者连图片一起发。" + ("（图片没能打开，换一张试试）" if skipped else "")
    remove_files(role_id, kind)
    now = int(time.time())
    names = []
    d = folder()
    for i, (blob, ext) in enumerate(good):
        name = f"{role_id}_{kind}_{i}_{now}.{ext}"
        (d / name).write_bytes(blob)
        names.append(name)
    with get_conn() as c:
        c.execute("DELETE FROM sheet_images WHERE role_id=? AND kind=?", (role_id, kind))
        c.execute("INSERT INTO sheets(role_id,kind,text,updated_ts) VALUES(?,?,?,?) "
                  "ON CONFLICT(role_id,kind) DO UPDATE SET text=excluded.text, updated_ts=excluded.updated_ts", (role_id, kind, text, now))
        for i, n in enumerate(names):
            c.execute("INSERT INTO sheet_images(role_id,kind,filename,ord) VALUES(?,?,?,?)", (role_id, kind, n, i))
    tip = f"（{skipped} 张图片没能打开，已跳过）" if skipped else ""
    return True, f"{len(text)} 字 + {len(names)} 张图片{tip}"


def get(role_id: int, kind: str) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT s.*, ro.name, ro.is_npc FROM sheets s JOIN roles ro ON ro.id=s.role_id WHERE s.role_id=? AND s.kind=?", (role_id, kind)).fetchone()
        if not r:
            return None
        imgs = [x["filename"] for x in c.execute("SELECT filename FROM sheet_images WHERE role_id=? AND kind=? ORDER BY ord", (role_id, kind))]
    return {"role_id": role_id, "name": r["name"], "kind": kind, "text": r["text"], "images": imgs, "updated_ts": r["updated_ts"],
            "is_npc": bool(r["is_npc"])}


def all_of(kind: str) -> list[dict]:
    """所有交过这种表的角色（按角色顺序）。"""
    with get_conn() as c:
        ids = [r["role_id"] for r in c.execute("SELECT s.role_id FROM sheets s JOIN roles ro ON ro.id=s.role_id WHERE s.kind=? ORDER BY ro.id", (kind,))]
    return [x for x in (get(i, kind) for i in ids) if x]


def image_bytes(name: str) -> tuple[bytes, str] | None:
    """按文件名读一张图（文件名必须是程序生成的格式，别的一律不认）。"""
    if not _NAME_RE.fullmatch(name or ""):
        return None
    p = folder() / name
    if not p.is_file():
        return None
    return p.read_bytes(), "image/png" if name.endswith(".png") else "image/jpeg"


def overview() -> list[dict]:
    """后台表格：所有角色，各自有没有交公知、二表。"""
    with get_conn() as c:
        roles = c.execute("SELECT id, name FROM roles ORDER BY id").fetchall()
        have = {(r["role_id"], r["kind"]): r["updated_ts"] for r in c.execute("SELECT role_id, kind, updated_ts FROM sheets")}
    return [{"id": r["id"], "name": r["name"], "gongzhi": have.get((r["id"], "gongzhi")), "erbiao": have.get((r["id"], "erbiao"))} for r in roles]


def set_text(role_id: int, kind: str, text: str) -> bool:
    """管理员改某人公知/二表的文字（照老系统管理手机「改公知」）：只改文字，图片不动；玩家之后重新提交会覆盖。"""
    with get_conn() as c:
        return c.execute("UPDATE sheets SET text=?, updated_ts=? WHERE role_id=? AND kind=?",
                         ((text or "").strip()[:TEXT_MAX], int(time.time()), role_id, kind)).rowcount > 0
