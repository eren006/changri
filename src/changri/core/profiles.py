"""小手机里的「微信名」「给对方的备注」「头像」。
微信名：自己设，别人看到「微信名（真名）」，真名永远跟在括号里，所以冒充不了别人。1–12 字，不能带括号/CQ 码，不能和别人的名字或微信名重复；填空就是清除。
备注：给任何一个角色写，只有自己看得到，显示成「备注（真名）」，优先于对方的微信名。
头像：服务端居中裁成 256×256 的 JPEG 存进库里，重新上传直接替换。都只影响显示，匿名、署名、落款都不变。"""
import io
import re
import time

from .db import get_conn

NICK_MAX = 12
REMARK_MAX = 12
AVATAR_EDGE = 256
AVATAR_GAP = 5
MAX_UPLOAD = 6 * 1024 * 1024
_BAD = re.compile(r"[()（）\[\]【】\n\r]")


def nick_map() -> dict[str, str]:
    """{真名: 微信名}"""
    with get_conn() as c:
        return {r["name"]: r["nick"] for r in c.execute("SELECT r.name, n.nick FROM role_nicks n JOIN roles r ON r.id=n.role_id")}


def remark_map(owner_id: int) -> dict[str, str]:
    with get_conn() as c:
        return {r["name"]: r["remark"] for r in c.execute("SELECT r.name, k.remark FROM role_remarks k JOIN roles r ON r.id=k.target_id WHERE k.owner_id=?", (owner_id,))}


def make_disp(owner_id: int):
    """名字的显示写法：给 TA 写了备注 →「备注（真名）」，否则 TA 设了微信名 →「微信名（真名）」，都没有就是真名。"""
    nicks, remarks = nick_map(), remark_map(owner_id)

    def disp(name: str) -> str:
        n = remarks.get(name) or nicks.get(name)
        return f"{n}（{name}）" if n else name
    return disp


def _check_text(label: str, v: str, mx: int) -> str:
    if len(v) > mx:
        return f"{label}最多 {mx} 个字（现在 {len(v)} 个）"
    if _BAD.search(v) or "[CQ:" in v.upper():
        return f"{label}里不能有括号、换行或 CQ 码"
    return ""


def set_nick(role_id: int, raw: str) -> tuple[bool, str]:
    nick = (raw or "").strip()
    with get_conn() as c:
        me = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not me:
            return False, "找不到这个角色"
        if not nick or nick == me["name"]:
            c.execute("DELETE FROM role_nicks WHERE role_id=?", (role_id,))
            return True, "已清除微信名，别人只会看到你的名字"
        err = _check_text("微信名", nick, NICK_MAX)
        if err:
            return False, err
        taken = {r["name"] for r in c.execute("SELECT name FROM roles WHERE id!=?", (role_id,))}
        taken |= {r["nick"] for r in c.execute("SELECT nick FROM role_nicks WHERE role_id!=?", (role_id,))}
        if nick in taken:
            return False, "这个名字已经有人用了，换一个"
        c.execute("INSERT INTO role_nicks(role_id, nick, updated_ts) VALUES(?,?,?) ON CONFLICT(role_id) DO UPDATE SET nick=excluded.nick, updated_ts=excluded.updated_ts",
                  (role_id, nick, int(time.time())))
    return True, f"微信名已设为「{nick}」，别人会看到「{nick}（{me['name']}）」"


def set_remark(owner_id: int, target_id: int, raw: str) -> tuple[bool, str]:
    remark = (raw or "").strip()
    with get_conn() as c:
        t = c.execute("SELECT name FROM roles WHERE id=?", (target_id,)).fetchone()
        if not t or owner_id == target_id:
            return False, "只能给别的角色写备注"
        if not remark or remark == t["name"]:
            c.execute("DELETE FROM role_remarks WHERE owner_id=? AND target_id=?", (owner_id, target_id))
            return True, "已清除备注"
        err = _check_text("备注", remark, REMARK_MAX)
        if err:
            return False, err
        c.execute("INSERT INTO role_remarks(owner_id, target_id, remark, updated_ts) VALUES(?,?,?,?) "
                  "ON CONFLICT(owner_id, target_id) DO UPDATE SET remark=excluded.remark, updated_ts=excluded.updated_ts", (owner_id, target_id, remark, int(time.time())))
    return True, "备注已保存，只有你自己看得到"


def process_avatar(raw: bytes) -> bytes:
    """裁成正方形、缩到 256，转 JPEG（顺便去掉 EXIF）。打不开抛 ValueError。"""
    from PIL import Image, ImageOps
    from .imgload import open_upload
    img = open_upload(raw, (256, 256))            # 先看像素数再解码（见 imgload）
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img = ImageOps.fit(img, (AVATAR_EDGE, AVATAR_EDGE), method=Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


def set_avatar(role_id: int, raw: bytes) -> tuple[bool, str, int]:
    """返回 (成功, 提示, 版本号)。"""
    if not raw:
        return False, "没收到图片", 0
    if len(raw) > MAX_UPLOAD:
        return False, "图片太大了", 0
    now = int(time.time())
    with get_conn() as c:
        old = c.execute("SELECT updated_ts FROM role_avatars WHERE role_id=?", (role_id,)).fetchone()
        if old and now - old["updated_ts"] < AVATAR_GAP:
            return False, "换得太快了，隔几秒再试", 0
        try:
            data = process_avatar(raw)
        except ValueError as e:
            return False, f"图片打不开：{e}", 0
        c.execute("INSERT INTO role_avatars(role_id, data, updated_ts) VALUES(?,?,?) ON CONFLICT(role_id) DO UPDATE SET data=excluded.data, updated_ts=excluded.updated_ts", (role_id, data, now))
    return True, "头像已更新", now


def remove_avatar(role_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM role_avatars WHERE role_id=?", (role_id,))


def avatar_versions() -> dict[str, int]:
    """{名字: 版本号}，前端据此把名字头像换成图片。"""
    with get_conn() as c:
        return {r["name"]: r["updated_ts"] for r in c.execute("SELECT r.name, a.updated_ts FROM role_avatars a JOIN roles r ON r.id=a.role_id")}


def avatar_data(name: str) -> bytes | None:
    with get_conn() as c:
        r = c.execute("SELECT a.data FROM role_avatars a JOIN roles r ON r.id=a.role_id WHERE r.name=?", (name,)).fetchone()
    return bytes(r["data"]) if r else None
