"""房间复盘：QQ 号在房间群里听到的发言存下来，事后按房间、按人查看。
戏文格式跟长日系统一致：首行「角色名」+ 换行/空格/冒号（：或:）+ 正文才算戏文（计字数），其余是闲聊，只存不计。
只记正在进行的房间群；房间被清掉（季度收尾）时发言一起清掉，要留的话先在后台导出文本。"""
import re
import time

from . import pool, rooms
from .db import get_conn

_RP = re.compile(r"^\s*(\S{1,12}?)\s*[：:\s]\s*(.+)$", re.S)
_ROOM_CMD = re.compile(r"[/.。]?(结束(?:私约|电话|官约|微信|心愿|约会)|修改时间线|同意时间线|拒绝时间线|绑定qq|绑定QQ)\s*.*", re.S)


def parse_rp(text: str, names: set[str]) -> tuple[str, str] | None:
    """是戏文返回 (角色名, 正文)；角色名要是已知的角色（或参与者）名，否则算闲聊。"""
    m = _RP.match(text)
    if not m:
        return None
    name, body = m.group(1), m.group(2).strip()
    return (name, body) if name in names and body else None


def record(app_id: str, msg) -> bool:
    """听到一条房间群发言：房间在进行中、不是指令、不是官方机器人自己，就存下来。返回是否存了。"""
    from . import qqpool, roles
    text = (msg.text or "").strip()
    if not text:
        return False
    room = rooms.room_of_group(app_id, pool.QQ_PREFIX + msg.group_id)
    if not room or _ROOM_CMD.fullmatch(text) or msg.user_id in qqpool.skip_users() or msg.nickname in qqpool.skip_users():
        return False
    mem = rooms.members(room["id"])
    mine = {r["id"] for r in roles.roles_by_qq(msg.user_id)}
    who = next((m for m in mem if m["role_id"] in mine), None)
    names = {m["name"] for m in mem}
    rp = parse_rp(text, names)
    name = who["name"] if who else (msg.nickname or msg.user_id)
    with get_conn() as c:
        c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,?,?,?)",
                  (room["id"], msg.time or int(time.time()), msg.user_id, who["role_id"] if who else None, name, text,
                   int(bool(rp)), len(rp[1]) if rp else 0))
    return True


