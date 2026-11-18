"""房间复盘：房间群里的发言存下来，事后按房间、按人查看。两条路进来：QQ 号听到的（record），
官方机器人在房间群里收到的（record_official，大家默认开了「接收全部消息」）。一个群里只会有其中一个，都按 R 号存。
戏文格式跟长日系统一致：首行「角色名」+ 换行/空格/冒号（：或:）+ 正文才算戏文（计字数），其余是闲聊，只存不计。
只记正在进行的房间群；房间被清掉（季度收尾）时发言一起清掉，要留的话先在后台导出文本。"""
import re
import time

from . import pool, rooms, storycal
from .db import get_conn, get_int
from .textutil import hm_text

_RP = re.compile(r"^\s*(\S{1,12}?)\s*[：:\s]\s*(.+)$", re.S)
_ROOM_CMD = re.compile(r"(?:@\S+\s*)*[/.。]?(结束(?:约会|私约|电话|官约|微信|心愿|前置电话)|修改时间线|同意时间线|拒绝时间线|绑定qq|绑定QQ|补录)\s*.*", re.S)


def body_of(name: str, text: str) -> str:
    """戏文去掉开头的「名字：」只留正文（发言人已经单独写了，免得出现「小满：小满：……」）。不是这个名字开头的原样返回。"""
    p = parse_rp(text, {name}) if name else None
    return p[1] if p else text


_PAIRS = {"(": ")", "（": "）"}


def strip_ooc(text: str) -> str:
    """去掉最外层括号包着的场外话（半角、全角都认，连里面嵌套的一起）。没打闭合的括号原样保留，免得漏打一个括号把正文删了。"""
    out, stack, start = [], [], 0
    for i, ch in enumerate(text):
        if ch in _PAIRS:
            if not stack:
                out.append(text[start:i])
                start = i
            stack.append(_PAIRS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                start = i + 1
    out.append(text[start:])               # 没闭合的：从那个左括号起原样留下
    return re.sub(r"[ \t]+\n", "\n", "".join(out)).strip()


def parse_rp(text: str, names) -> tuple[str, str] | None:
    """是戏文返回 (角色本名, 正文)；首行要是已知的角色（或参与者）名，否则算闲聊。
    names 可以是名字集合，也可以是 {首行能写的名字: 本名}（简称 → 本名）。正文里最外层括号的场外话已经去掉，去完没字了就不算戏文。"""
    m = _RP.match(text)
    if not m:
        return None
    name, body = m.group(1), strip_ooc(m.group(2))
    if name not in names or not body:
        return None
    return (names[name] if isinstance(names, dict) else name), body


def names_of(mem) -> dict[str, str]:
    """房间参与者首行能写的名字：本名，加上设了的简称（简称只用来认首行，记下来的还是本名）。"""
    out = {m["name"]: m["name"] for m in mem}
    ids = [m["role_id"] for m in mem]
    if ids:
        with get_conn() as c:
            for r in c.execute(f"SELECT role_id, short FROM role_profiles WHERE short!='' AND role_id IN ({','.join('?' * len(ids))})", ids):
                real = next(m["name"] for m in mem if m["role_id"] == r["role_id"])
                out.setdefault(r["short"], real)
    return out


def clean(text: str, names) -> tuple[str, tuple[str, str] | None]:
    """要存下来的文字和解析结果。戏文存成「本名 + 原来的分隔 + 去掉场外话的正文」，闲聊原样存。"""
    rp = parse_rp(text, names)
    if not rp:
        return text, None
    m = _RP.match(text)
    if rp[0] == m.group(1) and rp[1] == m.group(2).strip():      # 没有场外话、首行就是本名：原样存，不动排版（全角缩进、空行）
        return text, rp
    sep = "\n" if "\n" in text[m.end(1):m.start(2)] else "："
    return f"{rp[0]}{sep}{rp[1]}", rp


def record(app_id: str, msg) -> bool:
    """听到一条房间群发言：房间在进行中、不是指令、不是官方机器人自己，就存下来。返回是否存了。"""
    from . import qqpool, roles
    text = (msg.text or "").strip()
    if not text or getattr(msg, "forward_ids", None):          # 合并转发本身不存（要补录就回复它发「补录」）
        return False
    room = rooms.room_of_group(app_id, pool.QQ_PREFIX + msg.group_id)
    if not room or _ROOM_CMD.fullmatch(text) or msg.user_id in qqpool.skip_users() or msg.nickname in qqpool.skip_users():
        return False
    mem = rooms.members(room["id"])
    mine = {r["id"] for r in roles.roles_by_qq(msg.user_id)}
    who = next((m for m in mem if m["role_id"] in mine), None)
    text, rp = clean(text, names_of(mem))
    name = who["name"] if who else (msg.nickname or msg.user_id)
    ts = msg.time or int(time.time())
    with get_conn() as c:
        _drop_near_duplicate(c, room["id"], msg.user_id, text, ts)
        c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars, msg_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (room["id"], ts, msg.user_id, who["role_id"] if who else None, name, text,
                   int(bool(rp)), len(rp[1]) if rp else 0, getattr(msg, "message_id", "") or ""))
    from . import specials
    specials.on_room_line(room["id"], who["role_id"] if who else None, name, text)      # 窃听器
    return True


