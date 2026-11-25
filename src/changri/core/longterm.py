"""长期群模式：恋综、跑团之外的第三种群模式（不依赖 QQ / NoneBot，可单独测试）。

- 后台先建长期群（名字）；QQ 群里「/模式 长期群」切模式，再「/切换长期群 名字」接到哪一个（可以有好几个长期群）。
- 每个长期群自己一套：注册的玩家、字数和货币、属性面板（ra / rt 读它）、背包；没有季度、时间线。
- 字数：玩家回复一条合并转发发「/字数统计」，按转发里的发送者名字把字数记到对应玩家名下，
  对不上的名字先挂在「未认领」，后台认领或者那人以后用这个名字注册时转过去。同一条发言重复交不重复算。
- 货币：后台设「每 N 字换 M 货币」，字数入账时自动换，零头留着下次凑。管理员也能直接加减、改余额。
表在 storage/migrations/0033_longterm.sql。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from datetime import date, datetime, timedelta

from .db import get_config, get_conn, set_config

MODE = "longterm"
MAX_NAME = 16
MAX_ATTRS = 60
MAX_VALUE = 9999
MAX_ITEM = 20
MAX_QTY = 999999
_FULL = str.maketrans("０１２３４５６７８９：＝＋－", "0123456789:=+-")
_WS = re.compile(r"\s+")
_ATTR = re.compile(r"([A-Za-z一-鿿_]{1,12}?)\s*[:：=＝]?\s*(-?\d{1,4})(?!\d)")


def _now() -> int:
    return int(time.time())


def _today() -> str:
    """按机器人的时区（config 的 timezone，启动时设好）算今天。"""
    return time.strftime("%Y-%m-%d")


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


# ======================== 长期群 ========================

def create_group(name: str) -> tuple[int | None, str]:
    name = _clean_name(name)
    if not name or len(name) > MAX_NAME:
        return None, f"名字要 1~{MAX_NAME} 个字。"
    with get_conn() as c:
        if c.execute("SELECT 1 FROM lt_groups WHERE name=?", (name,)).fetchone():
            return None, f"已经有叫「{name}」的长期群了。"
        gid = c.execute("INSERT INTO lt_groups(name, created_ts) VALUES (?,?)", (name, _now())).lastrowid
    return gid, ""


def list_groups() -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT g.*, (SELECT COUNT(*) FROM lt_members m WHERE m.group_id=g.id) AS n_members, "
                         "(SELECT COUNT(*) FROM lt_submissions s WHERE s.group_id=g.id AND s.status='pending') AS n_pending "
                         "FROM lt_groups g ORDER BY g.id").fetchall()
    return [dict(r) for r in rows]


def get_group(gid: int | None) -> dict | None:
    if not gid:
        return None
    with get_conn() as c:
        r = c.execute("SELECT * FROM lt_groups WHERE id=?", (gid,)).fetchone()
    return dict(r) if r else None


def find_group(name: str) -> dict | None:
    name = _clean_name(name)
    with get_conn() as c:
        r = c.execute("SELECT * FROM lt_groups WHERE name=?", (name,)).fetchone()
        if not r and name.isdigit():
            r = c.execute("SELECT * FROM lt_groups WHERE id=?", (int(name),)).fetchone()
    return dict(r) if r else None


def update_group(gid: int, **kw) -> tuple[bool, str]:
    g = get_group(gid)
    if not g:
        return False, "没有这个长期群。"
    fields = {}
    if "name" in kw:
        name = _clean_name(kw["name"])
        if not name or len(name) > MAX_NAME:
            return False, f"名字要 1~{MAX_NAME} 个字。"
        fields["name"] = name
    if "currency" in kw:
        cur = _clean_name(kw["currency"])
        if not cur or len(cur) > 8:
            return False, "货币名要 1~8 个字。"
        fields["currency"] = cur
    for k in ("chars_per_step", "coins_per_step"):
        if k in kw:
            try:
                v = int(kw[k])
            except (TypeError, ValueError):
                return False, "兑换比例要填正整数。"
            if v < 1 or v > 1_000_000:
                return False, "兑换比例要填 1 到 1000000 之间的整数。"
            fields[k] = v
    if "reg_fields" in kw:
        lines = [_clean_name(x)[:12] for x in str(kw["reg_fields"]).splitlines()]
        fields["reg_fields"] = "\n".join(dict.fromkeys(x for x in lines if x))
    for k in ("self_attrs", "count_admin_only", "count_review", "checkin_on"):
        if k in kw:
            fields[k] = 1 if kw[k] else 0
    if "kpi_period" in kw:
        fields["kpi_period"] = kw["kpi_period"] if kw["kpi_period"] in KPI_PERIODS else "week"
    if fields.get("kpi_period") == "days":
        try:
            n = int(kw.get("kpi_days") or 0)
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= KPI_MAX_DAYS:
            return False, f"自定义 KPI 周期要填 1 到 {KPI_MAX_DAYS} 之间的天数。"
        fields["kpi_days"] = n
        raw = str(kw.get("kpi_anchor") or "").strip()
        try:
            fields["kpi_anchor"] = _ts(datetime.strptime(raw, "%Y-%m-%d").date()) if raw else _ts(date.today())
        except ValueError:
            return False, "KPI 从哪天开始算，要写成 2026-11-25 这样的日期。"
    for k in ("kpi_chars", "kpi_paras"):
        if k in kw:
            try:
                fields[k] = max(0, int(kw[k] or 0))
            except (TypeError, ValueError):
                return False, "KPI 要填整数（0 = 不考核这一项）。"
    if "checkin_rewards" in kw:
        text = str(kw["checkin_rewards"] or "").strip()
        _, bad = parse_rewards(text)
        if bad:
            return False, "签到奖励这几行看不懂：" + "、".join(bad) + "。每行写「名字 数量」或「名字 最少-最多」，比如「银两 10-30」。"
        fields["checkin_rewards"] = text
    if not fields:
        return True, ""
    with get_conn() as c:
        if "name" in fields and c.execute("SELECT 1 FROM lt_groups WHERE name=? AND id<>?", (fields["name"], gid)).fetchone():
            return False, f"已经有叫「{fields['name']}」的长期群了。"
        c.execute("UPDATE lt_groups SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), gid))
    return True, ""


def delete_group(gid: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lt_groups WHERE id=?", (gid,))
        for r in c.execute("SELECT key FROM config WHERE key LIKE 'lt_group:%' AND value=?", (str(gid),)).fetchall():
            c.execute("DELETE FROM config WHERE key=?", (r["key"],))


def reg_fields(group: dict) -> list[str]:
    return [x for x in (group.get("reg_fields") or "").splitlines() if x.strip()]


def rate_text(group: dict) -> str:
    return f"每 {group['chars_per_step']} 字换 {group['coins_per_step']} {group['currency']}"


# ---------- QQ 群 ↔ 长期群 ----------

def bound_group(group_openid: str) -> dict | None:
    v = get_config(f"lt_group:{group_openid}", "")
    return get_group(int(v)) if v.isdigit() else None


def group_count(gid: int) -> int:
    """接到这个长期群的 QQ 群有几个。"""
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM config WHERE key LIKE 'lt_group:%' AND value=?", (str(gid),)).fetchone()[0]


def bind(group_openid: str, gid: int) -> None:
    set_config(f"lt_group:{group_openid}", str(gid))


# ======================== 玩家 ========================

def _member(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    try:
        d["info"] = json.loads(d.get("info") or "{}")
    except ValueError:
        d["info"] = {}
    d["alias_list"] = [a for a in (d.get("aliases") or "").split(",") if a]
    try:
        notes = json.loads(d.get("notes") or "{}")
        d["notes"] = {str(s): {str(k): str(v) for k, v in f.items()} for s, f in notes.items() if isinstance(f, dict)}
    except (ValueError, AttributeError):
        d["notes"] = {}
    return d


def get_member(mid: int) -> dict | None:
    with get_conn() as c:
        return _member(c.execute("SELECT * FROM lt_members WHERE id=?", (mid,)).fetchone())


def linked_openids(member_openid: str) -> list[str]:
    """同一个人的所有 openid：自己 + 用「认人」（core/trpg 的 trpg_identities）认到一起的。
    官方机器人给的 member_openid 可能每个群不一样，一个长期群接好几个 QQ 群时靠它认出是同一个人。"""
    if not member_openid:
        return []
    from .trpg import player_for                            # 放函数里：trpg 加载时要读 dice，免得循环引用
    pid = player_for(member_openid, create=False)
    if not pid:
        return [member_openid]
    with get_conn() as c:
        others = [r["member_openid"] for r in c.execute(
            "SELECT member_openid FROM trpg_identities WHERE player_id=? AND member_openid NOT LIKE 'qq:%'", (pid,))]
    return list(dict.fromkeys([member_openid, *others]))


def member_of(gid: int, member_openid: str) -> dict | None:
    """这个人在长期群里注册的玩家：先按这个 openid 找，找不到再按「认人」认过的其他 openid 找。"""
    if not member_openid:
        return None
    with get_conn() as c:
        r = c.execute("SELECT * FROM lt_members WHERE group_id=? AND member_openid=?", (gid, member_openid)).fetchone()
        if r:
            return _member(r)
        for o in linked_openids(member_openid)[1:]:
            r = c.execute("SELECT * FROM lt_members WHERE group_id=? AND member_openid=?", (gid, o)).fetchone()
            if r:
                return _member(r)
    return None


def member_by_name(gid: int, name: str) -> dict | None:
    """按注册名找，找不到再按别名找（合并转发里的 QQ 昵称）。"""
    name = _clean_name(name)
    if not name:
        return None
    with get_conn() as c:
        r = c.execute("SELECT * FROM lt_members WHERE group_id=? AND name=?", (gid, name)).fetchone()
        if r:
            return _member(r)
        for r in c.execute("SELECT * FROM lt_members WHERE group_id=? AND aliases<>''", (gid,)):
            if name in r["aliases"].split(","):
                return _member(r)
    return None


def list_members(gid: int) -> list[dict]:
    with get_conn() as c:
        return [_member(r) for r in c.execute("SELECT * FROM lt_members WHERE group_id=? ORDER BY id", (gid,))]


def parse_register(text: str, fields: list[str]) -> tuple[str, dict]:
    """「/注册 名字」后面可以换行写「项目：内容」；只认后台设了的项目。返回 (名字, {项目: 内容})。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "", {}
    name, info = "", {}
    rest = lines
    m = re.match(r"^(?:名字|姓名|名称)\s*[:：]\s*(.+)$", lines[0])
    if m or not re.match(r"^\S{1,12}\s*[:：]", lines[0]):
        name = _clean_name(m.group(1) if m else lines[0])
        rest = lines[1:]
    for ln in rest:
        mm = re.match(r"^(\S{1,12}?)\s*[:：]\s*(.*)$", ln)
        if not mm:
            continue
        k, v = mm.group(1), mm.group(2).strip()
        if k in ("名字", "姓名", "名称") and not name:
            name = _clean_name(v)
        elif k in fields:
            info[k] = v[:200]
    return name, info


