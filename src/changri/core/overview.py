"""时间线和「我的待回」的内容。QQ 指令和小手机网页共用这一份，两边看到的永远一样。"""
from . import pending, rooms
from .db import get_conn


def slot_text(r) -> str:
    if r["kind"] in rooms.NO_TIMELINE:
        return "（不占时间线）"
    return f"D{r['day']} {r['start_hm']}-{r['end_hm']}" + (f" @{r['place']}" if r["place"] else "")


def group_line(room) -> str:
    gi = rooms.group_info(room)
    if not gi and not room["pool_id"] and room["status"] == "open" and rooms.web_mode():
        return "这次约会在小手机的聊天室里进行：小手机 → 发现 → 邀约 → 进入聊天室。"
    if gi:
        return f"请申请入群：{gi[2]}"
    return "群池暂时没有空群，已排队，轮到会再通知你们。"


def timeline_days(role_id: int) -> list[dict]:
    """[{day, items:[{time, kind, place, others, room, status}]}]，按天分组。"""
    out: list[dict] = []
    for r in rooms.timeline(role_id):
        if not out or out[-1]["day"] != r["day"]:
            out.append({"day": r["day"], "items": []})
        others = "、".join(m["name"] for m in rooms.members(r["id"]) if m["role_id"] != role_id)
        out[-1]["items"].append({"time": f"{r['start_hm']}-{r['end_hm']}", "kind": r["kind"], "place": r["place"] or "", "others": others,
                                 "room": r["id"], "status": {"open": "进行中", "ended": "已结束"}.get(r["status"], r["status"])})
    return out


def todo_items(role) -> list[str]:
    items: list[str] = []
    with get_conn() as c:
        open_rooms = c.execute("SELECT r.*, m.entered_ts, m.agree FROM rooms r JOIN room_members m ON m.room_id=r.id "
                               "WHERE m.role_id=? AND r.status='open' ORDER BY r.id", (role["id"],)).fetchall()
    for r in open_rooms:
        if not r["pool_id"]:
            items.append(f"R{r['id']} {r['kind']}（{slot_text(r)}）：群池没有空群，还在排队")
        elif not r["entered_ts"]:
            items.append(f"R{r['id']} {r['kind']}（{slot_text(r)}）：你还没进群发言，{group_line(r)}")
        else:
            items.append(f"R{r['id']} {r['kind']}（{slot_text(r)}）：进行中，聊完记得「结束{r['kind']}」")
        if r["pending_change"] and not r["agree"]:
            items.append(f"R{r['id']} 有人想改时间线，在那个群里发「同意时间线」或「拒绝时间线」")
    for q in rooms.pending_requests(role["id"]):
        items.append(f"{q['applicant']} 想加入 R{q['room_id']}，发「同意加入 {q['id']}」或「拒绝加入 {q['id']}」")
    return items + pending.collect(role)