OFFICIAL = "o:"           # 官方机器人那条路记的行：qq 列存「o:member_openid」，msg_id 存「o:事件id」


def record_official(bot_id: str, group_openid: str, member_openid: str, text: str, event_id: str = "", ts: int | None = None) -> bool:
    """官方机器人在房间群里收到一条发言：房间在进行中、不是房间指令，就存下来。返回是否存了。
    发言人按 openid 认角色（每人同期只有一个活跃角色）。"""
    from . import qqpool, roles
    text = (text or "").strip()
    if not text or _ROOM_CMD.fullmatch(text):
        return False
    room = rooms.room_of_group(bot_id, group_openid)
    if not room:
        return False
    skip = qqpool.skip_users()
    if skip:
        with get_conn() as c:
            if any(r["qq"] in skip for r in c.execute("SELECT qq FROM person_qq WHERE member_openid=?", (member_openid,))):
                return False
    mem = rooms.members(room["id"])
    r = roles.find_role_for_sender(bot_id, group_openid, member_openid)       # 每人同期只有一个活跃角色
    who = {"role_id": r["id"], "name": r["name"]} if r else None
    text, rp = clean(text, names_of(mem))
    name = who["name"] if who else "群友" + member_openid[-4:]
    key = OFFICIAL + member_openid
    ts = int(ts or time.time())
    with get_conn() as c:
        _drop_near_duplicate(c, room["id"], key, text, ts)
        c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars, msg_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (room["id"], ts, key, who["role_id"] if who else None, name, text,
                   int(bool(rp)), len(rp[1]) if rp else 0, (OFFICIAL + event_id) if event_id else ""))
    from . import specials
    specials.on_room_line(room["id"], who["role_id"] if who else None, name, text)      # 窃听器
    return True


DEDUP_WINDOW = 600        # 同一个人 10 分钟内的相似发言才算「撤回重发」（跟长日系统一致）
DEDUP_MIN_LEN = 8         # 太短的（「好」「嗯嗯」）重复是正常说话，不处理


def similar(a: str, b: str) -> float:
    """两段话有多像（0–1）：按相邻两个字一组，看重合的占多少（Jaccard，跟长日系统同一个算法）。"""
    ga, gb = {a[i:i + 2] for i in range(len(a) - 1)}, {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 1.0 if a == b else 0.0
    return len(ga & gb) / len(ga | gb)


def _drop_near_duplicate(c, room_id: int, qq: str, text: str, ts: int) -> None:
    """同一个人 10 分钟内发了和自己上一条差不多的话（发错重发、修改后再发）：算替换，删掉前一条，只留这一条，字数按新的算。
    相似度阈值是设置里的 room_redo_similarity（默认 60），0 = 不处理。"""
    pct = get_int("room_redo_similarity", 60)
    if pct <= 0 or len(text) < DEDUP_MIN_LEN:
        return
    prev = c.execute("SELECT id, text, ts FROM room_lines WHERE room_id=? AND qq=? ORDER BY id DESC LIMIT 1", (room_id, qq)).fetchone()
    if prev and len(prev["text"]) >= DEDUP_MIN_LEN and ts - prev["ts"] <= DEDUP_WINDOW and similar(prev["text"], text) * 100 >= pct:
        c.execute("DELETE FROM room_lines WHERE id=?", (prev["id"],))


def recall(message_id: str, room_gid: str = "") -> int:
    """QQ 里有人撤回了一条消息：复盘里同步删掉。返回删了几条。"""
    if not message_id:
        return 0
    with get_conn() as c:
        return c.execute("DELETE FROM room_lines WHERE msg_id=?", (str(message_id),)).rowcount


def lines(room_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM room_lines WHERE room_id=? ORDER BY ts, id", (room_id,)).fetchall()      # 按时间：补录的旧消息插回原位


def _classify(room_id: int, text: str) -> tuple[str, int, int]:
    """按房间参与者名判断是不是戏文：返回 (要存的文字, is_rp, 戏文字数)。"""
    text, rp = clean(text, names_of(rooms.members(room_id)))
    return (text, 1, len(rp[1])) if rp else (text, 0, 0)


def add_line(room_id: int, role_id: int | None, text: str, ts: int | None = None) -> tuple[bool, str]:
    """后台手动补一条发言（role_id 是参与者之一）。"""
    text = (text or "").strip()
    if not text:
        return False, "发言内容不能为空。"
    mem = {m["role_id"]: m["name"] for m in rooms.members(room_id)}
    if role_id not in mem:
        return False, "说话的人要是这个房间的参与者。"
    text, is_rp, chars = _classify(room_id, text)
    with get_conn() as c:
        c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,?,?,?)",
                  (room_id, ts or int(time.time()), "", role_id, mem[role_id], text, is_rp, chars))
    return True, "已添加。"