def register(gid: int, member_openid: str, name: str, info: dict | None = None) -> tuple[dict | None, str]:
    """注册或改资料。同一个人再发一次就是改名 / 改资料。后台先加过同名、还没认人的玩家，直接认上。
    注册名在「未认领」里有字数的，一起转过来。"""
    name = _clean_name(name)
    if not name or len(name) > MAX_NAME:
        return None, f"名字要 1~{MAX_NAME} 个字。"
    info = info or {}
    now = _now()
    new = False
    me = member_of(gid, member_openid) if member_openid else None      # 认人认过的其他群里注册的也算自己
    with get_conn() as c:
        mine = c.execute("SELECT * FROM lt_members WHERE id=?", (me["id"],)).fetchone() if me else None
        same = c.execute("SELECT * FROM lt_members WHERE group_id=? AND name=?", (gid, name)).fetchone()
        if not same:
            owner = next((r for r in c.execute("SELECT id, name, aliases FROM lt_members WHERE group_id=? AND aliases<>''", (gid,))
                          if name in r["aliases"].split(",")), None)
            if owner and (not mine or owner["id"] != mine["id"]):
                return None, f"「{name}」是「{owner['name']}」的别名，换个名字。"
        if same and (not mine or same["id"] != mine["id"]):
            if same["member_openid"]:
                return None, (f"「{name}」已经被别人注册了，换个名字。\n要是那就是你在另一个群注册的：在那个群发「认人」拿码，"
                              "到这个群发「认人 码」，两边就认成同一个人。")
            if mine:
                return None, f"「{name}」是后台预先加的玩家，你已经注册成「{mine['name']}」了；请管理员处理。"
            merged = {**json.loads(same["info"] or "{}"), **info}
            c.execute("UPDATE lt_members SET member_openid=?, info=? WHERE id=?",
                      (member_openid, json.dumps(merged, ensure_ascii=False), same["id"]))
            mid = same["id"]
        elif mine:
            merged = {**json.loads(mine["info"] or "{}"), **info}
            c.execute("UPDATE lt_members SET info=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), mine["id"]))
            mid = mine["id"]
        else:
            mid = c.execute("INSERT INTO lt_members(group_id, member_openid, name, info, created_ts) VALUES (?,?,?,?,?)",
                            (gid, member_openid, name, json.dumps(info, ensure_ascii=False), now)).lastrowid
            for d in c.execute("SELECT name, default_value FROM lt_attr_defs WHERE group_id=?", (gid,)).fetchall():
                c.execute("INSERT OR IGNORE INTO lt_attrs(member_id, name, value) VALUES (?,?,?)", (mid, d["name"], d["default_value"]))
            new = True
    if new:
        event(gid, mid, "注册", f"{name} 注册了" if member_openid else f"后台加了玩家 {name}")
    elif mine and mine["name"] != name:
        ok, err = rename(mid, name)                     # 再发一次「注册 新名字」= 改名
        if not ok:
            return None, err
    claim(gid, name, mid)
    return get_member(mid), ""


def rename(mid: int, name: str, by: str = "") -> tuple[bool, str]:
    """改名（注册时名字输错了等）。旧名字留成别名：合并转发里还写着旧名字的发言照样算给 TA。
    新名字不能跟别人的名字或别名重；改成自己的别名可以（顺手从别名里拿掉）。"""
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    name = _clean_name(name)
    if not name or len(name) > MAX_NAME:
        return False, f"名字要 1~{MAX_NAME} 个字。"
    if name == m["name"]:
        return False, f"本来就叫「{name}」。"
    other = member_by_name(m["group_id"], name)
    if other and other["id"] != mid:
        return False, f"「{name}」已经有人用了（是「{other['name']}」的名字或别名）。"
    aliases = [a for a in m["alias_list"] if a != name]
    if m["name"] not in aliases:
        aliases.append(m["name"])
    with get_conn() as c:
        c.execute("UPDATE lt_members SET name=?, aliases=? WHERE id=?", (name, ",".join(aliases), mid))
    event(m["group_id"], mid, "注册", f"{m['name']} 改名为 {name}" + (f"（{by}）" if by else ""))
    claim(m["group_id"], name, mid)
    return True, ""


def add_member(gid: int, name: str) -> tuple[dict | None, str]:
    """后台手动加一个玩家（还没认 QQ），本人以后用同一个名字注册就认上。"""
    return register(gid, "", name)


def update_member(mid: int, name: str | None = None, aliases: str | None = None, info: dict | None = None) -> tuple[bool, str]:
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    sets, vals = [], []
    if name is not None:
        name = _clean_name(name)
        if not name or len(name) > MAX_NAME:
            return False, f"名字要 1~{MAX_NAME} 个字。"
        other = member_by_name(m["group_id"], name)
        if name != m["name"] and other and other["id"] != mid:
            return False, f"「{name}」已经有人用了（是「{other['name']}」的名字或别名）。"
    if aliases is not None:
        al = [_clean_name(a)[:MAX_NAME * 2] for a in re.split(r"[,，、\n]", aliases)]
        sets.append("aliases=?"); vals.append(",".join(dict.fromkeys(a for a in al if a)))
    if info is not None:
        sets.append("info=?"); vals.append(json.dumps(info, ensure_ascii=False))
    if sets:
        with get_conn() as c:
            c.execute("UPDATE lt_members SET " + ",".join(sets) + " WHERE id=?", (*vals, mid))
    if name and name != m["name"]:
        ok, err = rename(mid, name, "后台改的")         # 别名先存好，再改名（旧名字加进别名）
        if not ok:
            return False, err
    for a in (aliases or "").replace("，", ",").replace("、", ",").split(","):
        if a.strip():
            claim(m["group_id"], a.strip(), mid)
    return True, ""


def unbind_member(mid: int) -> None:
    """解开 QQ（换号、认错人）：数据留着，本人再用这个名字注册就认回来。"""
    with get_conn() as c:
        c.execute("UPDATE lt_members SET member_openid='' WHERE id=?", (mid,))


def delete_member(mid: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lt_members WHERE id=?", (mid,))


def _log(c, gid: int, mid: int | None, kind: str, chars: int = 0, coins: int = 0, item: str = "", qty: int = 0, note: str = "",
         submission_id: int | None = None) -> None:
    c.execute("INSERT INTO lt_ledger(group_id, member_id, kind, chars, coins, item, qty, note, submission_id, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (gid, mid, kind, chars, coins, item, qty, note, submission_id, _now()))


EVENT_KINDS = ("字数", "商城", "道具", "赠送", "签到", "合成", "管理", "注册")


def event(gid: int, mid: int | None, kind: str, text: str, ref_id: int | None = None) -> None:
    """后台「动态」记一行：一件事一行，写成人话。"""
    with get_conn() as c:
        c.execute("INSERT INTO lt_events(group_id, member_id, kind, text, ref_id, ts) VALUES (?,?,?,?,?,?)",
                  (gid, mid, kind, text[:500], ref_id, _now()))


def events(gid: int, kind: str = "", mid: int | None = None, limit: int = 50, before: int | None = None) -> list[dict]:
    sql = "SELECT e.*, m.name AS member_name FROM lt_events e LEFT JOIN lt_members m ON m.id=e.member_id WHERE e.group_id=?"
    args: list = [gid]
    if kind:
        sql += " AND e.kind=?"; args.append(kind)
    if mid:
        sql += " AND e.member_id=?"; args.append(mid)
    if before:
        sql += " AND e.id<?"; args.append(before)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY e.id DESC LIMIT ?", (*args, limit))]


def stats(gid: int) -> dict:
    """总览顶上的几个数。"""
    today = _today()
    start = int(time.mktime(time.strptime(today, "%Y-%m-%d")))
    with get_conn() as c:
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0] or 0  # noqa: E731
        return {"members": one("SELECT COUNT(*) FROM lt_members WHERE group_id=?", gid),
                "chars": one("SELECT SUM(chars) FROM lt_members WHERE group_id=?", gid),
                "coins": one("SELECT SUM(coins) FROM lt_members WHERE group_id=?", gid),
                "pending": one("SELECT COUNT(*) FROM lt_submissions WHERE group_id=? AND status='pending'", gid),
                "unclaimed": one("SELECT COUNT(*) FROM lt_unclaimed WHERE group_id=?", gid),
                "today_chars": one("SELECT SUM(chars) FROM lt_ledger WHERE group_id=? AND kind='字数' AND chars>0 AND ts>=?", gid, start),
                "kpi_ok": sum(1 for p in kpi_progress(gid).values() if p["ok"]) if kpi_on(get_group(gid)) else None,
                "today_checkins": one("SELECT COUNT(*) FROM lt_checkins k JOIN lt_members m ON m.id=k.member_id WHERE m.group_id=? AND k.day=?", gid, today)}


def ledger(mid: int, limit: int = 100) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lt_ledger WHERE member_id=? ORDER BY id DESC LIMIT ?", (mid, limit))]


# ======================== 字数和货币 ========================

def _credit(c, gid: int, mid: int, chars: int, note: str, submission_id: int | None = None, paras: int = 0) -> int:
    """字数记到玩家名下，按当前比例换货币，零头留着。返回这次换到的货币。"""
    g = c.execute("SELECT chars_per_step, coins_per_step FROM lt_groups WHERE id=?", (gid,)).fetchone()
    m = c.execute("SELECT chars_left FROM lt_members WHERE id=?", (mid,)).fetchone()
    left = m["chars_left"] + chars
    steps = left // g["chars_per_step"]
    coins = steps * g["coins_per_step"]
    left -= steps * g["chars_per_step"]
    c.execute("UPDATE lt_members SET chars=chars+?, chars_left=?, coins=coins+?, paras=paras+? WHERE id=?", (chars, left, coins, paras, mid))
    c.execute("INSERT INTO lt_ledger(group_id, member_id, kind, chars, paras, coins, note, submission_id, ts) VALUES (?,?,?,?,?,?,?,?,?)",
              (gid, mid, "字数", chars, paras, coins, note, submission_id, _now()))
    return coins


def count_chars(text: str) -> int:
    """算字数：去掉空白；[图片] [表情] 这类占位不算。"""
    t = re.sub(r"\[(?:图片|表情|动画表情|语音|视频|文件|合并转发|QQ小程序|分享)[^\]]*\]", "", text or "")
    return len(re.sub(r"\s+", "", t))


def tally(gid: int, items: list[dict], submitter: dict | None = None, qq_group: str = "", pending: bool = False) -> dict:
    """items = [{nickname, text}]（roomlog.parse_quoted_forward 的结果）。按发送者名字合计字数：
    对上注册玩家的直接入账换货币；对不上的记进「未认领」。同一条（名字 + 内容）以前统计过就跳过。
    整次交的内容逐条存进 lt_submissions / lt_sub_lines，后台能追溯。
    pending=True：只留底、占住去重，不入账，等后台 approve()；驳回 reject() 放开去重，可以重新交。
    返回 {id: 留底编号, credited: [(名字, 字数, 换到的货币)], unclaimed: [(名字, 字数)], skipped: 条数, total: 字数}。"""
    now = _now()
    who_of: dict[str, dict | None] = {}
    per: dict[str, int] = {}
    pp: dict[str, int] = {}                     # 段数：一条消息算一段
    rows = []
    skipped = 0
    with get_conn() as c:
        sid = c.execute("INSERT INTO lt_submissions(group_id, submitter_id, submitter_name, qq_group, ts) VALUES (?,?,?,?,?)",
                        (gid, submitter["id"] if submitter else None, submitter["name"] if submitter else "", qq_group, now)).lastrowid
    for it in items:
        who = _clean_name(it.get("nickname", "")) or "（没有名字）"
        if who not in who_of:
            who_of[who] = member_by_name(gid, who)
        text = (it.get("text") or "").strip()
        n = count_chars(text)
        h = hashlib.sha1((who + "\n" + _WS.sub("", text)).encode()).hexdigest()
        with get_conn() as c:
            dup = bool(n) and c.execute("SELECT 1 FROM lt_counted WHERE group_id=? AND hash=?", (gid, h)).fetchone() is not None
            if n and not dup:
                c.execute("INSERT INTO lt_counted(group_id, hash, ts) VALUES (?,?,?)", (gid, h, now))
                per[who] = per.get(who, 0) + n
                pp[who] = pp.get(who, 0) + 1
        skipped += dup
        m = who_of[who]
        rows.append((sid, len(rows) + 1, who, m["id"] if m else None, text[:5000], n, int(bool(n) and not dup), h))
    with get_conn() as c:
        c.executemany("INSERT INTO lt_sub_lines(submission_id, seq, sender, member_id, text, chars, counted, hash) VALUES (?,?,?,?,?,?,?,?)", rows)
        total = sum(per.values())
        c.execute("UPDATE lt_submissions SET n_lines=?, n_counted=?, total_chars=?, status=? WHERE id=?",
                  (len(rows), sum(r[6] for r in rows), total, "pending" if pending and total else "approved", sid))
    who_sub = submitter["name"] if submitter else "管理员"
    if pending and total:
        event(gid, submitter["id"] if submitter else None, "字数", f"{who_sub} 交了字数统计 #{sid}：{total} 字，等批准", sid)
        return {"id": sid, "pending": True, "per": [((who_of[w] or {}).get("name") or w, n, bool(who_of[w]), pp[w]) for w, n in per.items()],
                "credited": [], "unclaimed": [], "skipped": skipped, "total": total}
    credited, unclaimed = _book(gid, sid, per, who_of, pp)
    if total:
        event(gid, submitter["id"] if submitter else None, "字数", f"{who_sub} 交了字数统计 #{sid}，直接入账：" + _book_text(gid, credited, unclaimed), sid)
    return {"id": sid, "pending": False, "credited": credited, "unclaimed": unclaimed, "skipped": skipped, "total": total}


def _book(gid: int, sid: int, per: dict, who_of: dict, pp: dict | None = None) -> tuple[list, list]:
    """把一次统计的字数入账：对上玩家的换货币，对不上的进「未认领」。"""
    credited, unclaimed = [], []
    now = _now()
    with get_conn() as c:
        for who, n in per.items():
            m = who_of[who]
            if m:
                note = f"字数统计 #{sid}" + (f"（{who}）" if who != m["name"] else "")
                credited.append((m["name"], n, _credit(c, gid, m["id"], n, note, sid, (pp or {}).get(who, 0))))
            else:
                c.execute("INSERT INTO lt_unclaimed(group_id, name, chars, paras, last_ts) VALUES (?,?,?,?,?) "
                          "ON CONFLICT(group_id, name) DO UPDATE SET chars=chars+excluded.chars, paras=paras+excluded.paras, last_ts=excluded.last_ts",
                          (gid, who, n, (pp or {}).get(who, 0), now))
                unclaimed.append((who, n))
    return credited, unclaimed


def _book_text(gid: int, credited: list, unclaimed: list) -> str:
    cur = get_group(gid)["currency"]
    parts = [f"{n} +{k}字" + (f"（+{c}{cur}）" if c else "") for n, k, c in credited]
    if unclaimed:
        parts.append("没对上：" + "、".join(f"{n} {k}字" for n, k in unclaimed))
    return "；".join(parts) or "没有可记的"


def approve(sid: int) -> tuple[bool, str]:
    """批准一次等待中的字数统计：按现在的名字归属入账（等待期间注册 / 认领的名字也算对上）。"""
    s, lines = submission(sid)
    if not s or s["status"] != "pending":
        return False, "这次统计不在等待批准。"
    with get_conn() as c:
        if c.execute("UPDATE lt_submissions SET status='approved', reviewed_ts=? WHERE id=? AND status='pending'", (_now(), sid)).rowcount == 0:
            return False, "这次统计已经处理过了。"
    gid = s["group_id"]
    per: dict[str, int] = {}
    pp: dict[str, int] = {}
    for ln in lines:
        if ln["counted"]:
            per[ln["sender"]] = per.get(ln["sender"], 0) + ln["chars"]
            pp[ln["sender"]] = pp.get(ln["sender"], 0) + 1
    who_of = {w: member_by_name(gid, w) for w in per}
    with get_conn() as c:
        for w, m in who_of.items():
            if m:
                c.execute("UPDATE lt_sub_lines SET member_id=? WHERE submission_id=? AND sender=?", (m["id"], sid, w))
    credited, unclaimed = _book(gid, sid, per, who_of, pp)
    event(gid, s["submitter_id"], "字数", f"批准了 #{sid}（{s['submitter_name'] or '管理员'} 交的）：" + _book_text(gid, credited, unclaimed), sid)
    return True, f"#{sid} 已入账：" + ("、".join(f"{n} +{k}字" for n, k, _ in credited) or "没有对上的玩家") + \
        (f"；没对上的 {len(unclaimed)} 个名字进了「未认领」" if unclaimed else "")


def reject(sid: int) -> tuple[bool, str]:
    """驳回：不入账，这次的发言放开去重（改好以后可以重新交）。"""
    s, lines = submission(sid)
    if not s or s["status"] != "pending":
        return False, "这次统计不在等待批准。"
    with get_conn() as c:
        if c.execute("UPDATE lt_submissions SET status='rejected', reviewed_ts=? WHERE id=? AND status='pending'", (_now(), sid)).rowcount == 0:
            return False, "这次统计已经处理过了。"
        for ln in lines:
            if ln["counted"] and ln["hash"]:
                c.execute("DELETE FROM lt_counted WHERE group_id=? AND hash=?", (s["group_id"], ln["hash"]))
    event(s["group_id"], s["submitter_id"], "字数", f"驳回了 #{sid}（{s['submitter_name'] or '管理员'} 交的 {s['total_chars']} 字），没有入账", sid)
    return True, f"#{sid} 已驳回，没有入账。"


def pending_submissions(gid: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lt_submissions WHERE group_id=? AND status='pending' ORDER BY id", (gid,))]


def pending_chars(mid: int) -> int:
    with get_conn() as c:
        r = c.execute("SELECT COALESCE(SUM(l.chars),0) AS n FROM lt_sub_lines l JOIN lt_submissions s ON s.id=l.submission_id "
                      "WHERE l.member_id=? AND l.counted=1 AND s.status='pending'", (mid,)).fetchone()
    return r["n"]


def submissions(gid: int, limit: int = 200) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lt_submissions WHERE group_id=? ORDER BY id DESC LIMIT ?", (gid, limit))]


def submission(sid: int) -> tuple[dict | None, list[dict]]:
    with get_conn() as c:
        s = c.execute("SELECT * FROM lt_submissions WHERE id=?", (sid,)).fetchone()
        if not s:
            return None, []
        lines = c.execute("SELECT l.*, m.name AS member_name FROM lt_sub_lines l LEFT JOIN lt_members m ON m.id=l.member_id "
                          "WHERE l.submission_id=? ORDER BY l.seq", (sid,)).fetchall()
    return dict(s), [dict(x) for x in lines]


def member_submission_lines(mid: int, limit: int = 300) -> list[dict]:
    """记在这个玩家名下的每一条发言（追溯用）。"""
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT l.*, s.ts FROM lt_sub_lines l JOIN lt_submissions s ON s.id=l.submission_id "
            "WHERE l.member_id=? ORDER BY s.id DESC, l.seq LIMIT ?", (mid, limit))]


def list_unclaimed(gid: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lt_unclaimed WHERE group_id=? ORDER BY last_ts DESC", (gid,))]


def claim(gid: int, name: str, mid: int) -> int:
    """「未认领」里这个名字的字数转给玩家（同时把名字加进别名，以后自动认）。返回转过去的字数。"""
    name = _clean_name(name)
    with get_conn() as c:
        r = c.execute("SELECT chars, paras FROM lt_unclaimed WHERE group_id=? AND name=?", (gid, name)).fetchone()
        if not r:
            return 0
        c.execute("DELETE FROM lt_unclaimed WHERE group_id=? AND name=?", (gid, name))
        m = c.execute("SELECT name, aliases FROM lt_members WHERE id=?", (mid,)).fetchone()
        if m and name != m["name"] and name not in m["aliases"].split(","):
            c.execute("UPDATE lt_members SET aliases=? WHERE id=?", (",".join(x for x in (m["aliases"], name) if x), mid))
        c.execute("UPDATE lt_sub_lines SET member_id=? WHERE member_id IS NULL AND sender=? "
                  "AND submission_id IN (SELECT id FROM lt_submissions WHERE group_id=?)", (mid, name, gid))
        _credit(c, gid, mid, r["chars"], f"认领「{name}」的字数", paras=r["paras"])
    event(gid, mid, "字数", f"「{name}」的 {r['chars']} 字认领给了 {m['name'] if m else '?'}")
    return r["chars"]


def drop_unclaimed(gid: int, name: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lt_unclaimed WHERE group_id=? AND name=?", (gid, name))


def adjust_coins(mid: int, delta: int, note: str = "") -> tuple[bool, str]:
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    if m["coins"] + delta < 0:
        return False, f"余额不够：现在 {m['coins']}。"
    with get_conn() as c:
        c.execute("UPDATE lt_members SET coins=coins+? WHERE id=?", (delta, mid))
        _log(c, m["group_id"], mid, "货币", coins=delta, note=note)
    return True, ""


def set_coins(mid: int, value: int, note: str = "后台改余额") -> tuple[bool, str]:
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    if value < 0:
        return False, "余额不能是负数。"
    return adjust_coins(mid, value - m["coins"], note) if value != m["coins"] else (True, "")


def adjust_chars(mid: int, delta: int, note: str = "后台调整") -> tuple[bool, str]:
    """后台改字数：加的照常换货币；减的只减累计字数和零头，不倒扣货币。"""
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    with get_conn() as c:
        if delta >= 0:
            _credit(c, m["group_id"], mid, delta, note)
        else:
            d = max(delta, -m["chars"])
            c.execute("UPDATE lt_members SET chars=chars+?, chars_left=MAX(0, chars_left+?) WHERE id=?", (d, d, mid))
            _log(c, m["group_id"], mid, "字数", chars=d, note=note)
    return True, ""


def ranking(gid: int, by: str = "chars", limit: int = 20) -> list[dict]:
    col = "coins" if by == "coins" else "chars"
    with get_conn() as c:
        return [dict(r) for r in c.execute(f"SELECT id, name, chars, paras, coins FROM lt_members WHERE group_id=? ORDER BY {col} DESC, id LIMIT ?", (gid, limit))]


# ======================== 属性 ========================

def attr_defs(gid: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lt_attr_defs WHERE group_id=? ORDER BY rowid", (gid,))]


def _opt_int(v):
    if v is None or str(v).strip() == "":
        return None
    return int(str(v).strip())


def set_attr_def(gid: int, name: str, default=0, min_value=None, max_value=None) -> tuple[bool, str]:
    name = _clean_name(name)
    if not name or len(name) > 12 or not re.fullmatch(r"[A-Za-z一-鿿_]+", name):
        return False, "属性名 1~12 个字，只能是汉字、字母、下划线。"
    try:
        dv, lo, hi = int(default or 0), _opt_int(min_value), _opt_int(max_value)
    except ValueError:
        return False, "默认值、最小值、最大值要填整数（最小最大可以空着）。"
    if lo is not None and hi is not None and lo > hi:
        return False, "最小值比最大值还大。"
    name = _norm_attr(name)
    with get_conn() as c:
        if len(attr_defs(gid)) >= MAX_ATTRS and not c.execute("SELECT 1 FROM lt_attr_defs WHERE group_id=? AND name=?", (gid, name)).fetchone():
            return False, f"属性最多 {MAX_ATTRS} 项。"
        c.execute("INSERT INTO lt_attr_defs(group_id, name, default_value, min_value, max_value) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(group_id, name) DO UPDATE SET default_value=excluded.default_value, min_value=excluded.min_value, max_value=excluded.max_value",
                  (gid, name, dv, lo, hi))
        for m in c.execute("SELECT id FROM lt_members WHERE group_id=?", (gid,)).fetchall():     # 已注册的人补上默认值
            c.execute("INSERT OR IGNORE INTO lt_attrs(member_id, name, value) VALUES (?,?,?)", (m["id"], name, dv))
    return True, ""


def delete_attr_def(gid: int, name: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lt_attr_defs WHERE group_id=? AND name=?", (gid, name))
        c.execute("DELETE FROM lt_attrs WHERE name=? AND member_id IN (SELECT id FROM lt_members WHERE group_id=?)", (name, gid))


def _norm_attr(name: str) -> str:
    """存的名字按管理员写的来（HP 就存 HP，不换成「生命」），英文统一大写；查的时候 _def_names 再认别名。"""
    n = name.strip()
    return n.upper() if n.isascii() else n


def _def_names(gid: int, name: str) -> list[str]:
    """查属性时依次试的名字：原名 → 跑团的别名组（力量 = STR、理智 = SAN……）。"""
    from .trpg import alias_names
    return list(dict.fromkeys([_norm_attr(name), name.strip(), *alias_names(name)]))


def list_attrs(mid: int) -> list[tuple[str, int]]:
    """面板：按后台定义的顺序排，后台没定义的（检定写回的）排后面。"""
    m = get_member(mid)
    if not m:
        return []
    order = {d["name"]: i for i, d in enumerate(attr_defs(m["group_id"]))}
    with get_conn() as c:
        rows = [(r["name"], r["value"]) for r in c.execute("SELECT name, value FROM lt_attrs WHERE member_id=? ORDER BY rowid", (mid,))]
    return sorted(rows, key=lambda r: order.get(r[0], 10_000))


def get_attr(mid: int, name: str) -> int | None:
    m = get_member(mid)
    if not m:
        return None
    with get_conn() as c:
        for n in _def_names(m["group_id"], name):
            r = c.execute("SELECT value FROM lt_attrs WHERE member_id=? AND name=?", (mid, n)).fetchone()
            if r:
                return r["value"]
            d = c.execute("SELECT default_value FROM lt_attr_defs WHERE group_id=? AND name=?", (m["group_id"], n)).fetchone()
            if d:
                return d["default_value"]
    return None


def parse_attrs(text: str) -> list[tuple[str, int]]:
    out: dict[str, int] = {}
    for mm in _ATTR.finditer((text or "").translate(_FULL)):
        out[_norm_attr(mm.group(1))] = int(mm.group(2))
    return list(out.items())


def set_attrs(mid: int, pairs: list[tuple[str, int]], only_defined: bool = False) -> tuple[bool, str]:
    """写属性，按后台的最小 / 最大值夹住。only_defined：玩家自己改时只能改后台定义了的属性。
    检定写回（理智损失、成长）时同名的后台定义找不到，就按跑团别名找已有的那一项。"""
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    gid = m["group_id"]
    defs = {d["name"]: d for d in attr_defs(gid)}
    with get_conn() as c:
        have = {r["name"] for r in c.execute("SELECT name FROM lt_attrs WHERE member_id=?", (mid,))}
        resolved = []
        for name, value in pairs:
            key = next((n for n in _def_names(gid, name) if n in defs or n in have), _norm_attr(name))
            if only_defined and key not in defs:
                return False, f"没有「{name}」这个属性。能改的：" + ("、".join(defs) or "（后台还没设属性）")
            d = defs.get(key)
            if d:
                if d["min_value"] is not None:
                    value = max(value, d["min_value"])
                if d["max_value"] is not None:
                    value = min(value, d["max_value"])
            resolved.append((key, max(-MAX_VALUE, min(MAX_VALUE, value))))
        if len(have | {k for k, _ in resolved}) > MAX_ATTRS:
            return False, f"属性最多 {MAX_ATTRS} 项。"
        for key, value in resolved:
            c.execute("INSERT INTO lt_attrs(member_id, name, value) VALUES (?,?,?) "
                      "ON CONFLICT(member_id, name) DO UPDATE SET value=excluded.value", (mid, key, value))
    return True, ""


def delete_attr(mid: int, name: str) -> bool:
    with get_conn() as c:
        return c.execute("DELETE FROM lt_attrs WHERE member_id=? AND name=?", (mid, name)).rowcount > 0


# ======================== 背包 ========================

def items(mid: int) -> list[tuple[str, int]]:
    with get_conn() as c:
        return [(r["name"], r["qty"]) for r in c.execute("SELECT name, qty FROM lt_items WHERE member_id=? AND qty>0 ORDER BY rowid", (mid,))]


def item_qty(mid: int, name: str) -> int:
    with get_conn() as c:
        r = c.execute("SELECT qty FROM lt_items WHERE member_id=? AND name=?", (mid, _clean_name(name))).fetchone()
    return r["qty"] if r else 0


def _add_item(c, mid: int, name: str, qty: int) -> None:
    c.execute("INSERT INTO lt_items(member_id, name, qty) VALUES (?,?,?) "
              "ON CONFLICT(member_id, name) DO UPDATE SET qty=qty+excluded.qty", (mid, name, qty))
    c.execute("DELETE FROM lt_items WHERE member_id=? AND name=? AND qty<=0", (mid, name))


def adjust_item(mid: int, name: str, delta: int, note: str = "") -> tuple[bool, str]:
    m = get_member(mid)
    name = _clean_name(name)
    if not m:
        return False, "没有这个玩家。"
    if not name or len(name) > MAX_ITEM:
        return False, f"物品名要 1~{MAX_ITEM} 个字。"
    have = item_qty(mid, name)
    if have + delta < 0:
        return False, f"「{name}」只有 {have} 个。"
    if have + delta > MAX_QTY:
        return False, f"一种物品最多 {MAX_QTY} 个。"
    with get_conn() as c:
        _add_item(c, mid, name, delta)
        _log(c, m["group_id"], mid, "物品", item=name, qty=delta, note=note)
    return True, ""


def parse_give(text: str) -> tuple[str, str, int]:
    """「对象 物品 数量」→ (对象, 物品, 数量)；数量可以省（=1），也可以写「x3」「×3」「3个」。"""
    parts = (text or "").translate(_FULL).split()
    if len(parts) < 2:
        return "", "", 0
    qty = 1
    m = re.fullmatch(r"[xX×*]?(\d{1,6})[个件份枚张瓶块颗只]?", parts[-1])
    if m and len(parts) >= 3:
        qty = int(m.group(1))
        parts = parts[:-1]
    else:
        m = re.fullmatch(r"(.+?)[xX×*](\d{1,6})", parts[-1])
        if m:
            parts[-1], qty = m.group(1), int(m.group(2))
    return parts[0].lstrip("@"), " ".join(parts[1:]), qty


def give(src_mid: int, dst_mid: int, what: str, qty: int) -> tuple[bool, str]:
    """玩家之间赠送物品；物品名写成货币名就是转货币。"""
    src, dst = get_member(src_mid), get_member(dst_mid)
    if not src or not dst or src["group_id"] != dst["group_id"]:
        return False, "找不到对方。"
    if src_mid == dst_mid:
        return False, "不能送给自己。"
    if qty < 1:
        return False, "数量至少 1。"
    g = get_group(src["group_id"])
    what = _clean_name(what)
    with get_conn() as c:
        if what == g["currency"]:
            if src["coins"] < qty:
                return False, f"你的{g['currency']}只有 {src['coins']}。"
            c.execute("UPDATE lt_members SET coins=coins-? WHERE id=?", (qty, src_mid))
            c.execute("UPDATE lt_members SET coins=coins+? WHERE id=?", (qty, dst_mid))
            _log(c, g["id"], src_mid, "货币", coins=-qty, note=f"送给 {dst['name']}")
            _log(c, g["id"], dst_mid, "货币", coins=qty, note=f"{src['name']} 送的")
            done = f"送给「{dst['name']}」{qty} {g['currency']}。你还剩 {src['coins'] - qty}。"
    if what == g["currency"]:                       # 动态在事务提交以后再写（另开连接，免得锁库）
        event(g["id"], src_mid, "赠送", f"{src['name']} 送给 {dst['name']} {qty} {g['currency']}")
        return True, done
    with get_conn() as c:
        have = c.execute("SELECT qty FROM lt_items WHERE member_id=? AND name=?", (src_mid, what)).fetchone()
        have = have["qty"] if have else 0
        if have < qty:
            return False, (f"你的背包里「{what}」只有 {have} 个。" if have else f"你的背包里没有「{what}」。用 /背包 看看有什么。")
        _add_item(c, src_mid, what, -qty)
        _add_item(c, dst_mid, what, qty)
        _log(c, g["id"], src_mid, "物品", item=what, qty=-qty, note=f"送给 {dst['name']}")
        _log(c, g["id"], dst_mid, "物品", item=what, qty=qty, note=f"{src['name']} 送的")
    event(g["id"], src_mid, "赠送", f"{src['name']} 送给 {dst['name']} {what}×{qty}")
    return True, f"送给「{dst['name']}」{what}×{qty}。"


# ======================== 道具和商城 ========================

def item_defs(gid: int, on_sale: bool | None = None) -> list[dict]:
    sql = "SELECT * FROM lt_item_defs WHERE group_id=?" + ("" if on_sale is None else " AND on_sale=?")
    args = (gid,) if on_sale is None else (gid, int(on_sale))
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY sort, rowid", args)]


def item_def(gid: int, name: str) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM lt_item_defs WHERE group_id=? AND name=?", (gid, _clean_name(name))).fetchone()
    return dict(r) if r else None


def save_item_def(gid: int, name: str, description: str = "", effects: str = "", on_sale=False, price=0, stock=-1,
                  old_name: str = "", limit_total=0, limit_daily=0) -> tuple[bool, str]:
    """新建或修改道具。effects 写「HP+10 理智-5」，只能改后台定义了的属性。old_name 不同 = 改名（背包里的同名物品跟着改）。"""
    name = _clean_name(name)
    if not name or len(name) > MAX_ITEM:
        return False, f"道具名要 1~{MAX_ITEM} 个字。"
    g = get_group(gid)
    if name in (g["currency"], "字数", "段数"):
        return False, "道具不能跟货币或「字数」同名。"
    effects = (effects or "").strip()
    ops = parse_adjust(effects)
    if effects and not ops:
        return False, "效果的格式：HP+10 理智-5 力量=60（可以写好几项，空着 = 不能使用）。"
    defs = {d["name"] for d in attr_defs(gid)}
    bad = [n for n, _, _ in ops if not any(x in defs for x in _def_names(gid, n))]
    if bad:
        return False, "效果里的属性要先在「属性面板」里定义：" + "、".join(bad)
    try:
        price, stock = int(price or 0), int(-1 if str(stock).strip() == "" else stock)
        limit_total, limit_daily = max(0, int(limit_total or 0)), max(0, int(limit_daily or 0))
    except ValueError:
        return False, "价格、库存要填整数（库存空着 = 不限）。"
    if price < 0:
        return False, "价格不能是负数。"
    stock = max(-1, stock)
    old_name = _clean_name(old_name) or name
    with get_conn() as c:
        if old_name != name:
            if c.execute("SELECT 1 FROM lt_item_defs WHERE group_id=? AND name=?", (gid, name)).fetchone():
                return False, f"已经有叫「{name}」的道具了。"
            c.execute("UPDATE lt_item_defs SET name=? WHERE group_id=? AND name=?", (name, gid, old_name))
            for r in c.execute("SELECT member_id, qty FROM lt_items WHERE name=? AND member_id IN (SELECT id FROM lt_members WHERE group_id=?)",
                               (old_name, gid)).fetchall():
                c.execute("DELETE FROM lt_items WHERE member_id=? AND name=?", (r["member_id"], old_name))
                _add_item(c, r["member_id"], name, r["qty"])
        c.execute("INSERT INTO lt_item_defs(group_id, name, description, effects, on_sale, price, stock, limit_total, limit_daily) VALUES (?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(group_id, name) DO UPDATE SET description=excluded.description, effects=excluded.effects, "
                  "on_sale=excluded.on_sale, price=excluded.price, stock=excluded.stock, limit_total=excluded.limit_total, limit_daily=excluded.limit_daily",
                  (gid, name, (description or "").strip()[:200], effects, 1 if on_sale else 0, price, stock, limit_total, limit_daily))
    return True, ""


def delete_item_def(gid: int, name: str) -> None:
    """只删登记，玩家背包里已有的留着（变成普通物品）。"""
    with get_conn() as c:
        c.execute("DELETE FROM lt_item_defs WHERE group_id=? AND name=?", (gid, name))


def buy(mid: int, name: str, qty: int = 1) -> tuple[bool, str]:
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    g = get_group(m["group_id"])
    if qty < 1 or qty > 999:
        return False, "数量写 1~999。"
    with get_conn() as c:
        d = c.execute("SELECT * FROM lt_item_defs WHERE group_id=? AND name=? AND on_sale=1", (g["id"], _clean_name(name))).fetchone()
        if not d:
            return False, f"商城里没有「{name}」。发 /商城 看看在卖什么。"
        if d["stock"] >= 0 and d["stock"] < qty:
            return False, f"「{d['name']}」只剩 {d['stock']} 个了。" if d["stock"] else f"「{d['name']}」卖完了。"
        today = _today()
        if d["limit_total"]:
            had = c.execute("SELECT COALESCE(SUM(qty),0) AS n FROM lt_purchases WHERE member_id=? AND item=?", (mid, d["name"])).fetchone()["n"]
            if had + qty > d["limit_total"]:
                return False, f"「{d['name']}」每人最多买 {d['limit_total']} 个，你已经买过 {had} 个。"
        if d["limit_daily"]:
            had = c.execute("SELECT COALESCE(SUM(qty),0) AS n FROM lt_purchases WHERE member_id=? AND item=? AND day=?",
                            (mid, d["name"], today)).fetchone()["n"]
            if had + qty > d["limit_daily"]:
                return False, f"「{d['name']}」每人每天最多买 {d['limit_daily']} 个，你今天已经买过 {had} 个。"
        cost = d["price"] * qty
        cur = c.execute("SELECT coins FROM lt_members WHERE id=?", (mid,)).fetchone()["coins"]
        if cur < cost:
            return False, f"要 {cost} {g['currency']}，你只有 {cur}。"
        if d["stock"] >= 0:
            n = c.execute("UPDATE lt_item_defs SET stock=stock-? WHERE group_id=? AND name=? AND stock>=?", (qty, g["id"], d["name"], qty)).rowcount
            if not n:                                     # 同时有人买：库存已经不够
                return False, f"「{d['name']}」刚被买走，库存不够了。"
        c.execute("UPDATE lt_members SET coins=coins-? WHERE id=?", (cost, mid))
        c.execute("INSERT INTO lt_purchases(member_id, item, qty, day, ts) VALUES (?,?,?,?,?)", (mid, d["name"], qty, today, _now()))
        _add_item(c, mid, d["name"], qty)
        _log(c, g["id"], mid, "货币", coins=-cost, note=f"商城买 {d['name']}×{qty}")
        _log(c, g["id"], mid, "物品", item=d["name"], qty=qty, note="商城购买")
    event(g["id"], mid, "商城", f"{m['name']} 买了 {d['name']}×{qty}，花 {cost} {g['currency']}")
    return True, f"🛒 买到 {d['name']}×{qty}，花了 {cost} {g['currency']}，还剩 {cur - cost}。"


def use(mid: int, name: str) -> tuple[bool, str]:
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    name = _clean_name(name)
    if item_qty(mid, name) < 1:
        return False, f"你的背包里没有「{name}」。"
    d = item_def(m["group_id"], name)
    if not d or not d["effects"]:
        return False, f"「{name}」不能使用。"
    ok, err = adjust_item(mid, name, -1, "使用")
    if not ok:
        return False, err
    ops = parse_adjust(d["effects"])
    out = apply_adjust(mid, [op for op in ops if op[0] not in (get_group(m["group_id"])["currency"], "字数", "段数")], note=f"使用 {name}")
    event(m["group_id"], mid, "道具", f"{m['name']} 用了 {name}：" + "；".join(out))
    return True, f"✨ 用掉 1 个{name}：" + "；".join(out)


def shop_text(gid: int) -> str:
    g = get_group(gid)
    goods = item_defs(gid, on_sale=True)
    if not goods:
        return "商城还没有上架的东西。"
    lines = [f"🏪 「{g['name']}」商城（{g['currency']}）"]
    for d in goods:
        stock = "" if d["stock"] < 0 else ("（卖完了）" if d["stock"] == 0 else f"（剩 {d['stock']}）")
        lim = "、".join(x for x in (f"每人限 {d['limit_total']}" if d["limit_total"] else "", f"每天限 {d['limit_daily']}" if d["limit_daily"] else "") if x)
        stock += f"（{lim}）" if lim else ""
        desc = "　".join(x for x in (d["description"], f"效果：{d['effects']}" if d["effects"] else "") if x)
        lines.append(f"· {d['name']}　{d['price']} {g['currency']}{stock}" + (f"\n　{desc}" if desc else ""))
    lines.append("买：/购买 名字 数量　用：/使用 名字")
    return "\n".join(lines)


# ======================== KPI ========================

KPI_PERIODS = {"week": "本周", "month": "本月", "days": "本期", "all": "累计"}
KPI_MAX_DAYS = 366


def kpi_on(group: dict) -> bool:
    return bool(group.get("kpi_chars") or group.get("kpi_paras"))


def _day0(ts: float) -> date:
    return date.fromtimestamp(ts)


def _ts(d: date) -> int:
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


def kpi_range(group: dict, now: float | None = None) -> tuple[int, int | None]:
    """这一期 [开始, 结束)：每周从周一 0 点，每月从 1 号 0 点，累计从头（没有结束）；
    自定义（days）从 kpi_anchor 那天起每 kpi_days 天一期，到期自动进下一期。还没到第一期的开始日，就按第一期算（还没入账）。"""
    now = now or time.time()
    today = _day0(now)
    period = group.get("kpi_period")
    if period == "all":
        return 0, None
    if period == "month":
        start = today.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return _ts(start), _ts(nxt)
    if period == "days":
        n = min(max(int(group.get("kpi_days") or 7), 1), KPI_MAX_DAYS)
        anchor = _day0(group.get("kpi_anchor") or now)
        k = max((today - anchor).days, 0) // n
        start = anchor + timedelta(days=k * n)
        return _ts(start), _ts(start + timedelta(days=n))
    start = today - timedelta(days=today.weekday())
    return _ts(start), _ts(start + timedelta(days=7))


def kpi_start(group: dict, now: float | None = None) -> int:
    return kpi_range(group, now)[0]


def kpi_label(group: dict, now: float | None = None) -> str:
    """「本周」「本月」「累计」；自定义周期写成「本期（11-25～12-04）」。"""
    if group.get("kpi_period") != "days":
        return KPI_PERIODS.get(group.get("kpi_period"), "本周")
    s, e = kpi_range(group, now)
    last = date.fromtimestamp(e) - timedelta(days=1)
    return f"本期（{date.fromtimestamp(s):%m-%d}～{last:%m-%d}）"


def kpi_left_days(group: dict, now: float | None = None) -> int | None:
    """这一期还剩几天（含今天）；累计返回 None。"""
    _, e = kpi_range(group, now)
    return None if e is None else (date.fromtimestamp(e) - _day0(now or time.time())).days


def kpi_progress(gid: int) -> dict[int, dict]:
    """{玩家 id: {chars, paras, ok}}：这一期入账的字数和段数（按入账 / 批准的时间算，后台加减的也算）。"""
    g = get_group(gid)
    start = kpi_start(g)
    with get_conn() as c:
        rows = {r["member_id"]: (r["c"] or 0, r["p"] or 0) for r in c.execute(
            "SELECT member_id, SUM(chars) AS c, SUM(paras) AS p FROM lt_ledger WHERE group_id=? AND kind='字数' AND ts>=? GROUP BY member_id",
            (gid, start))}
    out = {}
    for m in list_members(gid):
        ch, pa = rows.get(m["id"], (0, 0))
        ch, pa = max(ch, 0), max(pa, 0)
        out[m["id"]] = {"chars": ch, "paras": pa,
                        "ok": (not g["kpi_chars"] or ch >= g["kpi_chars"]) and (not g["kpi_paras"] or pa >= g["kpi_paras"])}
    return out


def kpi_text(mid: int) -> str:
    m = get_member(mid)
    g = get_group(m["group_id"])
    if not kpi_on(g):
        return ""
    p = kpi_progress(g["id"]).get(mid, {"chars": 0, "paras": 0, "ok": False})
    parts = []
    if g["kpi_chars"]:
        parts.append(f"字数 {p['chars']}/{g['kpi_chars']}")
    if g["kpi_paras"]:
        parts.append(f"段数 {p['paras']}/{g['kpi_paras']}")
    left = kpi_left_days(g)
    tail = f"（还剩 {left} 天）" if g["kpi_period"] == "days" and left is not None else ""
    return f"🎯 {kpi_label(g)} KPI{tail}：" + "　".join(parts) + ("　✅ 达标" if p["ok"] else "　还没达标")


# ======================== 条目（玩家自己的备忘录） ========================
# 玩家随时加一栏（「魔杖」），下面写几个字段（木材：冬青）。都不是必填，栏目名、字段名都是自己起的。
# 存在 lt_members.notes（JSON，按加的先后排）。

NOTE_MAX_SECTIONS = 30
NOTE_MAX_FIELDS = 30
NOTE_MAX_NAME = 12
NOTE_MAX_VALUE = 200
_KV = re.compile(r"^\s*([^\s:：]{1,40})\s*(?:[:：]\s*|\s+|$)(.*)$")
_INLINE_KV = re.compile(r"([^\s:：]{1,40})\s*[:：]\s*(.*?)(?=\s+[^\s:：]{1,40}\s*[:：]|$)")


def parse_note_fields(text: str) -> list[tuple[str, str]]:
    """一栏下面的字段。几种写法都认：
    「木材 冬青」「木材：冬青」；一行写好几个「木材：冬青 杖芯：凤凰羽毛」；换行一行一个；只写「木材」= 这个字段先空着。"""
    text = (text or "").strip()
    if not text:
        return []
    lines = [x for x in text.splitlines() if x.strip()]
    if len(lines) == 1 and len(re.findall(r"[:：]", text)) > 1:
        return [(k.strip(), v.strip()) for k, v in _INLINE_KV.findall(text)]
    out = []
    for line in lines:
        m = _KV.match(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def _note_name(s: str) -> str:
    return _clean_name(s).strip("【】[]「」:：")


def set_note(mid: int, section: str, pairs: list[tuple[str, str]]) -> tuple[bool, str]:
    """加一栏（已经有就接着写），写进这些字段（同名的覆盖）。pairs 可以是空的：只加一栏。"""
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    section = _note_name(section)
    if not section or len(section) > NOTE_MAX_NAME:
        return False, f"栏目名要 1～{NOTE_MAX_NAME} 个字。"
    notes = m["notes"]
    if section not in notes and len(notes) >= NOTE_MAX_SECTIONS:
        return False, f"最多 {NOTE_MAX_SECTIONS} 栏，先删掉不要的（/删条目 栏目名）。"
    fields = dict(notes.get(section, {}))
    for k, v in pairs:
        k = _note_name(k)
        if not k or len(k) > NOTE_MAX_NAME:
            return False, f"字段名「{k[:20]}」要 1～{NOTE_MAX_NAME} 个字。"
        if len(v) > NOTE_MAX_VALUE:
            return False, f"「{k}」写得太长了，一个字段最多 {NOTE_MAX_VALUE} 字。"
        if k not in fields and len(fields) >= NOTE_MAX_FIELDS:
            return False, f"一栏最多 {NOTE_MAX_FIELDS} 个字段。"
        fields[k] = v
    notes[section] = fields
    _save_notes(mid, notes)
    return True, ""


def delete_note(mid: int, section: str, field: str = "") -> tuple[bool, str]:
    """删一整栏，或者删这一栏里的某个字段。"""
    m = get_member(mid)
    notes = m["notes"] if m else {}
    section, field = _note_name(section), _note_name(field)
    if section not in notes:
        return False, f"没有「{section}」这一栏。"
    if field:
        if field not in notes[section]:
            return False, f"「{section}」里没有「{field}」。"
        del notes[section][field]
    else:
        del notes[section]
    _save_notes(mid, notes)
    return True, ""


def _save_notes(mid: int, notes: dict) -> None:
    with get_conn() as c:
        c.execute("UPDATE lt_members SET notes=? WHERE id=?", (json.dumps(notes, ensure_ascii=False), mid))


def notes_to_text(notes: dict) -> str:
    """后台文本框里的样子：栏目名单独一行，下面一行一个「字段：内容」，栏目之间空一行。"""
    return "\n\n".join("【" + s + "】" + "".join(f"\n{k}：{v}" for k, v in f.items()) for s, f in notes.items())


def parse_notes_text(text: str) -> tuple[dict | None, str]:
    """后台文本框 → 条目。「【魔杖】」是栏目名；空行后面第一行不带冒号的也算栏目名。下面的「字段：内容」归它。"""
    notes: dict[str, dict[str, str]] = {}
    cur, fresh = None, True                      # fresh：这一行是不是一段的第一行（开头或者空行后面）
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            fresh = True
            continue
        head = re.fullmatch(r"[【\[]\s*(.+?)\s*[】\]]", line)
        is_head = bool(head) or (fresh and not re.search(r"[:：]", line))
        fresh = False
        if is_head:
            cur = _note_name(head.group(1) if head else line)
            if not cur or len(cur) > NOTE_MAX_NAME:
                return None, f"栏目名「{cur[:20]}」要 1～{NOTE_MAX_NAME} 个字。"
            notes.setdefault(cur, {})
            continue
        if cur is None:
            return None, f"「{line[:20]}」上面要先写栏目名，比如【魔杖】。"
        for k, v in parse_note_fields(line):
            k = _note_name(k)
            if not k or len(k) > NOTE_MAX_NAME:
                return None, f"字段名「{k[:20]}」要 1～{NOTE_MAX_NAME} 个字。"
            notes[cur][k] = v[:NOTE_MAX_VALUE]
    if len(notes) > NOTE_MAX_SECTIONS or any(len(f) > NOTE_MAX_FIELDS for f in notes.values()):
        return None, f"最多 {NOTE_MAX_SECTIONS} 栏、每栏 {NOTE_MAX_FIELDS} 个字段。"
    return notes, ""


def replace_notes(mid: int, notes: dict) -> None:
    _save_notes(mid, notes)


def notes_text(mid: int, section: str = "") -> str:
    m = get_member(mid)
    notes = m["notes"]
    if section:
        section = _note_name(section)
        if section not in notes:
            return f"{m['name']} 还没有「{section}」这一栏。"
        notes = {section: notes[section]}
    if not notes:
        return f"📒 {m['name']} 还没有条目。\n加一栏：/写条目 魔杖 木材：冬青 杖芯：凤凰羽毛"
    out = [f"📒 {m['name']} 的条目"]
    for s, f in notes.items():
        out.append(f"【{s}】" + ("" if f else "（还没写内容）"))
        out += [f"  {k}：{v}" if v else f"  {k}：—" for k, v in f.items()]
    return "\n".join(out)


# ======================== 签到 ========================

_REWARD = re.compile(r"^(.+?)\s*[+＋]?\s*(\d{1,7})\s*(?:[-~～到]\s*(\d{1,7}))?$")


def parse_rewards(text: str) -> tuple[list[tuple[str, int, int]], list[str]]:
    """签到奖励：一行一项，「银两 10-30」「回血药水 1」「HP 1~3」→ [(名字, 最少, 最多)]。返回 (能认的, 认不出的行)。"""
    ok, bad = [], []
    for ln in (text or "").translate(_FULL).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = _REWARD.match(ln)
        if not m:
            bad.append(ln)
            continue
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        ok.append((_clean_name(m.group(1)), min(lo, hi), max(lo, hi)))
    return ok, bad


def checkin(mid: int) -> tuple[bool, str]:
    """每天一次。每项奖励在「最少-最多」之间随机；名字是货币名就给货币，是属性就加属性，其余放进背包。"""
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    g = get_group(m["group_id"])
    if not g["checkin_on"]:
        return False, "这个长期群没开签到。"
    rewards, _ = parse_rewards(g["checkin_rewards"])
    if not rewards:
        return False, "管理员还没设签到奖励。"
    today = _today()
    with get_conn() as c:
        try:
            c.execute("INSERT INTO lt_checkins(member_id, day, ts) VALUES (?,?,?)", (mid, today, _now()))
        except Exception:  # noqa: BLE001  主键冲突 = 今天签过了
            return False, "今天已经签过到了，明天再来～"
    ops = [(name, "+", random.randint(lo, hi)) for name, lo, hi in rewards]
    ops = [op for op in ops if op[2] > 0]
    apply_adjust(mid, ops, note="签到")
    got = "、".join(f"{name}+{n}" for name, _, n in ops) or "这次什么都没抽到"
    with get_conn() as c:
        c.execute("UPDATE lt_checkins SET rewards=? WHERE member_id=? AND day=?", (got, mid, today))
        days = c.execute("SELECT COUNT(*) AS n FROM lt_checkins WHERE member_id=?", (mid,)).fetchone()["n"]
    event(g["id"], mid, "签到", f"{m['name']} 签到（第 {days} 天）：{got}")
    return True, f"✅ {m['name']} 签到成功（累计 {days} 天）：{got}"


def checkins_today(gid: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM lt_checkins k JOIN lt_members m ON m.id=k.member_id WHERE m.group_id=? AND k.day=?",
                         (gid, _today())).fetchone()["n"]


# ======================== 批量发放 ========================

def resolve_targets(gid: int, text: str) -> tuple[list[dict], list[str]]:
    """「全员」「全部」→ 所有注册玩家；否则按 、 , ， 空格 分开的名字找。返回 (找到的玩家, 没找到的名字)。"""
    text = (text or "").strip()
    if text in ("全员", "全部", "所有人", "大家"):
        return list_members(gid), []
    found, missing = [], []
    for name in re.split(r"[、,，\s]+", text):
        if not name:
            continue
        m = member_by_name(gid, name)
        if m and m["id"] not in {x["id"] for x in found}:
            found.append(m)
        elif not m:
            missing.append(name)
    return found, missing


def bulk_adjust(mids: list[int], ops: list[tuple[str, str, int]], note: str = "批量发放") -> list[tuple[str, list[str]]]:
    res = [(get_member(mid)["name"], apply_adjust(mid, ops, note)) for mid in mids if get_member(mid)]
    if res:
        gid = get_member(mids[0])["group_id"]
        names = "、".join(n for n, _ in res[:8]) + (f" 等 {len(res)} 人" if len(res) > 8 else "")
        event(gid, None, "管理", f"{note}：给 {names} " + " ".join(f"{n}{o}{v}" for n, o, v in ops))
    return res


# ======================== 合成 / 兑换 ========================

_PART = re.compile(r"^(.+?)\s*(?:[×xX*]\s*(\d{1,6})|(\d{1,6}))?$")


def parse_parts(text: str) -> tuple[list[tuple[str, int]], list[str]]:
    """「碎片×3 银两×10」「碎片*3、木头」→ [(名字, 数量)]（不写数量 = 1）。返回 (能认的, 认不出的)。"""
    ok, bad = [], []
    for tok in re.split(r"[、,，+＋\s]+", (text or "").translate(_FULL)):
        if not tok:
            continue
        m = _PART.match(tok)
        name = _clean_name(m.group(1)) if m else ""
        if not m or not name or name.isdigit():
            bad.append(tok)
            continue
        ok.append((name, int(m.group(2) or m.group(3) or 1)))
    return ok, bad


def recipes(gid: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lt_recipes WHERE group_id=? ORDER BY rowid", (gid,))]


def save_recipe(gid: int, name: str, inputs: str, outputs: str, old_name: str = "") -> tuple[bool, str]:
    name = _clean_name(name)
    if not name or len(name) > MAX_ITEM:
        return False, f"配方名要 1~{MAX_ITEM} 个字。"
    ins, bad1 = parse_parts(inputs)
    outs, bad2 = parse_parts(outputs)
    if bad1 or bad2 or not ins or not outs:
        return False, "材料和产物都要写，格式：碎片×3 银两×10（不写数量 = 1）。" + (f"看不懂：{'、'.join(bad1 + bad2)}" if bad1 or bad2 else "")
    old_name = _clean_name(old_name) or name
    with get_conn() as c:
        if old_name != name:
            if c.execute("SELECT 1 FROM lt_recipes WHERE group_id=? AND name=?", (gid, name)).fetchone():
                return False, f"已经有叫「{name}」的配方了。"
            c.execute("DELETE FROM lt_recipes WHERE group_id=? AND name=?", (gid, old_name))
        c.execute("INSERT INTO lt_recipes(group_id, name, inputs, outputs) VALUES (?,?,?,?) "
                  "ON CONFLICT(group_id, name) DO UPDATE SET inputs=excluded.inputs, outputs=excluded.outputs",
                  (gid, name, " ".join(f"{n}×{q}" for n, q in ins), " ".join(f"{n}×{q}" for n, q in outs)))
    return True, ""


def delete_recipe(gid: int, name: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM lt_recipes WHERE group_id=? AND name=?", (gid, name))


def find_recipe(gid: int, name: str) -> dict | None:
    """按配方名找，找不到再按产物名找（「/合成 稀有剑」也认）。"""
    name = _clean_name(name)
    rs = recipes(gid)
    return next((r for r in rs if r["name"] == name), None) or \
        next((r for r in rs if name in [n for n, _ in parse_parts(r["outputs"])[0]]), None)


def craft(mid: int, name: str, times: int = 1) -> tuple[bool, str]:
    m = get_member(mid)
    if not m:
        return False, "没有这个玩家。"
    g = get_group(m["group_id"])
    r = find_recipe(g["id"], name)
    if not r:
        return False, f"没有「{name}」这个配方。发 /合成表 看看。"
    if times < 1 or times > 99:
        return False, "次数写 1~99。"
    ins, _ = parse_parts(r["inputs"])
    outs, _ = parse_parts(r["outputs"])
    with get_conn() as c:
        lack = []
        for n, q in ins:
            need = q * times
            have = c.execute("SELECT coins FROM lt_members WHERE id=?", (mid,)).fetchone()["coins"] if n == g["currency"] else \
                (lambda row: row["qty"] if row else 0)(c.execute("SELECT qty FROM lt_items WHERE member_id=? AND name=?", (mid, n)).fetchone())
            if have < need:
                lack.append(f"{n} 要 {need}，你有 {have}")
        if lack:
            return False, f"材料不够（{r['name']}×{times}）：" + "；".join(lack)
        for n, q in ins:
            if n == g["currency"]:
                c.execute("UPDATE lt_members SET coins=coins-? WHERE id=?", (q * times, mid))
                _log(c, g["id"], mid, "货币", coins=-q * times, note=f"合成 {r['name']}")
            else:
                _add_item(c, mid, n, -q * times)
                _log(c, g["id"], mid, "物品", item=n, qty=-q * times, note=f"合成 {r['name']} 用掉")
        for n, q in outs:
            if n == g["currency"]:
                c.execute("UPDATE lt_members SET coins=coins+? WHERE id=?", (q * times, mid))
                _log(c, g["id"], mid, "货币", coins=q * times, note=f"合成 {r['name']}")
            else:
                _add_item(c, mid, n, q * times)
                _log(c, g["id"], mid, "物品", item=n, qty=q * times, note=f"合成 {r['name']} 得到")
    event(g["id"], mid, "合成", f"{m['name']} 合成 {r['name']}×{times}")
    return True, f"🔨 {r['name']}×{times}：用掉 " + "、".join(f"{n}×{q * times}" for n, q in ins) + \
        "，得到 " + "、".join(f"{n}×{q * times}" for n, q in outs) + "。"


def recipes_text(gid: int) -> str:
    rs = recipes(gid)
    if not rs:
        return "还没有配方。"
    return "🔨 合成 / 兑换表\n" + "\n".join(f"· {r['name']}：{r['inputs']} → {r['outputs']}" for r in rs) + \
        "\n用法：/合成 配方名 次数（次数不写 = 1）"


# ======================== 帮助 ========================

def help_text(group: dict | None, is_admin: bool = False) -> str:
    cur = group["currency"] if group else "货币"
    head = f"📖 长期群「{group['name']}」指令" if group else "📖 长期群指令（这个群还没接长期群：管理员发 /切换长期群 名字）"
    lines = [head, "",
             "【我自己】",
             "/改名 新名字　名字写错了就改，旧名字留着当别名",
             *(["/认人　这个长期群接了好几个 QQ 群、换个群认不出你时：在注册过的群发「认人」拿码，到新群发「认人 码」"]
               if group and group_count(group["id"]) > 1 else []),
             "/注册 名字　注册（再发一次 = 改资料）" + ("，换行写 " + "、".join(f"{k}：…" for k in reg_fields(group)) if group and reg_fields(group) else ""),
             "/我的　看资料、字数、" + cur + "、属性、背包（后面写名字或 @ 人看别人）",
             "/属性　/背包　只看属性 / 只看背包",
             "/st 力量60　改自己的属性" + ("（这个群由管理员改）" if group and not group["self_attrs"] else ""),
             "/条目　看自己的条目（像备忘录，后面写名字或 @ 人看别人，写栏目名只看那一栏）",
             "/写条目 魔杖 木材：冬青 杖芯：凤凰羽毛　加一栏、写字段，都不是必填；可以换行一行一个",
             "/删条目 魔杖　删一栏（/删条目 魔杖 木材 只删一个字段）"]
    if group and group["checkin_on"]:
        lines.append("/签到　每天一次领奖励")
    lines += ["", "【字数和" + cur + "】",
              "/字数统计　长按合并转发 →「回复」→ 发这条，按转发里的名字记字数" + ("，管理员批准后入账" if group and group["count_review"] else ""),
              f"/字数榜　/字数榜 货币　排行" + (f"（{rate_text(group)}）" if group else ""),
              *(["/KPI　看自己这一期的字数、段数有没有达标（后面写名字看别人）"] if group and kpi_on(group) else []),
              "", "【东西】",
              f"/赠送 名字 物品 数量　送人东西（物品写「{cur}」就是转账，也可以 @ 对方）",
              "/商城　/购买 名字 数量　/使用 名字",
              "/合成表　/合成 配方名 次数",
              "", "【检定】", "/ra 侦查　/rt 100 侦查　/sc 1/1d6 …　读你注册的属性面板"]
    if is_admin:
        lines += ["", "【管理员】",
                  f"/调整 名字 力量+5 {cur}-10 苹果+2 字数+300　（=100 是直接改成）",
                  f"/发放 全员 {cur}+50　/发放 阿青、小白 苹果+1",
                  "/切换长期群 名字　/模式 恋综|跑团|长期群",
                  "/写条目 @某人 魔杖 木材：冬青　替别人写（/删条目 @某人 … 一样）",
                  "其余（注册项、兑换比例、属性、道具、商城、配方、签到奖励、批准字数）在后台「长期群」页"]
    return "\n".join(lines)


# ======================== 管理员加减（群里「/调整」、后台共用） ========================

_ADJ = re.compile(r"([^\s+\-=＝:：\d][^\s+\-=＝:：]{0,19}?)\s*([+\-=＝:：])\s*(\d{1,7})(?!\d)")


def parse_adjust(text: str) -> list[tuple[str, str, int]]:
    """「力量+5 金币-10 苹果+2 SAN=50」→ [(名字, '+'/'-'/'=', 数)]。"""
    out = []
    for m in _ADJ.finditer((text or "").translate(_FULL)):
        op = {"＝": "=", ":": "=", "：": "="}.get(m.group(2), m.group(2))
        out.append((m.group(1).strip(), op, int(m.group(3))))
    return out


def apply_adjust(mid: int, ops: list[tuple[str, str, int]], note: str = "管理员调整", log: bool = False) -> list[str]:
    """按名字认是什么：货币名 → 货币；「字数」→ 字数；后台定义的属性或这人已有的属性 → 属性；其余 → 背包物品。
    「=」对货币、属性是改成这个数，对物品是改成这么多个。返回每一项的结果（一行一项）。"""
    m = get_member(mid)
    if not m:
        return ["没有这个玩家。"]
    g = get_group(m["group_id"])
    defs = {d["name"] for d in attr_defs(g["id"])}
    out = []
    for name, op, n in ops:
        m = get_member(mid)
        if name == g["currency"] or name in ("货币", "钱"):
            target = n if op == "=" else m["coins"] + (n if op == "+" else -n)
            ok, err = set_coins(mid, target, note) if op == "=" else adjust_coins(mid, target - m["coins"], note)
            out.append(f"{g['currency']}：{m['coins']} → {target}" if ok else f"{g['currency']}：{err}")
        elif name == "段数":
            delta = (n - m["paras"]) if op == "=" else (n if op == "+" else -n)
            delta = max(delta, -m["paras"])
            with get_conn() as c:
                c.execute("UPDATE lt_members SET paras=paras+? WHERE id=?", (delta, mid))
                c.execute("INSERT INTO lt_ledger(group_id, member_id, kind, paras, note, ts) VALUES (?,?,?,?,?,?)",
                          (g["id"], mid, "字数", delta, note, _now()))
            out.append(f"段数：{m['paras']} → {m['paras'] + delta}")
        elif name == "字数":
            delta = (n - m["chars"]) if op == "=" else (n if op == "+" else -n)
            adjust_chars(mid, delta, note)
            out.append(f"字数：{m['chars']} → {get_member(mid)['chars']}")
        elif any(x in defs for x in _def_names(g["id"], name)) or get_attr(mid, name) is not None:
            cur = get_attr(mid, name) or 0
            target = n if op == "=" else cur + (n if op == "+" else -n)
            ok, err = set_attrs(mid, [(name, target)])
            out.append(f"{name}：{cur} → {get_attr(mid, name)}" if ok else f"{name}：{err}")
        else:
            have = item_qty(mid, name)
            delta = (n - have) if op == "=" else (n if op == "+" else -n)
            ok, err = adjust_item(mid, name, delta, note)
            out.append(f"{name}：{have} → {have + delta}" if ok else err)
    if log:
        event(g["id"], mid, "管理", f"{note}：{get_member(mid)['name']} " + "；".join(out))
    return out


# ======================== 群里显示用 ========================

def panel_text(mid: int) -> str:
    m = get_member(mid)
    g = get_group(m["group_id"]) if m else None
    if not m or not g:
        return "没有这个玩家。"
    lines = [f"📇 {m['name']}（{g['name']}）"]
    for k in reg_fields(g):
        if m["info"].get(k):
            lines.append(f"{k}：{m['info'][k]}")
    wait = pending_chars(mid)
    lines.append(f"✍️ 累计 {m['chars']} 字 {m['paras']} 段" + (f"（另有 {wait} 字等批准）" if wait else "") + f"　💰 {m['coins']} {g['currency']}")
    if kpi_on(g):
        lines.append(kpi_text(mid))
    at = list_attrs(mid)
    lines.append("📊 " + ("　".join(f"{n} {v}" for n, v in at) if at else "还没有属性"))
    bag = items(mid)
    lines.append("🎒 " + ("、".join(f"{n}×{q}" for n, q in bag) if bag else "背包是空的"))
    if m["notes"]:
        lines.append("📒 条目：" + "、".join(m["notes"]) + "（/条目 看内容）")
    return "\n".join(lines)


def bag_text(mid: int) -> str:
    m = get_member(mid)
    g = get_group(m["group_id"])
    bag = items(mid)
    head = f"🎒 {m['name']} 的背包　💰 {m['coins']} {g['currency']}"
    if not bag:
        return head + "\n（空的）"
    return head + "\n" + "\n".join(f"· {n} ×{q}" for n, q in bag)


def attrs_text(mid: int) -> str:
    m = get_member(mid)
    at = list_attrs(mid)
    if not at:
        return f"「{m['name']}」还没有属性。"
    return f"📊 {m['name']} 的属性\n" + "\n".join(f"{n}：{v}" for n, v in at)


def tally_text(group: dict, res: dict) -> str:
    cur = group["currency"]
    if res.get("pending"):
        lines = [f"📝 收到了（#{res['id']}），这次新记 {res['total']} 字，等管理员在后台批准后入账："]
        lines += [f"· {name} {n} 字 {p} 段" + ("" if ok else "（没对上注册玩家）") for name, n, ok, p in sorted(res["per"], key=lambda r: -r[1])]
        if res["skipped"]:
            lines.append(f"有 {res['skipped']} 条以前统计过，跳过了。")
        lines.append(f"（{rate_text(group)}）")
        return "\n".join(lines)
    lines = [f"📝 统计好了：这次新记 {res['total']} 字。"]
    for name, n, coins in sorted(res["credited"], key=lambda r: -r[1]):
        lines.append(f"· {name} +{n} 字" + (f"，换到 {coins} {cur}" if coins else ""))
    if res["unclaimed"]:
        lines.append("没对上注册玩家（字数先记在名字下面，用这个名字注册或者管理员在后台认领后转过去）：")
        lines += [f"· {name} {n} 字" for name, n in res["unclaimed"]]
    if res["skipped"]:
        lines.append(f"有 {res['skipped']} 条以前统计过，跳过了。")
    lines.append(f"（{rate_text(group)}）")
    return "\n".join(lines)
