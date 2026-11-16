"""约会房间：私约、电话、官约、微信、心愿约会、踩点（一个人的群，默认关）。房间 = 一个时间线条目 + 一个群池里的群。
时间说的是游戏里的「几点到几点」（1400-1500），不是真实时钟；房间真实存活时间由 room_expire_hours 管。"""
import json
import re
import time

from . import blocks, pool, rpg, season, windows
from .db import get_conn, get_config, get_int

ROOM_KINDS_GATED = ("私约", "电话", "微信")
KINDS = {"私约": True, "电话": False, "官约": True, "微信": False, "心愿": True, "前置电话": False, "踩点": True}   # 值：是否需要地点
NO_TIMELINE = {"微信", "前置电话"}                                                            # 微信不占时间线
SOLO_KINDS = {"踩点"}                                                                       # 只有发起人自己的房间


def stakeout_enabled() -> bool:
    """踩点开关（「功能与设置 → 约会房间」，默认关）。"""
    return get_config("stakeout_enabled", "0") == "1"


def parse_hm(text: str, day: int | None = None) -> tuple[str, str] | None:
    """「1400-1500」「14:00-15:00」→ ('14:00', '15:00')；结束必须晚于开始。"""
    from . import storycal                  # 故事历打开（且这个 D 填了日期）时，约时间只认「3月5日」这种某一天，不认钟点
    if storycal.exclusive(day):
        return storycal.parse_when(text, day)[0]
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})\s*[-~到至]\s*(\d{1,2}):?(\d{2})", text.strip())
    if not m:
        return None
    a, b = f"{int(m[1]):02d}:{m[2]}", f"{int(m[3]):02d}:{m[4]}"
    if not ("00:00" <= a <= "23:59" and "00:00" <= b <= "23:59") or int(m[2]) > 59 or int(m[4]) > 59 or a >= b:
        return None
    return a, b


def _overlap(a1: str, a2: str, b1: str, b2: str) -> bool:
    return a1 < b2 and b1 < a2


