"""超时提醒（额外扩展，默认关）：进行中的房间里，轮到某人回、他隔了太久还没回，就到他的个人群提醒他本人。
照老系统的分类型超时：电话（含前置电话）默认 1 小时，其余房间默认 3 小时，在设置「超时提醒」里改，填 0 = 这类不提醒。
- 「轮到谁、从什么时候开始等」跟弧长、「我的待回」同一个口径（core/arclen.waiting）：只算戏文，两人房轮流，多人房各自计时。
- 还没人写戏文的房间：等发起人开场，从开房算起；还没分到群的房间不算（没地方写）。
- 同一段等待只提醒一次；他回了、对方再写完一段，就是新的一段，超时了再提醒。
- 只发到本人的个人群（@ 本人：「你已经多久没有回复 R12 的群了」），不往房间群、公告群发。同一个人同时有几个房间超时，合成一条。
- 免打扰时段（默认 0000-0800）不发；这段时间里超时的，到点（8 点）后的第一轮再发。
- NPC 不提醒；踩点（只有自己一个人）不提醒。"""
import json
import re
import time

from . import arclen
from .db import get_config, get_conn, set_config

PHONE_KINDS = {"电话", "前置电话"}
SKIP_KINDS = {"踩点"}
_SENT_KEY = "overdue_sent"          # {"房间:角色": 这段等待的开始时间}，记着哪段等待已经提醒过


def enabled() -> bool:
    return get_config("ext:overdue", "0") != "0"


def _hours(key: str, default: str) -> float:
    try:
        return max(0.0, float(get_config(key, default) or 0))
    except ValueError:
        return float(default)


def limit_hours(kind: str) -> float:
    """这类房间等多久算超时（小时），0 = 不提醒。"""
    if kind in PHONE_KINDS:
        return _hours("overdue_phone_hours", "1")
    return _hours("overdue_other_hours", "3")


def quiet_window() -> tuple[int, int] | None:
    """免打扰时段，返回 (开始分钟, 结束分钟)，没设或写错返回 None。写法跟房间时间一样：0000-0800 / 00:00-08:00。"""
    m = re.fullmatch(r"\s*(\d{1,2}):?(\d{2})\s*[-~—至到]\s*(\d{1,2}):?(\d{2})\s*", get_config("overdue_quiet", "0000-0800") or "")
    if not m:
        return None
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    if a > 24 * 60 or b > 24 * 60 or a == b:
        return None
    return a, b


def in_quiet(now: float | None = None) -> bool:
    w = quiet_window()
    if not w:
        return False
    t = time.localtime(now or time.time())
    cur = t.tm_hour * 60 + t.tm_min
    a, b = w
    return a <= cur < b if a < b else (cur >= a or cur < b)      # 跨午夜的（2300-0700）也认


def _sent() -> dict:
    try:
        d = json.loads(get_config(_SENT_KEY, "") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def due(now: float | None = None) -> list[dict]:
    """现在该提醒的：[{room_id, kind, role_id, others: [名字], since, hours}]。只看，不记。"""
    now = now or time.time()
    sent = _sent()
    out = []
    with get_conn() as c:
        npcs = {r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=1")}
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        for room in c.execute("SELECT id, kind, creator_id, pool_id, created_ts FROM rooms WHERE status='open' ORDER BY id").fetchall():
            if room["kind"] in SKIP_KINDS:
                continue
            lim = limit_hours(room["kind"])
            if not lim:
                continue
            members = [m["role_id"] for m in c.execute("SELECT role_id FROM room_members WHERE room_id=?", (room["id"],))]
            since, last = arclen.waiting(c, room["id"], npcs)
            if last is None:            # 还没人写：等发起人开场
                if room["pool_id"] is None and room["kind"] != "微信":
                    continue
                since = {room["creator_id"]: room["created_ts"]} if room["creator_id"] in members else {}
            for rid, start in since.items():
                if rid in npcs or rid not in names:
                    continue
                if now - start < lim * 3600 or sent.get(f"{room['id']}:{rid}") == start:
                    continue
                out.append({"room_id": room["id"], "kind": room["kind"], "role_id": rid, "since": start,
                            "hours": (now - start) / 3600,
                            "others": [names[m] for m in members if m != rid and m in names]})
    return out


def mark_sent(items: list[dict]) -> None:
    """记下这几段等待已经提醒过；顺手清掉已经不在进行中的房间的记录。"""
    sent = _sent()
    for it in items:
        sent[f"{it['room_id']}:{it['role_id']}"] = it["since"]
    with get_conn() as c:
        open_ids = {str(r["id"]) for r in c.execute("SELECT id FROM rooms WHERE status='open'")}
    sent = {k: v for k, v in sent.items() if k.split(":")[0] in open_ids}
    set_config(_SENT_KEY, json.dumps(sent))


def text_for(items: list[dict]) -> str:
    """一个人的提醒正文（items 都是同一个人的）。"""
    def room(it):
        who = "、".join(it["others"]) or "大家"
        return f"R{it['room_id']} {it['kind']}（和 {who}）"
    if len(items) == 1:
        it = items[0]
        return f"⏰ 你已经 {arclen.fmt(it['hours'])}没有回复 {room(it)}的群了，有空记得回一下哦。"
    return (f"⏰ 你有 {len(items)} 个房间的群很久没有回复了：\n"
            + "\n".join(f"· {room(it)}：{arclen.fmt(it['hours'])}" for it in items) + "\n有空记得回一下哦。")


async def push(send, now: float | None = None) -> int:
    """发一轮提醒，返回提醒了几个人。发不出去的（没有个人群）也记成提醒过，免得每轮都重试刷日志。"""
    from . import messaging
    if in_quiet(now):
        return 0
    items = due(now)
    if not items:
        return 0
    by_role: dict[int, list[dict]] = {}
    for it in items:
        by_role.setdefault(it["role_id"], []).append(it)
    n = 0
    for rid, its in by_role.items():
        try:
            ok, _ = await messaging.push_to_role(rid, text_for(its), send)
        except Exception:  # noqa: BLE001
            ok = False
        n += ok
    mark_sent(items)
    return n
