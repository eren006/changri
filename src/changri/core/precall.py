"""前置电话（额外扩展，默认关）：照老系统小手机的「滑卡」玩法（rp_archive 的 player_precall_*），替换了原来的编号盲选。
- 玩家在小手机「发现 → 前置电话」填一份公开资料（照片 / 一段介绍 / 性别），然后翻别人的资料卡：
  先把所有人看一遍（翻过去不记录，卡片还会转回来），想选谁就向左滑或点 ♥ → 确认框，确认后才算选了，选了不能撤回。
- 每轮最多选 max_picks 个；单向（我选了 TA 就开电话）或双向（mutual：互相选了才开）。
- 分组：只看异性（默认）/ 男女分开两个列表 / 混在一起。
- 匿名（默认开）：选人阶段只显示「匿名 #编号」，电话接通后互相公开真名。
- 配对后在小手机里直接聊（每人可以有句数上限，0 = 不限），不建 QQ 群、不经过机器人；谁都能挂断，挂了双方都不能再说。
- 管理员可以「新开一轮」（大家的选人次数重新算，已接通的电话保留）、隐藏某人的资料、看所有电话（真名）、替人挂断。"""
import io
import secrets
import time

from .db import get_config, get_conn, get_int, set_config

MODES = {"opposite": "只看异性", "split": "男女分开两个列表", "mixed": "混在一起"}
GENDERS = {"m": "男", "f": "女", "x": "不限"}
BIO_MAX = 200
MSG_MAX = 300
PHOTO_GAP = 5          # 秒：换照片不能太快
SEND_GAP = 1           # 秒：说话不能太快


def is_on() -> bool:
    from . import extensions
    return extensions.is_enabled("precall")


def set_on(on: bool) -> None:
    set_config("ext:precall", "1" if on else "0")


def cfg() -> dict:
    """设置在「前置电话」设置组里；round 由管理页的「新开一轮」管。"""
    mode = get_config("precall_group_mode", "opposite")
    mode = {"只看异性": "opposite", "男女分开": "split", "男女分开两个列表": "split", "混在一起": "mixed"}.get(mode, mode)
    return {"max_picks": max(1, min(50, get_int("precall_max_picks", 3))), "mutual": get_config("precall_mutual", "0") == "1",
            "group_mode": mode if mode in MODES else "opposite", "msg_limit": max(0, min(500, get_int("precall_msg_limit", 0))),
            "anonymous": get_config("precall_anonymous", "1") == "1", "round": max(1, get_int("precall_round", 1))}


def new_round() -> int:
    """新开一轮：大家的选人次数重新算，已经接通的电话保留。返回新的轮数。"""
    r = cfg()["round"] + 1
    set_config("precall_round", str(r))
    return r


# ---------- 资料 ----------

def profile(role_id: int):
    with get_conn() as c:
        return c.execute("SELECT role_id, pid, no, gender, bio, photo IS NOT NULL AS has_photo, hidden, updated_ts FROM precall_profiles WHERE role_id=?",
                         (role_id,)).fetchone()


def ready(role_id: int) -> bool:
    p = profile(role_id)
    return bool(p and (p["bio"] or p["has_photo"]))