def lines(room_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM room_lines WHERE room_id=? ORDER BY id", (room_id,)).fetchall()


def stats(room_id: int) -> list[dict]:
    """每个说过话的人：戏文几条、几个字，闲聊几条。"""
    with get_conn() as c:
        rows = c.execute("SELECT name, SUM(is_rp) AS rp, SUM(chars) AS chars, SUM(1-is_rp) AS chat, MIN(ts) AS first, MAX(ts) AS last "
                         "FROM room_lines WHERE room_id=? GROUP BY name ORDER BY MIN(id)", (room_id,)).fetchall()
    return [dict(r) for r in rows]


def search(role: str = "", kind: str = "", status: str = "", q: str = "", limit: int = 200) -> list[dict]:
    """复盘列表：所有房间，可按参与者、类型、状态、发言里的关键词过滤。"""
    sql = ("SELECT r.*, (SELECT COUNT(*) FROM room_lines l WHERE l.room_id=r.id) AS n, "
           "(SELECT COALESCE(SUM(chars),0) FROM room_lines l WHERE l.room_id=r.id) AS chars FROM rooms r WHERE 1=1")
    args: list = []
    if kind:
        sql += " AND r.kind=?"; args.append(kind)
    if status:
        sql += " AND r.status=?"; args.append(status)
    if role:
        sql += " AND EXISTS (SELECT 1 FROM room_members m JOIN roles ro ON ro.id=m.role_id WHERE m.room_id=r.id AND ro.name=?)"; args.append(role)
    if q:
        sql += " AND EXISTS (SELECT 1 FROM room_lines l WHERE l.room_id=r.id AND l.text LIKE ?)"; args.append(f"%{q}%")
    sql += " ORDER BY r.id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as c:
        out = [dict(r) for r in c.execute(sql, args)]
    for r in out:
        r["who"] = "、".join(m["name"] for m in rooms.members(r["id"]))
    return out


def export_text(room_id: int) -> str:
    room = rooms.get(room_id)
    if not room:
        return ""
    from .overview import slot_text
    head = [f"R{room['id']} {room['kind']}　{slot_text(room)}", "参与者：" + "、".join(m["name"] for m in rooms.members(room_id)), ""]
    body = []
    for ln in lines(room_id):
        t = time.strftime("%H:%M", time.localtime(ln["ts"]))
        body.append(f"[{t}] {ln['name']}：{ln['text']}" + ("" if ln["is_rp"] else "（闲聊）"))
    return "\n".join(head + body)


def review_text(room_id: int) -> str:
    """结束后自动发给参与者的复盘：只放戏文（闲聊不放），带每个人的戏文字数。没有戏文返回空串。"""
    room = rooms.get(room_id)
    rp = [x for x in lines(room_id) if x["is_rp"]]
    if not room or not rp:
        return ""
    from .overview import slot_text
    st = stats(room_id)
    head = f"📖 R{room['id']} {room['kind']} 复盘\n{slot_text(room)}\n" + "　".join(f"{x['name']} {x['chars'] or 0}字" for x in st if x["rp"])
    return head + "\n\n" + "\n".join(f"{x['name']}：{x['text']}" for x in rp)


def receipt_text(room_id: int) -> str:
    """结束后发到个人群的统计回执（自动复盘关着时用）：每人戏文字数 + 怎么查看复盘。没有戏文返回空串。"""
    room = rooms.get(room_id)
    st = [x for x in stats(room_id) if x["rp"]]
    if not room or not st:
        return ""
    from .overview import slot_text
    return (f"📊 R{room['id']} {room['kind']}已经结束\n{slot_text(room)}\n"
            + "　".join(f"{x['name']} {x['rp']}条/{x['chars'] or 0}字" for x in st)
            + f"\n\n查看复盘 ➜ 发「复盘 R{room['id']}」")


# ---------- 复盘：按天整理房间、短信、礼物 ----------

def _day_of(key: str) -> tuple[int, str]:
    """day_key（「S3D5.0」或一个日期）→ (排序用数字, 显示名)。季度里的天显示 D5，没有季度的按日期。"""
    m = re.match(r"S\d+D(-?\d+)\.", key or "")
    if m:
        return int(m[1]), f"D{m[1]}"
    return 10 ** 6, key or "未知"


def events(role: str = "", kind: str = "", q: str = "") -> list[dict]:
    """所有房间、短信、礼物，按发生的先后排好，每条带第几天。role：只要这个人参与的；kind：room / sms / gift / msg（短信和礼物，不含房间）；q：内容里有这个词。"""
    out: list[dict] = []
    with get_conn() as c:
        if kind in ("", "room"):
            for r in c.execute("SELECT * FROM rooms WHERE status!='cancelled' ORDER BY id"):
                who = [m["name"] for m in rooms.members(r["id"])]
                if role and role not in who:
                    continue
                ls = lines(r["id"])
                if q and not any(q in x["text"] for x in ls) and q not in "".join(who) + (r["place"] or ""):
                    continue
                num, label = _day_of(r["dkey"])
                if r["day"] is not None:                                   # 约好的是第几天，就算在第几天
                    num, label = r["day"], f"D{r['day']}"
                out.append({"type": "room", "ts": r["created_ts"], "day": num, "day_label": label, "id": r["id"], "kind": r["kind"],
                            "who": who, "slot": ("" if r["kind"] == "微信" else f"{r['start_hm']}-{r['end_hm']}" + (f" @{r['place']}" if r["place"] else "")),
                            "status": r["status"], "lines": ls, "n": len(ls), "chars": sum(x["chars"] for x in ls)})
        if kind in ("", "sms", "gift", "msg"):
            sql = ("SELECT m.*, f.name AS fn, t.name AS tn FROM messages m JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
                   "WHERE m.error!='已撤回'")
            args: list = []
            if kind in ("sms", "gift"):
                sql += " AND m.kind=?"; args.append(kind)
            for m in c.execute(sql + " ORDER BY m.id", args):
                if role and role not in (m["fn"], m["tn"]):
                    continue
                if q and q not in (m["content"] or "") + (m["title"] or ""):
                    continue
                num, label = _day_of(m["day"])
                out.append({"type": m["kind"], "ts": m["created_ts"], "day": num, "day_label": label, "id": m["id"],
                            "frm": m["sign"] or m["fn"], "to": m["tn"], "title": m["title"], "content": m["content"], "status": m["status"]})
    out.sort(key=lambda e: (e["day"], e["ts"]))
    return out


def day_summary(evts: list[dict]) -> list[dict]:
    """按天汇总：[{label, num, room, sms, gift, total}]，第几天从小到大。"""
    days: dict[tuple[int, str], dict] = {}
    for e in evts:
        d = days.setdefault((e["day"], e["day_label"]), {"num": e["day"], "label": e["day_label"], "room": 0, "sms": 0, "gift": 0})
        d[e["type"]] += 1
    out = sorted(days.values(), key=lambda d: d["num"])
    for d in out:
        d["total"] = d["room"] + d["sms"] + d["gift"]
    return out