def edit_line(room_id: int, line_id: int, text: str, role_id: int | None = None, ts: int | None = None) -> tuple[bool, str]:
    """改一条发言的内容（可以同时改说话的人、时间）；戏文/闲聊和字数按新内容重新判断。"""
    text = (text or "").strip()
    if not text:
        return False, "发言内容不能为空，要删掉请用「删除」。"
    with get_conn() as c:
        old = c.execute("SELECT * FROM room_lines WHERE id=? AND room_id=?", (line_id, room_id)).fetchone()
    if not old:
        return False, "找不到这条发言。"
    mem = {m["role_id"]: m["name"] for m in rooms.members(room_id)}
    rid, name = old["role_id"], old["name"]
    if role_id is not None and role_id != rid:
        if role_id not in mem:
            return False, "说话的人要是这个房间的参与者。"
        rid, name = role_id, mem[role_id]
    text, is_rp, chars = _classify(room_id, text)
    with get_conn() as c:
        c.execute("UPDATE room_lines SET text=?, role_id=?, name=?, ts=?, is_rp=?, chars=? WHERE id=?", (text, rid, name, ts or old["ts"], is_rp, chars, line_id))
    return True, "已修改。"


def delete_line(room_id: int, line_id: int) -> bool:
    with get_conn() as c:
        return c.execute("DELETE FROM room_lines WHERE id=? AND room_id=?", (line_id, room_id)).rowcount > 0


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
        body.append(f"[{t}] {ln['name']}：{body_of(ln['name'], ln['text'])}" + ("" if ln["is_rp"] else "（闲聊）"))
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
    return head + "\n\n" + "\n".join(f"{x['name']}：{body_of(x['name'], x['text'])}" for x in rp)


def archive_all() -> list[dict]:
    """管理员手机用：所有房间（有发言的或进行中的），新的在前。"""
    with get_conn() as c:
        ids = [r["role_id"] for r in c.execute("SELECT DISTINCT role_id FROM room_members").fetchall()]
    seen, out = set(), []
    for rid in ids:
        for x in archive_for(rid):
            if x["id"] not in seen:
                seen.add(x["id"]); out.append(x)
    out.sort(key=lambda x: (x["ended_ts"] or x["created_ts"], x["id"]), reverse=True)
    return out


def archive_for(role_id: int) -> list[dict]:
    """手机「戏录」：我参与过的、有发言记录的房间（进行中的也算），新的在前。每项带戏文条数/字数和分组标题。"""
    with get_conn() as c:
        rs = c.execute("SELECT r.*, (SELECT COUNT(*) FROM room_lines l WHERE l.room_id=r.id AND l.is_rp=1) AS rp_n, "
                       "(SELECT COALESCE(SUM(l.chars),0) FROM room_lines l WHERE l.room_id=r.id AND l.is_rp=1) AS rp_chars, "
                       "(SELECT COUNT(*) FROM room_lines l WHERE l.room_id=r.id) AS all_n "
                       "FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.status!='cancelled' "
                       "ORDER BY COALESCE(r.ended_ts, r.created_ts) DESC, r.id DESC", (role_id,)).fetchall()
    out = []
    for r in rs:
        if not r["all_n"] and r["status"] != "open":
            continue
        d = dict(r)
        ts = r["ended_ts"] or r["created_ts"]
        d["group"] = "进行中" if r["status"] == "open" else (f"D{r['day']}" if r["day"] is not None else time.strftime("%m-%d", time.localtime(ts)))
        out.append(d)
    return out


def _phone_hint() -> str:
    from . import extensions
    return "，或在小手机「发现 → 戏录」里翻看" if extensions.is_enabled("phone") else ""


