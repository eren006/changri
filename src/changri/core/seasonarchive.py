"""历史季度档案：结束 / 收尾季度时，把本季的统计和房间复盘存成一份快照，收尾清空数据后还能回看。

summary（JSON）：角色名单、各项数量、每个玩家的数据；rooms（gzip JSON）：每场房间的参与者和全部发言。
同一季重复存会覆盖（结束时存一次，收尾前再存一次，取最后一份）。
"""
import gzip
import json
import time

from .db import get_conn
from .textutil import hm_text


def _stats_for(rid: int) -> dict:
    from . import seasonreport
    try:
        return seasonreport.stats(rid)
    except Exception:  # noqa: BLE001 单个玩家算不出来不影响整份存档
        return {}


def _day_label(day, dkey: str) -> tuple[int, str]:
    """约好的是第几天就算第几天；没有的按 dkey（「S3D5.0」或日期），和 roomlog.events 一样。"""
    from .roomlog import _day_of
    return (day, f"D{day}") if day is not None else _day_of(dkey or "")


def _events(c) -> list[dict]:
    """本季的短信、礼物（撤回的、没发出去的不算）和已经送达的心动信。心动信只记署名，不记真实寄信人。"""
    from .roomlog import _day_of
    out = []
    for m in c.execute("SELECT m.*, f.name AS fn, t.name AS tn FROM messages m JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
                       "WHERE m.error!='已撤回' AND m.status!='failed' ORDER BY m.id"):
        num, label = _day_of(m["day"])
        out.append({"type": m["kind"], "ts": m["created_ts"], "day_num": num, "day_label": label,
                    "frm": m["sign"] or m["fn"], "to": m["tn"], "title": m["title"], "content": m["content"]})
    for m in c.execute("SELECT l.*, t.name AS tn FROM lovemails l JOIN roles t ON t.id=l.to_role_id WHERE l.status='sent' ORDER BY l.id"):
        num, label = _day_of(m["day"])
        out.append({"type": "lovemail", "ts": m["sent_ts"] or m["created_ts"], "day_num": num, "day_label": label,
                    "frm": m["signature"] or "匿名", "to": m["tn"], "title": "", "content": m["content"]})
    return out


def snapshot(season_id: int) -> dict:
    """把现在库里的本季数据存成档案；返回 summary。"""
    with get_conn() as c:
        roles = [dict(r) for r in c.execute("SELECT id, name, is_npc FROM roles ORDER BY id")]
        q = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        counts = {
            "roles": sum(1 for r in roles if not r["is_npc"]), "npcs": sum(1 for r in roles if r["is_npc"]),
            "sms": q("SELECT COUNT(*) FROM messages WHERE kind='sms' AND status!='failed'"),
            "gift": q("SELECT COUNT(*) FROM messages WHERE kind='gift' AND status!='failed'"),
            "lovemail": q("SELECT COUNT(*) FROM lovemails"),
            "relations": q("SELECT COUNT(*) FROM relations"),
            "posts": q("SELECT COUNT(*) FROM posts"),
        }
        chars = {r["role_id"]: r["n"] for r in c.execute("SELECT role_id, SUM(chars) AS n FROM room_lines WHERE is_rp=1 AND role_id IS NOT NULL GROUP BY role_id")}
        rooms = []
        for r in c.execute("SELECT * FROM rooms WHERE status!='cancelled' ORDER BY id").fetchall():
            mem = [m["name"] for m in c.execute("SELECT ro.name FROM room_members m JOIN roles ro ON ro.id=m.role_id WHERE m.room_id=? ORDER BY ro.id", (r["id"],))]
            lines = [{"ts": ln["ts"], "name": ln["name"], "text": ln["text"], "rp": ln["is_rp"], "chars": ln["chars"]}
                     for ln in c.execute("SELECT ts, name, text, is_rp, chars FROM room_lines WHERE room_id=? ORDER BY ts, id", (r["id"],))]
            num, label = _day_label(r["day"], r["dkey"])
            rooms.append({"id": r["id"], "kind": r["kind"], "day": r["day"], "start_hm": r["start_hm"], "end_hm": r["end_hm"], "place": r["place"],
                          "status": r["status"], "created_ts": r["created_ts"], "ended_ts": r["ended_ts"], "day_num": num, "day_label": label,
                          "members": mem, "lines": lines})
        events = _events(c)
    counts["rooms"] = sum(1 for r in rooms if r["status"] == "ended")
    counts["lines"] = sum(len(r["lines"]) for r in rooms)
    players = []
    for r in roles:
        st = _stats_for(r["id"])
        players.append({"name": r["name"], "npc": bool(r["is_npc"]), "chars": chars.get(r["id"], 0),
                        **{k: st.get(k, 0) for k in ("sessions", "sms_sent", "sms_recv", "gift_sent", "gift_recv", "lovemail_sent", "lovemail_recv")},
                        "time_title": st.get("time_title", ""), "report": st})          # report：季末海报要的完整数据，以后还能重画、下载
    summary = {"counts": counts, "players": players, "events": events}      # events：复盘网页（core/seasonview.py）的短信、礼物、心动信
    blob = gzip.compress(json.dumps(rooms, ensure_ascii=False).encode("utf-8"))
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO season_archives(season_id, created_ts, summary, rooms) VALUES(?,?,?,?)",
                  (season_id, int(time.time()), json.dumps(summary, ensure_ascii=False), blob))
    return summary


def get(season_id: int) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM season_archives WHERE season_id=?", (season_id,)).fetchone()
    if not r:
        return None
    return {"created_ts": r["created_ts"], "summary": json.loads(r["summary"] or "{}"),
            "rooms": json.loads(gzip.decompress(r["rooms"]).decode("utf-8")) if r["rooms"] else []}


def archived_ids() -> set[int]:
    with get_conn() as c:
        return {r[0] for r in c.execute("SELECT season_id FROM season_archives")}


def room_text(season_name: str, room: dict) -> str:
    head = [f"{season_name} · R{room['id']} {room['kind']}　" + (f"D{room['day']} " if room["day"] is not None else "") + f"{hm_text(room['start_hm'], room['end_hm'])} {room['place']}".strip(),
            "参与者：" + "、".join(room["members"]), ""]
    from .roomlog import body_of
    body = [f"[{time.strftime('%H:%M', time.localtime(ln['ts']))}] {ln['name']}：{body_of(ln['name'], ln['text'])}" + ("" if ln["rp"] else "（闲聊）") for ln in room["lines"]]
    return "\n".join(head + body)