def process_photo(raw: bytes) -> bytes:
    """资料照片：转成 3:4 竖图（600×800），去掉 EXIF，JPEG 压缩。"""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # noqa: BLE001
        raise ValueError("不是能识别的图片")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img = ImageOps.fit(img, (600, 800), method=Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


def save_profile(role_id: int, gender: str, bio: str, photo_raw: bytes | None = None, remove_photo: bool = False, now: int | None = None) -> tuple[bool, str]:
    now = int(now or time.time())
    gender = gender if gender in GENDERS else "x"
    bio = (bio or "").strip()
    if len(bio) > BIO_MAX:
        return False, f"介绍最多 {BIO_MAX} 字。"
    old = profile(role_id)
    data = None
    if photo_raw:
        if old and now - old["updated_ts"] < PHOTO_GAP:
            return False, "换得太快了，隔几秒再试。"
        try:
            data = process_photo(photo_raw)
        except ValueError as e:
            return False, f"图片打不开：{e}"
    keep_photo = bool(old and old["has_photo"]) and not remove_photo
    if not bio and not data and not keep_photo:
        return False, "照片和介绍至少要有一样。"
    with get_conn() as c:
        if old:
            c.execute("UPDATE precall_profiles SET gender=?, bio=?, updated_ts=? WHERE role_id=?", (gender, bio, now, role_id))
        else:
            no = c.execute("SELECT COALESCE(MAX(no), 0) + 1 FROM precall_profiles").fetchone()[0]
            c.execute("INSERT INTO precall_profiles(role_id, pid, no, gender, bio, updated_ts) VALUES(?,?,?,?,?,?)",
                      (role_id, secrets.token_hex(8), no, gender, bio, now))
        if data:
            c.execute("UPDATE precall_profiles SET photo=? WHERE role_id=?", (data, role_id))
        elif remove_photo:
            c.execute("UPDATE precall_profiles SET photo=NULL WHERE role_id=?", (role_id,))
    return True, "资料已保存，现在可以去选人了。"


def photo(pid: str, viewer_id: int) -> bytes | None:
    """照片按随机编号取，网址里不带角色（匿名时别人看不出这是谁）。被隐藏的只有本人看得到。"""
    with get_conn() as c:
        r = c.execute("SELECT role_id, photo, hidden FROM precall_profiles WHERE pid=?", (pid,)).fetchone()
    if not r or not r["photo"] or (r["hidden"] and r["role_id"] != viewer_id):
        return None
    return r["photo"]


def set_hidden(role_id: int, hidden: bool) -> None:
    with get_conn() as c:
        c.execute("UPDATE precall_profiles SET hidden=? WHERE role_id=?", (1 if hidden else 0, role_id))


# ---------- 名字 ----------

def name_of(role_id: int, reveal: bool = False, k: dict | None = None) -> str:
    """别人看到的名字：匿名模式下选人阶段是「匿名 #编号」，电话接通后（reveal）是真名；关了匿名就一直是真名。"""
    k = k or cfg()
    with get_conn() as c:
        if k["anonymous"] and not reveal:
            r = c.execute("SELECT no FROM precall_profiles WHERE role_id=?", (role_id,)).fetchone()
            return f"匿名 #{r['no'] if r else 0}"
        r = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
    return r["name"] if r else "？"


def initial(label: str) -> str:
    return "匿" if label.startswith("匿名") else label[:1]


# ---------- 选人 ----------

def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def session_between(a: int, b: int):
    x, y = _pair(a, b)
    with get_conn() as c:
        return c.execute("SELECT * FROM precall_sessions WHERE role_lo=? AND role_hi=?", (x, y)).fetchone()


def used(role_id: int, k: dict | None = None) -> int:
    k = k or cfg()
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM precall_likes WHERE round=? AND from_id=?", (k["round"], role_id)).fetchone()[0]


def left(role_id: int, k: dict | None = None) -> int:
    k = k or cfg()
    return max(0, k["max_picks"] - used(role_id, k))


def candidates(role_id: int) -> list[dict]:
    """滑卡列表：别人公开的（没隐藏）、这一轮我还没选过的、还没和我开过电话的；按分组模式过滤。只记「选」，翻过去不记录。"""
    k = cfg()
    me = profile(role_id)
    with get_conn() as c:
        liked = {r["to_id"] for r in c.execute("SELECT to_id FROM precall_likes WHERE round=? AND from_id=?", (k["round"], role_id))}
        talked = {r["role_hi"] if r["role_lo"] == role_id else r["role_lo"]
                  for r in c.execute("SELECT role_lo, role_hi FROM precall_sessions WHERE role_lo=? OR role_hi=?", (role_id, role_id))}
        rows = c.execute("SELECT p.role_id, p.pid, p.no, p.gender, p.bio, p.photo IS NOT NULL AS has_photo, p.updated_ts FROM precall_profiles p "
                         "JOIN roles r ON r.id=p.role_id WHERE p.hidden=0 AND p.role_id!=? AND (p.bio!='' OR p.photo IS NOT NULL) "
                         "ORDER BY p.updated_ts DESC", (role_id,)).fetchall()
    out = []
    for r in rows:
        if r["role_id"] in liked or r["role_id"] in talked:
            continue
        if k["group_mode"] == "opposite" and me and me["gender"] != "x" and r["gender"] != "x" and r["gender"] == me["gender"]:
            continue
        out.append({"pid": r["pid"], "name": name_of(r["role_id"], k=k), "gender": r["gender"], "bio": r["bio"],
                    "has_photo": bool(r["has_photo"]), "v": r["updated_ts"]})
    return out


def like(from_id: int, pid: str, now: int | None = None) -> dict:
    """选 TA（确认过的）→ {ok, msg, matched, session_id, left}。选了不能撤回。"""
    k = cfg()
    now = int(now or time.time())
    if not ready(from_id):
        return {"ok": False, "msg": "先填好自己的资料再选人。"}
    with get_conn() as c:
        t = c.execute("SELECT role_id FROM precall_profiles WHERE pid=? AND hidden=0", (pid,)).fetchone()
    if not t or t["role_id"] == from_id:
        return {"ok": False, "msg": "这个人的资料已经不可见了。"}
    to = t["role_id"]
    with get_conn() as c:
        if c.execute("SELECT 1 FROM precall_likes WHERE round=? AND from_id=? AND to_id=?", (k["round"], from_id, to)).fetchone():
            return {"ok": False, "msg": "这一轮已经选过 TA 了。"}
        if used(from_id, k) >= k["max_picks"]:
            return {"ok": False, "msg": "这一轮的选人次数用完了。", "left": 0}
        c.execute("INSERT INTO precall_likes(round, from_id, to_id, ts) VALUES(?,?,?,?)", (k["round"], from_id, to, now))
    matched, sid = False, 0
    old = session_between(from_id, to)
    if old:
        matched, sid = True, old["id"]
    else:
        with get_conn() as c:
            back = c.execute("SELECT 1 FROM precall_likes WHERE round=? AND from_id=? AND to_id=?", (k["round"], to, from_id)).fetchone()
            if not k["mutual"] or back:
                x, y = _pair(from_id, to)
                sid = c.execute("INSERT INTO precall_sessions(role_lo, role_hi, round, created_ts) VALUES(?,?,?,?)", (x, y, k["round"], now)).lastrowid
                matched = True
    return {"ok": True, "msg": "", "matched": matched, "session_id": sid, "left": left(from_id, k)}


# ---------- 电话 ----------

def session_of(session_id: int, role_id: int):
    """这个电话（只有当事双方拿得到）。"""
    with get_conn() as c:
        r = c.execute("SELECT * FROM precall_sessions WHERE id=?", (session_id,)).fetchone()
    return r if r and role_id in (r["role_lo"], r["role_hi"]) else None


def other_of(s, role_id: int) -> int:
    return s["role_hi"] if s["role_lo"] == role_id else s["role_lo"]


def messages(session_id: int, after: int = 0) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM precall_msgs WHERE session_id=? AND id>? ORDER BY id", (session_id, after)).fetchall()


def mark_read(s, role_id: int, last_id: int) -> None:
    col = "read_lo" if s["role_lo"] == role_id else "read_hi"
    with get_conn() as c:
        c.execute(f"UPDATE precall_sessions SET {col}=MAX({col}, ?) WHERE id=?", (max(last_id, 1), s["id"]))


def mine_count(session_id: int, role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM precall_msgs WHERE session_id=? AND from_id=?", (session_id, role_id)).fetchone()[0]


def send(role_id: int, session_id: int, text: str, now: int | None = None) -> dict:
    k = cfg()
    s = session_of(session_id, role_id)
    if not s:
        return {"ok": False, "msg": "没有找到这个电话。"}
    if s["ended"]:
        return {"ok": False, "msg": "这个电话已经结束了。"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "msg": "说点什么吧。"}
    if len(text) > MSG_MAX:
        return {"ok": False, "msg": f"一次最多 {MSG_MAX} 字。"}
    n = mine_count(session_id, role_id)
    if k["msg_limit"] and n >= k["msg_limit"]:
        return {"ok": False, "msg": f"你的 {k['msg_limit']} 句已经说完了。"}
    now = int(now or time.time())
    with get_conn() as c:
        last = c.execute("SELECT MAX(created_ts) FROM precall_msgs WHERE session_id=? AND from_id=?", (session_id, role_id)).fetchone()[0] or 0
        if now - last < SEND_GAP:
            return {"ok": False, "msg": "说得太快了。"}
        mid = c.execute("INSERT INTO precall_msgs(session_id, from_id, text, created_ts) VALUES(?,?,?,?)", (session_id, role_id, text, now)).lastrowid
    return {"ok": True, "id": mid, "left": (max(0, k["msg_limit"] - n - 1) if k["msg_limit"] else None)}


def end(session_id: int, role_id: int | None = None) -> bool:
    """挂断：双方都不能再说。role_id 为空 = 管理员挂断。"""
    if role_id is not None and not session_of(session_id, role_id):
        return False
    with get_conn() as c:
        return c.execute("UPDATE precall_sessions SET ended=1 WHERE id=?", (session_id,)).rowcount > 0


def sessions_for(role_id: int) -> list[dict]:
    """我的电话列表：对方（接通了就是真名）、最后一条、没读几条、是不是刚配对还没人说话。没读的、刚配对的在前。"""
    k = cfg()
    out = []
    with get_conn() as c:
        rows = c.execute("SELECT * FROM precall_sessions WHERE role_lo=? OR role_hi=? ORDER BY id DESC", (role_id, role_id)).fetchall()
        for s in rows:
            other = other_of(s, role_id)
            read = s["read_lo"] if s["role_lo"] == role_id else s["read_hi"]
            last = c.execute("SELECT from_id, text, created_ts FROM precall_msgs WHERE session_id=? ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()
            unread = c.execute("SELECT COUNT(*) FROM precall_msgs WHERE session_id=? AND from_id!=? AND id>?", (s["id"], role_id, read)).fetchone()[0]
            label = name_of(other, reveal=True, k=k)
            out.append({"id": s["id"], "other_id": other, "name": label, "ini": initial(label), "ended": bool(s["ended"]),
                        "last": last["text"] if last else "", "last_mine": bool(last and last["from_id"] == role_id), "unread": unread,
                        "fresh": (not last) and read == 0, "ts": last["created_ts"] if last else s["created_ts"]})
    return sorted(out, key=lambda x: (-(x["unread"] > 0 or x["fresh"]), -x["id"]))


def badge(role_id: int) -> int:
    """红点数：没读的电话消息 + 刚配对还没开口的电话。"""
    try:
        if not is_on():
            return 0
        return sum(x["unread"] + (1 if x["fresh"] and not x["unread"] else 0) for x in sessions_for(role_id))
    except Exception:  # noqa: BLE001
        return 0


# ---------- 管理员 ----------

def admin_profiles() -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT p.role_id, p.pid, p.no, p.gender, p.bio, p.photo IS NOT NULL AS has_photo, p.hidden, p.updated_ts, r.name "
                         "FROM precall_profiles p JOIN roles r ON r.id=p.role_id ORDER BY p.no").fetchall()
    k = cfg()
    out = []
    for r in rows:
        d = dict(r)
        d["used"] = used(r["role_id"], k)
        out.append(d)
    return out


def admin_sessions() -> list[dict]:
    """所有电话（真名）和完整聊天记录。"""
    with get_conn() as c:
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        rows = c.execute("SELECT * FROM precall_sessions ORDER BY id DESC").fetchall()
        out = []
        for s in rows:
            msgs = [{"who": names.get(m["from_id"], "？"), "text": m["text"], "ts": m["created_ts"]}
                    for m in c.execute("SELECT * FROM precall_msgs WHERE session_id=? ORDER BY id", (s["id"],))]
            out.append({"id": s["id"], "a": names.get(s["role_lo"], "？"), "b": names.get(s["role_hi"], "？"), "round": s["round"],
                        "ended": bool(s["ended"]), "created_ts": s["created_ts"], "msgs": msgs})
    return out


def admin_likes() -> list[dict]:
    """谁选了谁（本轮），单向等对方的也在里面。"""
    k = cfg()
    with get_conn() as c:
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        return [{"from": names.get(r["from_id"], "？"), "to": names.get(r["to_id"], "？"), "ts": r["ts"]}
                for r in c.execute("SELECT * FROM precall_likes WHERE round=? ORDER BY id DESC", (k["round"],))]
