"""时间线和「我的待回」的内容。QQ 指令和小手机网页共用这一份，两边看到的永远一样。"""
from . import pending, rooms, storycal
from .db import get_conn


def slot_text(r) -> str:
    if r["kind"] in rooms.NO_TIMELINE:
        return "（不占时间线）"
    story = storycal.label_for_slot(r["day"], r["start_hm"])          # 故事历开着：D3（康熙十四年3月5日）
    return f"D{r['day']}" + (f"（{story}）" if story else "") + f" {r['start_hm']}-{r['end_hm']}" + (f" @{r['place']}" if r["place"] else "")


def group_line(room) -> str:
    gi = rooms.group_info(room)
    if not gi and not room["pool_id"] and room["status"] == "open" and rooms.web_mode():
        return "这次约会在小手机的聊天室里进行：小手机 → 发现 → 邀约 → 进入聊天室。"
    if gi:
        return f"请申请入群：{gi[2]}"
    return "备用群池暂时没有空群，请稍后再试。"


def _story_span(day) -> str:
    line = storycal.stage_line(day)
    return line.split(" = ", 1)[-1] if line else ""


def timeline_days(role_id: int) -> list[dict]:
    """[{day, items:[{time, kind, place, others, room, status}]}]，按天分组。"""
    out: list[dict] = []
    for r in rooms.timeline(role_id):
        if not out or out[-1]["day"] != r["day"]:
            out.append({"day": r["day"], "items": [], "story": _story_span(r["day"])})
        others = "、".join(m["name"] for m in rooms.members(r["id"]) if m["role_id"] != role_id)
        out[-1]["items"].append({"time": f"{r['start_hm']}-{r['end_hm']}", "kind": r["kind"], "place": r["place"] or "", "others": others,
                                 "room": r["id"], "status": {"open": "进行中", "ended": "已结束"}.get(r["status"], r["status"]),
                                 "story": storycal.label_for_slot(r["day"], r["start_hm"])})
    return out


def fmt_wait(seconds: float) -> str:
    m = max(0, int(seconds)) // 60
    return f"{m // 60}小时{m % 60}分钟" if m >= 60 else f"{max(1, m)}分钟"


def todo_items(role, lingering: list | None = None, now: float | None = None) -> list[str]:
    return [t for _k, t in todo_entries(role, lingering, now)]


def todo_entries(role, lingering: list | None = None, now: float | None = None) -> list[tuple[str, str]]:
    """「我的待回」（照老系统的顺序）：先是轮到你回的场次（超过「最长弧长」的标 🔴 置顶，写清上一句是谁说的、等了多久），
    再是其他进行中的房间、要你处理的改时间/加入申请、已结束还没退的 QQ 小号群（lingering，由调用方异步查好传进来），
    最后是各扩展注册的（关系线、心动信）。"""
    import time as _time

    from . import arclen
    now = now or _time.time()
    lim_h = arclen.max_hours()
    turn: list[tuple[bool, float, str, str]] = []
    items: list[tuple[str, str]] = []
    with get_conn() as c:
        open_rooms = c.execute("SELECT r.*, m.entered_ts, m.agree FROM rooms r JOIN room_members m ON m.room_id=r.id "
                               "WHERE m.role_id=? AND r.status='open' ORDER BY r.id", (role["id"],)).fetchall()
        names = {x["id"]: x["name"] for x in c.execute("SELECT id, name FROM roles")}
        for r in open_rooms:
            last = c.execute("SELECT role_id, ts FROM room_lines WHERE room_id=? AND role_id IS NOT NULL ORDER BY ts DESC, id DESC LIMIT 1",
                             (r["id"],)).fetchone()
            head = f"R{r['id']} {r['kind']}（{slot_text(r)}）"
            if not r["pool_id"] and not rooms.web_mode():
                items.append((f"g:{r['id']}", f"{head}：备用群池没有空群，还在等群"))
            elif last and last["role_id"] != role["id"]:
                wait = now - last["ts"]
                over = bool(lim_h and wait > lim_h * 3600)
                tail = "" if r["entered_ts"] else f"\n　⚠️ 你好像还没进这个群，{group_line(r)}"
                turn.append((over, wait, f"t:{r['id']}:{last['ts']}",
                             f"{'🔴 已超时' if over else '⏳'} {head}：轮到你回（{names.get(last['role_id'], '对方')} 说完到现在 {fmt_wait(wait)}）{tail}"))
            elif not r["entered_ts"] and r["pool_id"]:
                items.append((f"e:{r['id']}", f"{head}：你还没进群发言，{group_line(r)}"))
            elif last:
                others = "、".join(m["name"] for m in rooms.members(r["id"]) if m["role_id"] != role["id"]) or "对方"
                items.append((f"w:{r['id']}:{last['ts']}", f"{head}：你说完了，等 {others} 回（已经等了 {fmt_wait(now - last['ts'])}）"))
            else:
                items.append((f"n:{r['id']}", f"{head}：进行中，还没有人说话，聊完记得「结束{r['kind']}」"))
            if r["pending_change"] and not r["agree"]:
                items.append((f"c:{r['id']}:{r['pending_change']}", f"R{r['id']} 有人想改时间线，发「同意时间线 R{r['id']}」或「拒绝时间线 R{r['id']}」（在那个房间群里可以不带房间号）"))
    for q in rooms.pending_requests(role["id"]):
        items.append((f"j:{q['id']}", f"{q['applicant']} 想加入 R{q['room_id']}，发「同意加入 {q['id']}」或「拒绝加入 {q['id']}」"))
    turn.sort(key=lambda x: (not x[0], -x[1]))
    linger = [x if isinstance(x, tuple) else (f"l:{x}", x) for x in (lingering or [])]
    return [(t[2], t[3]) for t in turn] + items + linger + pending.collect_entries(role)


