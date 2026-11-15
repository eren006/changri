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
