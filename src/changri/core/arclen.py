"""弧长：房间里「对方写完一段，你隔了多久才回」。只算戏文（首行是角色名的发言），闲聊不算；只在正式档期里计。
两人房轮流计时，多人房各自计时（见 walk）。微信不计（作者 2026-11-27：微信不复盘、不记弧长）。
复盘页顶上按人给平均和最长，设了「最长弧长」后，超过的人和正在等的房间标红。NPC 不计入。"""
import time

from .db import get_conn, get_config


def max_hours() -> float:
    try:
        return max(0.0, float(get_config("arc_max_hours", "12") or 0))
    except ValueError:
        return 0.0


def fmt(h: float) -> str:
    if h < 1:
        return f"{max(1, round(h * 60))} 分钟"
    return f"{h:.1f} 小时" if h < 48 else f"{h / 24:.1f} 天"


def main_window() -> tuple[float, float] | None:
    """弧长只在正式档期里计：返回 (档期开始, 档期结束) 的时间戳；补戏期、超期回的戏只留场次，不算弧长。
    没有季度（或季度没写档期）返回 None = 不限。"""
    from . import season
    s = season.running() or (season.latest_ended() if get_config("season_wiped", "1") == "0" else None)
    if not s or not s["start_date"] or not s["end_date"]:
        return None
    try:
        a = time.mktime(time.strptime(s["start_date"], "%Y-%m-%d"))
        b = time.mktime(time.strptime(s["end_date"], "%Y-%m-%d")) + 86400
    except ValueError:
        return None
    return a, b


def walk(lines, members: list, npcs: set, window=None) -> tuple[list[tuple[int, float]], dict[int, int]]:
    """按戏文一条条走，算出每次回复的弧长。lines：这一场的戏文（只要认得出角色的），按时间排好。
    members：房间的参与者。两个人的房间轮流计时：对方说了第一句就开始等你，对方接着再说几句也不重新计；
    三个人以上各自计时：谁说了话，其余每个人都从这一句起重新计。
    回复落在正式档期外（window 之外）的不算。返回 ([(角色, 小时)], {还在被等的角色: 从什么时候开始等})。"""
    gaps, since = [], {}
    turn = len(members) == 2
    seen: list[int] = list(members)
    for ln in lines:
        me, ts = ln["role_id"], ln["ts"]
        if me in since:
            start = since.pop(me)
            if me not in npcs and (window is None or window[0] <= ts < window[1]):
                gaps.append((me, max(0, ts - start) / 3600))
        if me not in seen:
            seen.append(me)
        for other in seen:
            if other != me and (not turn or other not in since):
                since[other] = ts
    return gaps, since


def _room_lines(c, room_id: int):
    """这一场拿来算弧长的发言：只算戏文（首行是角色名的），闲聊不算。"""
    return c.execute("SELECT role_id, ts FROM room_lines WHERE room_id=? AND role_id IS NOT NULL AND is_rp=1 ORDER BY ts, id", (room_id,)).fetchall()


def _members(c, room_id: int) -> list[int]:
    return [m["role_id"] for m in c.execute("SELECT role_id FROM room_members WHERE room_id=? ORDER BY role_id", (room_id,))]


def waiting(c, room_id: int, npcs: set | None = None) -> tuple[dict[int, int], int | None]:
    """进行中的房间现在在等谁：返回 ({角色: 从什么时候开始等}, 最后一句戏文是谁说的)。还没人写戏文时两个都是空。"""
    lines = _room_lines(c, room_id)
    _g, since = walk(lines, _members(c, room_id), npcs or set())
    return since, (lines[-1]["role_id"] if lines else None)