LINGER_DAYS = 7


async def lingering(role, now: float | None = None) -> list[tuple[str, str]]:
    """已经结束、但你好像还没退的群：只看 QQ 小号管的群（小号能查群成员；官方机器人查不了）。
    看最近 7 天结束的房间，同一个群只看最后一场；这个群现在开着的房间里有你（又约到同一个群）就不算。"""
    import time as _time

    from . import pool, qqmanager, roles as roles_core
    now = now or _time.time()
    qqs = set(roles_core.qqs_of_role(role["id"]))
    if not qqs:
        return []
    with get_conn() as c:
        ended = c.execute("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.status='ended' "
                          "AND r.pool_id IS NOT NULL AND r.ended_ts>=? ORDER BY r.ended_ts DESC", (role["id"], int(now - LINGER_DAYS * 86400))).fetchall()
    seen, out = set(), []
    for r in ended:
        if r["pool_id"] in seen:
            continue
        seen.add(r["pool_id"])
        g = pool.get(r["pool_id"])
        if not g or not pool.is_qq_only(g) or not g["real_gid"]:
            continue
        cur = rooms.get(g["room_id"]) if g["room_id"] else None
        if cur and cur["status"] == "open" and rooms.is_member(cur["id"], role["id"]):
            continue
        try:
            members = await qqmanager.group_members(g["bot_id"], g["real_gid"])
        except Exception:  # noqa: BLE001 查不到就不报，别让「我的待回」整个出错
            members = None
        if members and qqs & members:
            out.append((f"l:{r['id']}", f"🚶 R{r['id']} {r['kind']}（{slot_text(r)}）已经结束，你好像还没退群：群号 {g['real_gid']}"))
    return out


def day_totals() -> dict[str, int]:
    """全服今天（游戏天数，没有季度按自然日）的总数：约会/电话/踩点（取消的不算）、短信/礼物（撤回的不算）、挂出的心愿。给「我的数量」最后一行用。"""
    from . import season
    dk = season.day_key()
    out: dict[str, int] = {}
    with get_conn() as c:
        for kind, n in c.execute("SELECT kind, COUNT(*) FROM rooms WHERE dkey=? AND status!='cancelled' GROUP BY kind", (dk,)):
            out[kind] = n
        for kind, n in c.execute("SELECT kind, COUNT(*) FROM messages WHERE day=? AND error!='已撤回' GROUP BY kind", (dk,)):
            out[{"sms": "短信", "gift": "礼物"}.get(kind, kind)] = n
        out["心愿"] = c.execute("SELECT COUNT(*) FROM wishes WHERE dkey=?", (dk,)).fetchone()[0]
    return out
