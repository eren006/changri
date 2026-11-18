"""心愿：挂一条想约的事，等别人来摘；摘了就开一个「心愿」房间。规则照老长日系统（不含悬赏心愿，作者说不要了）：
- 挂心愿 时间 地点 内容 [| 昵称]：昵称最多 10 个字；对外只显示 性别符号 + 昵称，不显示真名（没写昵称就只有性别符号）。
- 有效期 24 小时（现实时间）：过了自动消失，跨了游戏天也还能摘；列表里写「剩几小时」。
- 每人同时最多挂 3 条（设置 wish_max_concurrent，0 = 不限）。
- 挂着没被摘的心愿占时间线（rooms.conflict 也算它）。"""
import time

from . import blocks, rooms, season, storycal, windows
from .db import get_conn, get_int


EXPIRE = 24 * 3600
NICK_MAX = 10
GENDER_MARK = {"男": "👨", "女": "👩"}


def alive_since(now: int | None = None) -> int:
    """created_ts 不早于这个时间的心愿还没过期。"""
    return int(now or time.time()) - EXPIRE


def left_hours(w, now: int | None = None) -> int:
    return max(1, -(-(w["created_ts"] + EXPIRE - int(now or time.time())) // 3600))


def label(w) -> str:
    """对外显示的发布者：性别符号 + 昵称（不显示真名）。"""
    from . import charprofile
    mark = GENDER_MARK.get(charprofile.get(w["poster_id"])["gender"], "")
    return f"{mark} {w['sign']}".strip() if w["sign"] else (mark or "匿名")


def line(w, now: int | None = None) -> str:
    """看心愿 / 撤心愿 列表里的一行（照老系统）。"""
    return f"编号：{w['id']}｜{label(w)}｜D{w['day']} {w['start_hm']}-{w['end_hm']}｜{w['place']}｜剩{left_hours(w, now)}h｜内容：{w['content']}"


def broadcast_text(w) -> str:
    """挂心愿时发到公告群的那段（开了 wish_public_send 才发）。"""
    return (f"🌠 新心愿 [{w['id']}]\n{label(w)}\n📅 D{w['day']} {w['start_hm']} ~ {w['end_hm']}\n📍 {w['place']}\n💌 {w['content']}\n"
            f"✨ 摘取：摘心愿 {w['id']}")


def post_ok_text(w) -> str:
    return f"✅ 心愿已漂走！编号：{w['id']}\n有效期：24小时" + (f"\n🏷️ 显示昵称：{w['sign']}" if w["sign"] else "")


def day_count(role_id: int, col: str) -> int:
    with get_conn() as c:
        field = "poster_id" if col == "post" else "taker_id"
        key = "dkey" if col == "post" else "taken_dkey"
        n = c.execute(f"SELECT COUNT(*) FROM wishes WHERE {field}=? AND {key}=?", (role_id, season.day_key())).fetchone()[0]
    from . import godmode
    return max(0, n - godmode.offset(role_id, "wish_post" if col == "post" else "wish_take"))


def post(poster_id: int, time_text: str, place: str, content: str, sign: str = "") -> tuple[bool, str]:
    if season.phase() != "playing":
        return False, "现在不是进行中的季度，不能挂心愿。"
    from . import godmode
    closed = windows.check("wish") or godmode.check(poster_id, "wish")
    if closed:
        return False, closed
    hm = rooms.parse_hm(time_text)
    if not hm:
        return False, storycal.parse_when(time_text)[1] or storycal.format_error()
    from . import rpg
    allowed, reason = rpg.check_place(poster_id, place.strip())
    if not allowed:
        return False, reason
    if not place.strip() or not content.strip():
        return False, "地点和内容都要写。"
    sign = (sign or "").strip()
    if len(sign) > NICK_MAX:
        return False, f"昵称最多 {NICK_MAX} 个字。"
    cap = get_int("wish_max_concurrent", 3)
    if cap:
        with get_conn() as c:
            open_n = c.execute("SELECT COUNT(*) FROM wishes WHERE poster_id=? AND status='open' AND created_ts>=?", (poster_id, alive_since())).fetchone()[0]
        if open_n >= cap:
            return False, f"最多同时挂 {cap} 个心愿，先撤掉或等人摘走一条。"
    lim = get_int("limit_挂心愿", 0)
    if lim and day_count(poster_id, "post") >= lim:
        return False, f"今天挂心愿的次数用完了（每天 {lim} 次）。"
    day = season.current_day()
    closed_h = rooms.blocked_hit(day, hm[0], hm[1])             # 禁约的钟点，心愿也一样不能挂
    if closed_h:
        return False, closed_h
    hit = rooms.conflict(poster_id, day, hm[0], hm[1])          # 已有的约会（含已结束的）和自己别的心愿都算
    if hit:
        return False, f"你在 D{day} {hit['start_hm']}-{hit['end_hm']} 已经有安排了（{hit['kind']}）。"
    from . import letters                       # 写信综开着、设了「挂心愿要几个写信币」时先看够不够
    why = letters.check_cost(poster_id, "wish")
    if why:
        return False, why
    with get_conn() as c:
        cur = c.execute("INSERT INTO wishes(poster_id, day, start_hm, end_hm, place, content, reward, sign, dkey, created_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (poster_id, day, hm[0], hm[1], place.strip(), content.strip(), "", sign, season.day_key(), int(time.time())))
        wid = cur.lastrowid
    letters.pay(poster_id, "wish", f"挂心愿 #{wid}")
    return True, str(wid)


def get(wish_id: int):
    with get_conn() as c:
        return c.execute("SELECT w.*, r.name AS poster_name FROM wishes w JOIN roles r ON r.id=w.poster_id WHERE w.id=?", (wish_id,)).fetchone()


def list_open(poster_id: int | None = None) -> list:
    """还没过期、没被摘走的心愿（给了 poster_id 就只列这个人的）。"""
    sql = "SELECT w.*, r.name AS poster_name FROM wishes w JOIN roles r ON r.id=w.poster_id WHERE w.status='open' AND w.created_ts>=?"
    args: list = [alive_since()]
    if poster_id is not None:
        sql += " AND w.poster_id=?"
        args.append(poster_id)
    with get_conn() as c:
        return c.execute(sql + " ORDER BY w.id", args).fetchall()


def is_alive(w) -> bool:
    return w is not None and w["status"] == "open" and w["created_ts"] >= alive_since()


def take(wish_id: int, taker_id: int) -> tuple[bool, str, int]:
    from . import godmode
    closed = windows.check("wish") or godmode.check(taker_id, "wish")
    if closed:
        return False, closed, 0
    w = get(wish_id)
    if not is_alive(w):
        return False, "心愿不存在或已过期（也可能已经被摘走了）。", 0
    if w["poster_id"] == taker_id:
        return False, "不能摘自己挂的心愿，要撤回请发「撤心愿 编号」。", 0
    if blocks.get_block(w["poster_id"], taker_id):
        return False, "这条心愿你摘不了。", 0
    lim = get_int("limit_摘心愿", 0)
    if lim and day_count(taker_id, "take") >= lim:
        return False, f"今天摘心愿的次数用完了（每天 {lim} 次）。", 0
    hit = rooms.conflict(taker_id, w["day"], w["start_hm"], w["end_hm"], ignore_wish=wish_id)
    if hit:
        return False, f"你在 D{w['day']} {hit['start_hm']}-{hit['end_hm']} 已经有安排了（{hit['kind']}），摘不了这条心愿。", 0
    hit = rooms.conflict(w["poster_id"], w["day"], w["start_hm"], w["end_hm"], locks=False, ignore_wish=wish_id)
    if hit:
        return False, f"挂这条心愿的人在 D{w['day']} {hit['start_hm']}-{hit['end_hm']} 已经有别的安排了，这条心愿暂时摘不了。", 0
    ok, info, room_id = rooms.create("心愿", w["poster_id"], [taker_id], f"{w['start_hm']}-{w['end_hm']}", w["place"], w["content"],
                                     official=True, day=w["day"], wish_id=wish_id)
    if not ok:
        return False, info, 0
    with get_conn() as c:
        c.execute("UPDATE wishes SET status='taken', taker_id=?, room_id=?, taken_dkey=? WHERE id=?", (taker_id, room_id, season.day_key(), wish_id))
    return True, "", room_id


def withdraw(wish_id: int, role_id: int) -> tuple[bool, str]:
    w = get(wish_id)
    if not w or w["poster_id"] != role_id:
        return False, "没有这条心愿，或者不是你挂的。"
    if not is_alive(w):
        return False, "这条心愿已经被摘走、撤回或过期了。"
    with get_conn() as c:
        c.execute("UPDATE wishes SET status='withdrawn' WHERE id=?", (wish_id,))
    return True, ""