def get(room_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()


def members(room_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT m.*, r.name FROM room_members m JOIN roles r ON r.id=m.role_id WHERE m.room_id=? ORDER BY r.id", (room_id,)).fetchall()


def room_of_group(bot_id: str, group_openid: str):
    with get_conn() as c:
        return c.execute("SELECT r.* FROM rooms r JOIN pool_groups g ON g.id=r.pool_id WHERE g.bot_id=? AND g.group_openid=? AND r.status='open'",
                         (bot_id, group_openid)).fetchone()


def timeline(role_id: int, day: int | None = None) -> list:
    sql = ("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.status!='cancelled' "
           "AND r.kind NOT IN ('微信') AND r.day IS NOT NULL")
    args: list = [role_id]
    if day is not None:
        sql += " AND r.day=?"; args.append(day)
    with get_conn() as c:
        return c.execute(sql + " ORDER BY r.day, r.start_hm", args).fetchall()


def my_open(role_id: int) -> list:
    """我参与的、还在进行的房间（含微信），新的在前。"""
    with get_conn() as c:
        return c.execute("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.status='open' ORDER BY r.id DESC", (role_id,)).fetchall()


def is_member(room_id: int, role_id: int) -> bool:
    with get_conn() as c:
        return c.execute("SELECT 1 FROM room_members WHERE room_id=? AND role_id=?", (room_id, role_id)).fetchone() is not None


def conflict(role_id: int, day: int, a: str, b: str, ignore_room: int = 0, locks: bool = True):
    for r in timeline(role_id, day):
        if r["id"] != ignore_room and r["status"] == "open" and _overlap(a, b, r["start_hm"], r["end_hm"]):
            return r
    if locks:                                              # 上帝模式的时间锁定
        from . import godmode
        return godmode.lock_hit(role_id, day, a, b)
    return None


def open_phone_count(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.kind='电话' AND r.status='open'", (role_id,)).fetchone()[0]


def day_count(role_id: int, kind: str) -> int:
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM rooms WHERE creator_id=? AND kind=? AND dkey=? AND status!='cancelled'", (role_id, kind, season.day_key())).fetchone()[0]
    from . import godmode
    return max(0, n - godmode.offset(role_id, kind))


def daily_limit(kind: str) -> int:
    return get_int(f"limit_{kind}", 0)    # 0 = 不限


def web_mode() -> bool:
    """小手机开着 + 聊天室开关开着：新约会一律走网页聊天室，不分配 QQ 群。"""
    from . import extensions
    return extensions.is_enabled("phone") and extensions.is_enabled("chatroom")


def create(kind: str, creator_id: int, target_ids: list[int], time_text: str, place: str = "", note: str = "",
           official: bool = False, day: int | None = None) -> tuple[bool, str, int]:
    """发起一个房间。返回 (成功, 提示或房间号, room_id)。
    成功时提示是「R房间号」；多人邀约里有人拉黑了发起人，会被悄悄剔掉，提示后面多一行「\n…」告诉发起人谁没加进来（对方设了静默的不提）。"""
    if kind not in KINDS:
        return False, "不认识的类型", 0
    if kind in SOLO_KINDS:
        if not official and not stakeout_enabled():
            return False, "踩点没有开放。", 0
        if target_ids:
            return False, "踩点是一个人的群，不能带别人。", 0
        if not place.strip():
            return False, "踩点要写地点。", 0
    closed = "" if official else windows.check("date")
    if closed:
        return False, closed, 0
    if kind != "微信":
        if season.phase() != "playing":
            return False, "现在不是进行中的季度（筹备期/没有季度时不能约）。", 0
        hm = parse_hm(time_text, day)
        if not hm:
            from . import storycal
            return False, (storycal.parse_when(time_text, day)[1] or storycal.format_error(day)), 0
    else:
        hm = ("", "")
    ids = list(dict.fromkeys(target_ids))
    if creator_id in ids:
        return False, "不能约自己。", 0
    if kind in ROOM_KINDS_GATED and not official:                 # 上帝模式：功能权限（发起、被邀请都算）
        from . import godmode
        with get_conn() as c:
            nm = {r["id"]: r["name"] for r in c.execute(f"SELECT id, name FROM roles WHERE id IN ({','.join('?' * (len(ids) + 1))})", ids + [creator_id])}
        for rid_ in [creator_id] + ids:
            bad = godmode.check(rid_, kind, "你" if rid_ == creator_id else f"「{nm.get(rid_, '对方')}」")
            if bad:
                return False, bad.replace("🚫 ", "").replace("暂时关闭", "暂时关闭，不能参与"), 0
    if not ids and kind not in SOLO_KINDS:
        return False, "至少要有一位对象。", 0
    if day is not None and day < 1:
        return False, "D几 要写成大于 0 的数字。", 0
    day = (day if day is not None else season.current_day()) if kind != "微信" else None      # 官约可以指定 D几（预排以后的天）
    with get_conn() as c:
        names = {r["id"]: r["name"] for r in c.execute(f"SELECT id, name FROM roles WHERE id IN ({','.join('?' * (len(ids) + 1))})", ids + [creator_id])}
    if KINDS[kind] and place:
        allowed, reason = rpg.check_place(creator_id, place)
        if not allowed:
            return False, reason, 0
    refused = []
    if not official:
        lim = daily_limit(kind)
        if lim and day_count(creator_id, kind) >= lim:
            return False, f"今天的{kind}次数用完了（每天 {lim} 次）。", 0
        single = len(ids) == 1
        for t in list(ids):
            b = blocks.get_block(t, creator_id)
            if b:
                if single:                # 单人邀约：直接失败，不建群（群是有限的资源，不能拿来演戏）
                    return False, ("发起失败，请稍后再试。" if b["silent"] else f"「{names.get(t, '对方')}」已拒绝你的联系。"), 0
                ids.remove(t)             # 多人邀约：把拉黑了你的人剔掉，其余人照常
                if not b["silent"]:
                    refused.append(names.get(t, "对方"))
        if not ids and kind not in SOLO_KINDS:
            return False, "邀约对象都不可用，请检查名单或稍后再试。", 0
        if kind == "电话":
            cap = get_int("phone_open_cap", 5)
            if cap and open_phone_count(creator_id) >= cap:
                return False, f"你有 {cap} 个电话还没结束，先结束几个再打。", 0
    if kind not in NO_TIMELINE:
        for rid in [creator_id] + ids:
            hit = conflict(rid, day, hm[0], hm[1], locks=not official)
            if hit:
                who = "你" if rid == creator_id else f"「{names.get(rid, '对方')}」"
                return False, f"{who}在 D{day} {hit['start_hm']}-{hit['end_hm']} 已经有安排（{hit['kind']}），时间撞了。", 0
    with get_conn() as c:
        cur = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, note, dkey, created_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                        (kind, creator_id, day, hm[0], hm[1], place, note, season.day_key(), int(time.time())))
        rid_ = cur.lastrowid
        c.execute("INSERT INTO room_members(room_id, role_id, agree) VALUES(?,?,1)", (rid_, creator_id))
        for t in ids:
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid_, t))
    if kind != "微信" and not web_mode():                                            # 聊天室开着：约会在小手机网页里聊，不占 QQ 群
        pool.allocate(rid_)
    try:                                                          # 目击系统（额外扩展）：关着就什么都不做
        from . import sighting
        sighting.detect(rid_)
    except Exception:  # noqa: BLE001 目击出错不能让约会建不成
        pass
    note_refused = f"\n⚠️ {'、'.join(refused)} 已拒绝你的联系，没有加入这次邀约。" if refused else ""
    return True, f"R{rid_}{note_refused}", rid_


