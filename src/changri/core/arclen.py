"""弧长：房间里「对方说完，你隔了多久才回」。从房间群里监听到的发言算（只有认得出是哪个角色的发言）。
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


def stats(now: float | None = None) -> dict:
    now = now or time.time()
    lim = max_hours()
    people: dict[int, dict] = {}
    waits = []
    with get_conn() as c:
        npcs = {r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=1")}
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        for room in c.execute("SELECT id, kind, status, created_ts FROM rooms ORDER BY id").fetchall():
            lines = c.execute("SELECT role_id, ts FROM room_lines WHERE room_id=? AND role_id IS NOT NULL ORDER BY ts, id", (room["id"],)).fetchall()
            prev = None
            for ln in lines:
                if prev and ln["role_id"] != prev["role_id"] and ln["role_id"] not in npcs:
                    p = people.setdefault(ln["role_id"], {"name": names.get(ln["role_id"], "?"), "gaps": []})
                    p["gaps"].append(max(0, ln["ts"] - prev["ts"]) / 3600)
                prev = ln
            if room["status"] == "open":
                mem = [m["role_id"] for m in c.execute("SELECT role_id FROM room_members WHERE room_id=?", (room["id"],))]
                since = prev["ts"] if prev else room["created_ts"]
                who = [names[m] for m in mem if m != (prev["role_id"] if prev else None) and m not in npcs and m in names]
                if who:
                    h = max(0, now - since) / 3600
                    waits.append({"room_id": room["id"], "kind": room["kind"], "who": "、".join(who), "hours": h, "over": bool(lim and h > lim),
                                  "started": prev is not None})
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


def _gaps(lines, role_id: int, npcs: set) -> list[float]:
    """一场里 role_id 每次接在别人后面回复隔了多久（小时）。"""
    out, prev = [], None
    for ln in lines:
        if prev and ln["role_id"] == role_id and prev["role_id"] != role_id and role_id not in npcs:
            out.append(max(0, ln["ts"] - prev["ts"]) / 3600)
        prev = ln
    return out


def report(role_id: int, now: float | None = None) -> str:
    """「我的弧长」「查看弧长」：本人所有场次的平均回复耗时 + 每个进行中房间里双方回复次数、本场平均、现在轮到谁、等了多久。"""
    now = now or time.time()
    with get_conn() as c:
        me = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not me:
            return ""
        npcs = {r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=1")}
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        rooms = c.execute("SELECT r.id, r.kind, r.status, r.created_ts FROM rooms r JOIN room_members m ON m.room_id=r.id "
                          "WHERE m.role_id=? AND r.status!='cancelled' ORDER BY r.id", (role_id,)).fetchall()
        all_gaps, played, live = [], 0, []
        for room in rooms:
            lines = c.execute("SELECT role_id, ts FROM room_lines WHERE room_id=? AND role_id IS NOT NULL ORDER BY ts, id", (room["id"],)).fetchall()
            g = _gaps(lines, role_id, npcs)
            all_gaps += g
            if any(ln["role_id"] == role_id for ln in lines):
                played += 1
            if room["status"] != "open":
                continue
            mem = [m["role_id"] for m in c.execute("SELECT role_id FROM room_members WHERE room_id=? ORDER BY role_id", (room["id"],))]
            others = [m for m in mem if m != role_id]
            cnt = {m: sum(1 for ln in lines if ln["role_id"] == m) for m in mem}
            last = lines[-1] if lines else None
            since = last["ts"] if last else room["created_ts"]
            wait = fmt(max(0, now - since) / 3600)
            mine = f"本人本场平均 {fmt(sum(g) / len(g))}（{len(g)} 次）" if g else "本人本场还没有计时的回复"
            if len(others) == 1:
                o = others[0]
                turn = "你" if (last is None or last["role_id"] != role_id) else names.get(o, "?")
                waitline = f"你还没回：{wait}" if turn == "你" else f"{names.get(o, '?')}未回：{wait}"
                live.append(f"R{room['id']} {room['kind']}：{me['name']}×{names.get(o, '?')} {cnt[role_id]}v{cnt[o]}（待{turn}），{mine}，{waitline}")
            else:
                state = f"你还没回：{wait}" if (last is None or last["role_id"] != role_id) else f"你说完了，等其他人回：{wait}"
                live.append(f"R{room['id']} {room['kind']}：{len(mem)} 人场，本场你说了 {cnt[role_id]} 次，{mine}，{state}")
    head = f"⏱️ 【{me['name']} 的弧长】\n"
    if all_gaps:
        head += f"总平均：{fmt(sum(all_gaps) / len(all_gaps))}（回复 {len(all_gaps)} 次，{played} 场，含已结束和进行中的）\n最慢一次：{fmt(max(all_gaps))}"
    else:
        head += "还没有计时的回复（别人说完你接着回的那一句才算）。"
    lim = max_hours()
    if lim and all_gaps and sum(all_gaps) / len(all_gaps) > lim:
        head += f"\n⚠️ 平均已经超过最长弧长 {fmt(lim)}"
    body = ("\n\n进行中的房间：\n" + "\n".join(live)) if live else "\n\n现在没有进行中的房间。"
    return head + body
