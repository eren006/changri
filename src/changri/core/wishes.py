"""心愿：挂一条想约的事，等别人来摘；摘了就开一个「心愿」房间。"""
import time

from . import blocks, rooms, season, storycal, windows
from .db import get_conn, get_int


def day_count(role_id: int, col: str) -> int:
    with get_conn() as c:
        field = "poster_id" if col == "post" else "taker_id"
        key = "dkey" if col == "post" else "taken_dkey"
        n = c.execute(f"SELECT COUNT(*) FROM wishes WHERE {field}=? AND {key}=?", (role_id, season.day_key())).fetchone()[0]
    from . import godmode
    return max(0, n - godmode.offset(role_id, "wish_post" if col == "post" else "wish_take"))


def post(poster_id: int, time_text: str, place: str, content: str, reward: str = "", sign: str = "") -> tuple[bool, str]:
    if season.phase() != "playing":
        return False, "现在不是进行中的季度，不能挂心愿。"
    from . import godmode
    closed = windows.check("wish") or godmode.check(poster_id, "wish")
    if closed:
        return False, closed
    hm = rooms.parse_hm(time_text)
    if not hm:
        return False, storycal.parse_when(time_text)[1] or storycal.format_error()
    if not place.strip() or not content.strip():
        return False, "地点和内容都要写。"
    cap = get_int("wish_max_concurrent", 0)
    if cap:
        with get_conn() as c:
            open_n = c.execute("SELECT COUNT(*) FROM wishes WHERE poster_id=? AND status='open'", (poster_id,)).fetchone()[0]
        if open_n >= cap:
            return False, f"你同时最多挂 {cap} 条没被摘走的心愿，先撤掉或等人摘走一条。"
    lim = get_int("limit_挂心愿", 0)
    if lim and day_count(poster_id, "post") >= lim:
        return False, f"今天挂心愿的次数用完了（每天 {lim} 次）。"
    day = season.current_day()
    hit = rooms.conflict(poster_id, day, hm[0], hm[1])
    if hit:
        return False, f"你在 D{day} {hit['start_hm']}-{hit['end_hm']} 已经有安排了。"
    with get_conn() as c:
        cur = c.execute("INSERT INTO wishes(poster_id, day, start_hm, end_hm, place, content, reward, sign, dkey, created_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (poster_id, day, hm[0], hm[1], place.strip(), content.strip(), reward.strip(), sign.strip(), season.day_key(), int(time.time())))
    return True, str(cur.lastrowid)


def get(wish_id: int):
    with get_conn() as c:
        return c.execute("SELECT w.*, r.name AS poster_name FROM wishes w JOIN roles r ON r.id=w.poster_id WHERE w.id=?", (wish_id,)).fetchone()


def list_open() -> list:
    with get_conn() as c:
        return c.execute("SELECT w.*, r.name AS poster_name FROM wishes w JOIN roles r ON r.id=w.poster_id WHERE w.status='open' ORDER BY w.id").fetchall()


def take(wish_id: int, taker_id: int) -> tuple[bool, str, int]:
    from . import godmode
    closed = windows.check("wish") or godmode.check(taker_id, "wish")
    if closed:
        return False, closed, 0
    w = get(wish_id)
    if not w or w["status"] != "open":
        return False, "这条心愿不存在或已经被摘走了。", 0
    if w["poster_id"] == taker_id:
        return False, "不能摘自己挂的心愿，要撤回请发「撤心愿 编号」。", 0
    if blocks.get_block(w["poster_id"], taker_id):
        return False, "这条心愿你摘不了。", 0
    lim = get_int("limit_摘心愿", 0)
    if lim and day_count(taker_id, "take") >= lim:
        return False, f"今天摘心愿的次数用完了（每天 {lim} 次）。", 0
    if season.current_day() != w["day"]:
        return False, f"这条心愿是 D{w['day']} 的，现在已经过了。", 0
    ok, info, room_id = rooms.create("心愿", w["poster_id"], [taker_id], f"{w['start_hm']}-{w['end_hm']}", w["place"], w["content"], official=True)
    if not ok:
        return False, info, 0
    with get_conn() as c:
        c.execute("UPDATE wishes SET status='taken', taker_id=?, room_id=?, taken_dkey=? WHERE id=?", (taker_id, room_id, season.day_key(), wish_id))
    return True, "", room_id


def withdraw(wish_id: int, role_id: int) -> tuple[bool, str]:
    w = get(wish_id)
    if not w or w["poster_id"] != role_id:
        return False, "没有这条心愿，或者不是你挂的。"
    if w["status"] != "open":
        return False, "这条心愿已经被摘走或撤回了。"
    with get_conn() as c:
        c.execute("UPDATE wishes SET status='withdrawn' WHERE id=?", (wish_id,))
    return True, ""