def create_call(creator_id: int, target_id: int, note: str = "") -> int:
    """前置电话拨通：不看季度、不占时间线，直接开房间（网页聊天室开着就不占 QQ 群，否则群池有空群就分群，没有排队/走网页）。"""
    with get_conn() as c:
        rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, note, dkey, created_ts) VALUES('前置电话',?,NULL,'','','',?,?,?)",
                        (creator_id, note, season.day_key(), int(time.time()))).lastrowid
        c.execute("INSERT INTO room_members(room_id, role_id, agree) VALUES(?,?,1)", (rid, creator_id))
        c.execute("INSERT INTO room_members(room_id, role_id, agree) VALUES(?,?,1)", (rid, target_id))
    if not web_mode():
        pool.allocate(rid)
    return rid


def join(room_id: int, role_id: int) -> tuple[bool, str]:
    r = get(room_id)
    if not r or r["status"] != "open":
        return False, "这个房间已经结束了。"
    if r["kind"] not in NO_TIMELINE and conflict(role_id, r["day"], r["start_hm"], r["end_hm"]):
        return False, "这个时间你已经有安排了。"
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO room_members(room_id, role_id, agree) VALUES(?,?,1)", (room_id, role_id))
    return True, ""


def mark_entered(room_id: int, role_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE room_members SET entered_ts=COALESCE(entered_ts, ?) WHERE room_id=? AND role_id=?", (int(time.time()), room_id, role_id))


def _mark_here(room, here: list[int]) -> dict | None:
    mem = members(room["id"])
    if not here:
        return None
    was_all = all(m["entered_ts"] for m in mem)
    for rid in here:
        mark_entered(room["id"], rid)
    return {"room_id": room["id"], "complete": not was_all and all(m["entered_ts"] or m["role_id"] in here for m in mem) and len(mem) > 1}


def note_qq_speech(bot_id: str, gid: str, qq: str) -> dict | None:
    """QQ 号听到房间群（只有群号的群）里有人说话：这个 QQ 号认过角色、又是房间的参与者，就算到场。
    没标记返回 None；标记了返回 {"room_id", "complete"}，complete 表示这一次正好让所有人都到齐了（只会有一次）。"""
    from . import roles
    room = room_of_group(bot_id, pool.QQ_PREFIX + gid)
    if not room:
        return None
    mine = {r["id"] for r in roles.roles_by_qq(qq)}                      # 这个人名下的所有角色
    return _mark_here(room, [m["role_id"] for m in members(room["id"]) if m["role_id"] in mine])


def note_presence(room, qqs: set[str]) -> dict | None:
    """房间群的成员名单（QQ 号集合）：名单里认过角色的参与者都算到场，不用等他们发言。返回值同 note_qq_speech。"""
    from . import roles
    mine: set[int] = set()
    for q in qqs:
        mine.update(r["id"] for r in roles.roles_by_qq(q))
    return _mark_here(room, [m["role_id"] for m in members(room["id"]) if m["role_id"] in mine])


def group_info(room) -> tuple[str, str, str] | None:
    """(bot_id, group_openid, 给玩家看的群描述)；没领到群返回 None。"""
    if not room["pool_id"]:
        return None
    g = pool.get(room["pool_id"])
    if not g:
        return None
    desc = (f"群号 {g['real_gid']}" if g["real_gid"] else "（管理员还没填群号，请联系管理员）") + (f"（{g['label']}）" if g["label"] else "")
    return g["bot_id"], g["group_openid"], desc


def end(room_id: int, status: str = "ended") -> dict | None:
    """结束房间：群直接放回池里（下次分配时再查群里有没有玩家）。成功给占用的群返回信息，没群返回 None。"""
    r = get(room_id)
    if not r or r["status"] != "open":
        return None
    with get_conn() as c:
        c.execute("UPDATE rooms SET status=?, ended_ts=?, pending_change='' WHERE id=?", (status, int(time.time()), room_id))
    if r["pool_id"]:
        pool.release(r["pool_id"], "free" if get_config("pool_auto_free", "1") == "1" else "cleaning")
        return dict(pool.get(r["pool_id"]))
    return None


def assign_waiting() -> list[int]:
    """有空群了：给排队的房间按先后补群。返回拿到群的房间 id。"""
    got = []
    with get_conn() as c:
        wait = [r["id"] for r in c.execute("SELECT id FROM rooms WHERE status='open' AND pool_id IS NULL ORDER BY id")]
    for rid in wait:
        if pool.allocate(rid):
            got.append(rid)
        else:
            break
    return got


def open_rooms() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM rooms WHERE status='open' AND pool_id IS NOT NULL").fetchall()


def expire_enabled() -> bool:
    """「到时间强制结群」：默认关。关着时房间不会因为开久了被自动结束。"""
    return get_config("room_expire_enabled", "0") == "1" and get_int("room_expire_hours", 24) > 0


def expired(now: int | None = None) -> list:
    if not expire_enabled():
        return []
    now = int(now or time.time())
    with get_conn() as c:
        return c.execute("SELECT * FROM rooms WHERE status='open' AND created_ts<?", (now - get_int("room_expire_hours", 24) * 3600,)).fetchall()


def expiring_soon(now: int | None = None) -> list:
    """快到结戏限时、还没提醒过的房间（提前 room_expire_warn_hours 小时提醒一次）。"""
    hours, warn = get_int("room_expire_hours", 24), get_int("room_expire_warn_hours", 4)
    if not expire_enabled() or warn <= 0:
        return []
    now = int(now or time.time())
    lead = max(hours - warn, 0) * 3600
    with get_conn() as c:
        return c.execute("SELECT * FROM rooms WHERE status='open' AND expire_warned=0 AND created_ts<? AND created_ts>=?",
                         (now - lead, now - hours * 3600)).fetchall()


def mark_expire_warned(room_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE rooms SET expire_warned=1 WHERE id=?", (room_id,))


# ---------- 修改时间线（需要房间里所有人同意）----------

def propose_change(room_id: int, by_id: int, day: int, a: str, b: str) -> tuple[bool, str]:
    r = get(room_id)
    if not r or r["status"] != "open" or r["kind"] in NO_TIMELINE:
        return False, "这个房间没有时间线可改。"
    for m in members(room_id):
        hit = conflict(m["role_id"], day, a, b, ignore_room=room_id)
        if hit:
            return False, f"「{m['name']}」在 D{day} {hit['start_hm']}-{hit['end_hm']} 已有安排，改不了。"
    with get_conn() as c:
        c.execute("UPDATE rooms SET pending_change=? WHERE id=?", (json.dumps({"day": day, "a": a, "b": b, "by": by_id}), room_id))
        c.execute("UPDATE room_members SET agree=CASE WHEN role_id=? THEN 1 ELSE 0 END WHERE room_id=?", (by_id, room_id))
    return True, ""


def vote_change(room_id: int, role_id: int, agree: bool) -> tuple[str, dict | None]:
    """返回 (状态, 申请内容)：none 没有申请 / rejected / waiting / applied"""
    r = get(room_id)
    if not r or not r["pending_change"]:
        return "none", None
    ch = json.loads(r["pending_change"])
    if not agree:
        with get_conn() as c:
            c.execute("UPDATE rooms SET pending_change='' WHERE id=?", (room_id,))
        return "rejected", ch
    with get_conn() as c:
        c.execute("UPDATE room_members SET agree=1 WHERE room_id=? AND role_id=?", (room_id, role_id))
        left = c.execute("SELECT COUNT(*) FROM room_members WHERE room_id=? AND agree=0", (room_id,)).fetchone()[0]
        if left:
            return "waiting", ch
        c.execute("UPDATE rooms SET day=?, start_hm=?, end_hm=?, pending_change='' WHERE id=?", (ch["day"], ch["a"], ch["b"], room_id))
    return "applied", ch


# ---------- 申请加入别人正在进行的约会 ----------

def find_joinable(target_id: int, start_hm: str = "") -> list:
    """对方现在参与的、可以申请加入的房间（私约/官约/心愿，不含电话和微信）。"""
    with get_conn() as c:
        rs = c.execute("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.status='open' "
                       "AND r.kind IN ('私约','官约','心愿') ORDER BY r.id", (target_id,)).fetchall()
    if start_hm:
        a = parse_hm(f"{start_hm}-23:59") if re.fullmatch(r"\d{1,2}:?\d{2}", start_hm) else None
        rs = [r for r in rs if a and r["start_hm"] == a[0]]
    return rs


def request_join(room_id: int, role_id: int) -> tuple[bool, str, int]:
    r = get(room_id)
    if not r or r["status"] != "open":
        return False, "这个房间已经结束了。", 0
    if any(m["role_id"] == role_id for m in members(room_id)):
        return False, "你已经在这个房间里了。", 0
    for m in members(room_id):
        b = blocks.get_block(m["role_id"], role_id)
        if b:
            return False, ("申请失败，请稍后再试。" if b["silent"] else f"「{m['name']}」已拒绝你的联系。"), 0
    hit = conflict(role_id, r["day"], r["start_hm"], r["end_hm"])
    if hit:
        return False, f"你在 D{r['day']} {hit['start_hm']}-{hit['end_hm']} 已经有安排了。", 0
    with get_conn() as c:
        old = c.execute("SELECT id FROM join_requests WHERE room_id=? AND role_id=? AND status='pending'", (room_id, role_id)).fetchone()
        if old:
            return False, "你已经申请过了，等对方回复。", 0
        return True, "", c.execute("INSERT INTO join_requests(room_id, role_id, created_ts) VALUES(?,?,?)", (room_id, role_id, int(time.time()))).lastrowid


def pending_requests(role_id: int) -> list:
    """别人想加入我所在房间的申请。"""
    with get_conn() as c:
        return c.execute("SELECT q.*, a.name AS applicant, r.kind, r.day, r.start_hm, r.end_hm FROM join_requests q "
                         "JOIN rooms r ON r.id=q.room_id JOIN roles a ON a.id=q.role_id "
                         "JOIN room_members m ON m.room_id=q.room_id AND m.role_id=? "
                         "WHERE q.status='pending' AND r.status='open' ORDER BY q.id", (role_id,)).fetchall()


def answer_request(req_id: int, role_id: int, accept: bool) -> tuple[bool, str, dict | None]:
    with get_conn() as c:
        q = c.execute("SELECT * FROM join_requests WHERE id=?", (req_id,)).fetchone()
        if not q or q["status"] != "pending":
            return False, "没有这条待处理的申请。", None
        if not c.execute("SELECT 1 FROM room_members WHERE room_id=? AND role_id=?", (q["room_id"], role_id)).fetchone():
            return False, "这条申请不是发给你的。", None
    if accept:
        ok, why = join(q["room_id"], q["role_id"])
        if not ok:
            return False, why, None
    with get_conn() as c:
        c.execute("UPDATE join_requests SET status=? WHERE id=?", ("accepted" if accept else "rejected", req_id))
    return True, "", dict(q)