def stats(now: float | None = None) -> dict:
    now = now or time.time()
    lim = max_hours()
    win = main_window()
    people: dict[int, dict] = {}
    waits = []
    with get_conn() as c:
        npcs = {r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=1")}
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        for room in c.execute("SELECT id, kind, status, created_ts FROM rooms WHERE kind!='微信' ORDER BY id").fetchall():     # 微信不记弧长
            mem = _members(c, room["id"])
            gaps, _since = walk(_room_lines(c, room["id"]), mem, npcs, win)
            for rid, h in gaps:
                people.setdefault(rid, {"name": names.get(rid, "?"), "gaps": []})["gaps"].append(h)
            if room["status"] == "open":
                since, last = waiting(c, room["id"], npcs)
                who = [m for m in mem if m not in npcs and m in names and (m in since if last is not None else True)]
                if who:
                    start = min(since[m] for m in who) if last is not None else room["created_ts"]
                    h = max(0, now - start) / 3600
                    waits.append({"room_id": room["id"], "kind": room["kind"], "who": "、".join(names[m] for m in who), "hours": h,
                                  "over": bool(lim and h > lim), "started": last is not None})
    out = []
    for rid, p in people.items():
        g = p["gaps"]
        avg, longest = sum(g) / len(g), max(g)
        out.append({"id": rid, "name": p["name"], "replies": len(g), "avg": avg, "longest": longest, "fastest": min(g),
                    "over": bool(lim and (avg > lim or longest > lim)), "avg_over": bool(lim and avg > lim)})
    out.sort(key=lambda x: (-x["avg"], x["name"]))
    waits.sort(key=lambda w: -w["hours"])
    return {"limit": lim, "people": out, "waits": waits,
            "over_people": sum(1 for p in out if p["over"]), "over_waits": sum(1 for w in waits if w["over"])}


def report(role_id: int, now: float | None = None) -> str:
    """「我的弧长」「查看弧长」：本人所有场次的平均回复耗时 + 每个进行中房间里双方回复次数、本场平均、现在轮到谁、等了多久。"""
    now = now or time.time()
    win = main_window()
    with get_conn() as c:
        me = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not me:
            return ""
        npcs = {r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=1")}
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        rooms = c.execute("SELECT r.id, r.kind, r.status, r.created_ts FROM rooms r JOIN room_members m ON m.room_id=r.id "
                          "WHERE m.role_id=? AND r.status!='cancelled' AND r.kind!='微信' ORDER BY r.id", (role_id,)).fetchall()
        all_gaps, played, live = [], 0, []
        for room in rooms:
            lines = _room_lines(c, room["id"])
            mem = _members(c, room["id"])
            g = [h for rid, h in walk(lines, mem, npcs, win)[0] if rid == role_id]
            all_gaps += g
            if any(ln["role_id"] == role_id for ln in lines):
                played += 1
            if room["status"] != "open":
                continue
            others = [m for m in mem if m != role_id]
            cnt = {m: sum(1 for ln in lines if ln["role_id"] == m) for m in mem}
            since, last = waiting(c, room["id"], npcs)
            mine_turn = last is None or role_id in since
            start = since.get(role_id) if last is not None and role_id in since else (min(since.values()) if since else (lines[-1]["ts"] if lines else room["created_ts"]))
            wait = fmt(max(0, now - start) / 3600)
            mine = f"本人本场平均 {fmt(sum(g) / len(g))}（{len(g)} 次）" if g else "本人本场还没有计时的回复"
            if len(others) == 1:
                o = others[0]
                turn = "你" if mine_turn else names.get(o, "?")
                waitline = f"你还没回：{wait}" if mine_turn else f"{names.get(o, '?')}未回：{wait}"
                live.append(f"R{room['id']} {room['kind']}：{me['name']}×{names.get(o, '?')} {cnt[role_id]}v{cnt[o]}（待{turn}），{mine}，{waitline}")
            else:
                state = f"你还没回：{wait}" if mine_turn else f"你说完了，等其他人回：{wait}"
                live.append(f"R{room['id']} {room['kind']}：{len(mem)} 人场，本场你说了 {cnt[role_id]} 次，{mine}，{state}")
    head = f"⏱️ 【{me['name']} 的弧长】\n"
    if all_gaps:
        head += f"总平均：{fmt(sum(all_gaps) / len(all_gaps))}（回复 {len(all_gaps)} 次，{played} 场，含已结束和进行中的）\n最慢一次：{fmt(max(all_gaps))}"
    else:
        head += "还没有计时的回复（只算戏文：别人写完一段、你接着回的那一段才算；补戏期和档期结束后的不算）。"
    lim = max_hours()
    if lim and all_gaps and sum(all_gaps) / len(all_gaps) > lim:
        head += f"\n⚠️ 平均已经超过最长弧长 {fmt(lim)}"
    body = ("\n\n进行中的房间：\n" + "\n".join(live)) if live else "\n\n现在没有进行中的房间。"
    return head + body
