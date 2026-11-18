"""目击系统（额外扩展，独立于小手机，默认关闭）。

房间（约会/官约/心愿）建好后，找同一天、同一地点、时间重叠够多、人不重复的其他进行中房间，
双方成员各自按概率「看见」对方：看见的人收到点名提示，被看见的人收到匿名或点名提示（后台设置）。
消息先写进 sightings 表（既是记录也是发件箱），由定时任务发到个人群；没开小手机也照常工作。
"""
import random
import time

from . import rooms
from .db import get_config, get_conn, get_int
from .textutil import hm_text

MODES = ("匿名", "点名", "随机")


def enabled() -> bool:
    return get_config("ext:sighting", "0") != "0"


def _mins(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def overlap_ratio(a1: str, a2: str, b1: str, b2: str, d1: int = 0, d2: int = 0) -> float:
    """两段时间的重叠占较短那段的几成。跨午夜的（2300-0100）后半段算进第二天，所以要带上各自是第几天。"""
    s, t = rooms.span(d1, a1, a2), rooms.span(d2, b1, b2)
    if not s or not t:
        return 0.0
    lo, hi = max(s[0], t[0]), min(s[1], t[1])
    if lo >= hi:
        return 0.0
    return (hi - lo) / min(s[1] - s[0], t[1] - t[0])


def _today_start() -> int:
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def saw_today(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM sightings WHERE role_id=? AND kind='saw' AND created_ts>=?", (role_id, _today_start())).fetchone()[0]


def _names(room_id: int) -> list[str]:
    with get_conn() as c:
        return [r["name"] for r in c.execute("SELECT r.name FROM room_members m JOIN roles r ON r.id=m.role_id WHERE m.room_id=? ORDER BY r.id", (room_id,))]


def _ids(room_id: int) -> list[int]:
    with get_conn() as c:
        return [r["role_id"] for r in c.execute("SELECT role_id FROM room_members WHERE room_id=?", (room_id,))]


def _add(role_id: int, kind: str, room_id: int, other_id: int, place: str, day, hm: str, text: str, anon: int = 0) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO sightings(role_id, kind, room_id, other_room_id, place, day, hm, text, anon, status, created_ts) VALUES(?,?,?,?,?,?,?,?,?, 'pending', ?)",
                  (role_id, kind, room_id, other_id, place, day, hm, text, anon, int(time.time())))


def _already(role_id: int, other_id: int) -> bool:
    with get_conn() as c:
        return c.execute("SELECT 1 FROM sightings WHERE role_id=? AND kind='saw' AND other_room_id=?", (role_id, other_id)).fetchone() is not None


def _look(viewers: list[int], vroom, target) -> None:
    """viewers（vroom 的成员）这一边去看 target 房间。"""
    chance = get_int("sighting_chance", 50)
    cap = get_int("sighting_daily_limit", 5)
    tnames = _names(target["id"])
    won: list[int] = []
    for v in viewers:
        if _already(v, target["id"]) or (cap and saw_today(v) >= cap) or random.random() * 100 >= chance:
            continue
        _add(v, "saw", vroom["id"], target["id"], target["place"], target["day"], f"{hm_text(target['start_hm'], target['end_hm'])}",
             f"👀 不会吧，你居然在「{target['place']}」看见 {'、'.join(tnames)} 在一起了！（时间：{hm_text(target['start_hm'], target['end_hm'])}）")
        won.append(v)
    if not won or get_config("sighting_notify_seen", "1") != "1":
        return
    mode = get_config("sighting_seen_mode", "随机")
    with get_conn() as c:
        vnames = [r["name"] for r in c.execute(f"SELECT name FROM roles WHERE id IN ({','.join('?' * len(won))})", won)]
    for rid in _ids(target["id"]):
        anon = 1 if mode == "匿名" or (mode != "点名" and random.random() < 0.5) else 0
        who = "有人" if anon else "、".join(vnames)
        _add(rid, "seen", target["id"], vroom["id"], target["place"], target["day"], f"{hm_text(target['start_hm'], target['end_hm'])}",
             f"👀 哎呀，你们在「{target['place']}」的约会被{who}看见了！（时间：{hm_text(target['start_hm'], target['end_hm'])}）", anon)


def detect(room_id: int) -> int:
    """新房间建好后调用。返回写下的记录条数。出错不能拖垮建房间，调用方自己 try。"""
    if not enabled():
        return 0
    room = rooms.get(room_id)
    if not room or not room["place"] or room["day"] is None or not room["start_hm"]:
        return 0
    thr = get_int("sighting_overlap_pct", 30) / 100
    states = ("open", "ended") if get_config("sighting_include_ended", "0") == "1" else ("open",)
    with get_conn() as c:
        others = c.execute(f"SELECT * FROM rooms WHERE id!=? AND day BETWEEN ? AND ? AND status IN ({','.join('?' * len(states))}) AND place!='' AND LOWER(place)=LOWER(?) AND start_hm!=''",
                           (room_id, room["day"] - 1, room["day"] + 1, *states, room["place"])).fetchall()
        before = c.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    mine = set(_ids(room_id))
    for o in others:
        if overlap_ratio(room["start_hm"], room["end_hm"], o["start_hm"], o["end_hm"], room["day"], o["day"]) < thr:
            continue
        theirs = set(_ids(o["id"]))
        if mine & theirs:
            continue
        _look(sorted(mine), room, o)
        _look(sorted(theirs), o, room)
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] - before


async def push_pending(send, limit: int = 30) -> int:
    """把没发出去的提示发到个人群。超过 3 小时的作废（约会早就结束了，再提示没意义）。"""
    from . import messaging
    now = int(time.time())
    with get_conn() as c:
        rows = c.execute("SELECT * FROM sightings WHERE status='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()
    n = 0
    for r in rows:
        if now - r["created_ts"] > 3 * 3600:
            ok, why = False, "超时作废"
        else:
            try:
                ok, why = await messaging.push_to_role(r["role_id"], r["text"], send)
            except Exception as e:  # noqa: BLE001
                ok, why = False, str(e)[:80]
        with get_conn() as c:
            c.execute("UPDATE sightings SET status=?, sent_ts=?, error=? WHERE id=?", ("sent" if ok else "failed", now, "" if ok else why, r["id"]))
        n += ok
    return n


def recent(role_id: int | None = None, limit: int = 100) -> list:
    with get_conn() as c:
        if role_id is None:
            return c.execute("SELECT s.*, r.name AS role_name FROM sightings s JOIN roles r ON r.id=s.role_id ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
        return c.execute("SELECT s.*, r.name AS role_name FROM sightings s JOIN roles r ON r.id=s.role_id WHERE s.role_id=? ORDER BY s.id DESC LIMIT ?", (role_id, limit)).fetchall()


def stats() -> dict:
    with get_conn() as c:
        row = c.execute("SELECT COUNT(*) n, SUM(kind='saw') saw, SUM(kind='seen') seen, SUM(status='pending') pending, SUM(status='failed') failed FROM sightings").fetchone()
    return {k: row[k] or 0 for k in ("n", "saw", "seen", "pending", "failed")}


def clear_all() -> None:
    with get_conn() as c:
        c.execute("DELETE FROM sightings")