def receipt_text(room_id: int) -> str:
    """结束后发到个人群的统计回执（自动复盘关着时用）：每人戏文字数 + 怎么查看复盘。没有戏文返回空串。"""
    room = rooms.get(room_id)
    st = [x for x in stats(room_id) if x["rp"]]
    if not room or not st:
        return ""
    from .overview import slot_text
    return (f"📊 R{room['id']} {room['kind']}已经结束\n{slot_text(room)}\n"
            + "　".join(f"{x['name']} {x['rp']}条/{x['chars'] or 0}字" for x in st)
            + f"\n\n查看复盘 ➜ 发「复盘 R{room['id']}」" + _phone_hint())


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
                            "story": ("" if r["kind"] in rooms.NO_TIMELINE else storycal.label_for_slot(num, r["start_hm"])),
                            "who": who, "slot": ("" if r["kind"] in rooms.NO_TIMELINE else f"{hm_text(r['start_hm'], r['end_hm'])}" + (f" @{r['place']}" if r["place"] else "")),
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
                            "story": storycal.label_for_ts(num, m["created_ts"]),
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


# ---------- 补录（只由官方机器人做）：漏记的发言，事后回复合并转发补回来 ----------

def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


# ---------- 官方机器人群里的补录：回复合并转发时，QQ 把被引用的聊天记录作为文字带过来（没有时间、没有 QQ 号） ----------

_QUOTE_ITEM = re.compile(r"---\s*第\s*\d+\s*条\s*---(.*?)(?=---\s*第\s*\d+\s*条\s*---|===\s*消息|\Z)", re.S)
_QUOTE_FIELD = re.compile(r"\[(消息内容|发送者)\]\s?(.*?)(?=\n\s*\[(?:消息内容|发送者|消息类型|关联消息)\]|\Z)", re.S)


def parse_quoted_forward(text: str) -> list[dict]:
    """「--- 第1条 --- [消息内容] … [发送者] …」这种文字 → [{nickname, text}]（按原来的顺序）。"""
    out = []
    for block in _QUOTE_ITEM.findall(text or ""):
        fields = {k: re.sub(r"\n\s+", "\n", v).strip() for k, v in _QUOTE_FIELD.findall(block)}
        if fields.get("消息内容"):
            out.append({"nickname": fields.get("发送者", ""), "text": fields["消息内容"]})
    return out


def backfill_quoted(room_id: int, items: list[dict], now: int | None = None) -> dict:
    """没有时间戳的补录：内容和复盘里已有的一样就跳过（拿到的是 QQ 昵称，和存的角色名对不上，所以只比内容）；
    缺的按转发里的顺序插：夹在前后两条「已有的」之间平均分时间；前面没有已有的就排在第一条已有的前面，
    一条都对不上就均匀摆在房间开始到结束的时间段里。戏文开头写了参与者名字的，记在那个角色名下。返回 {added, skipped, empty}。"""
    room = rooms.get(room_id)
    if not room:
        return {"added": 0, "skipped": 0, "empty": 0}
    mem = rooms.members(room_id)
    names = names_of(mem)
    role_of = {m["name"]: m["role_id"] for m in mem}
    have = {}
    for ln in lines(room_id):
        have.setdefault(_norm(ln["text"]), ln["ts"])
    for it in items:                           # 跟平时记的一样：戏文去掉场外话、首行换成本名，这样才对得上已经存的
        it["text"] = clean((it.get("text") or "").strip(), names)[0]
    now = int(now or time.time())
    seq = []                                   # [(item, 已有的时间 或 None)]
    empty = 0
    for it in items:
        t = (it.get("text") or "").strip()
        if not t:
            empty += 1
            continue
        seq.append((it, have.get(_norm(t))))
    known = [i for i, (_, ts) in enumerate(seq) if ts is not None]
    added = 0
    with get_conn() as c:
        for i, (it, ts) in enumerate(seq):
            if ts is not None:
                continue
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            if prev is not None and nxt is not None:
                a, b = seq[prev][1], seq[nxt][1]
                when = a + (b - a) * (i - prev) // max(nxt - prev, 1)
            elif nxt is not None:
                when = seq[nxt][1] - (nxt - i)
            elif prev is not None:
                when = seq[prev][1] + (i - prev)
            else:                              # 一条都对不上：均匀摆在房间开始到结束（还没结束就到现在）之间
                start = int(room["created_ts"])
                stop = int(room["ended_ts"] or now) if "ended_ts" in room.keys() else now
                when = start + (max(stop - start, len(seq)) * (i + 1)) // (len(seq) + 1)
            text = it["text"].strip()
            rp = parse_rp(text, names)
            who = role_of.get(rp[0]) if rp else role_of.get(it.get("nickname", ""))
            c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars, msg_id) VALUES(?,?,?,?,?,?,?,?,?)",
                      (room_id, int(when), "", who, (rp[0] if rp else None) or it.get("nickname") or "?", text,
                       int(bool(rp)), len(rp[1]) if rp else 0, ""))
            have[_norm(text)] = int(when)
            added += 1
    return {"added": added, "skipped": len(seq) - added, "empty": empty}
