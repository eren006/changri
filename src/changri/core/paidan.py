"""排单（个性化模块，默认关）：业务、客户、额度、单口、订单、散单兑换码、暂停顺延、每日提醒。设计见 docs/排单设计.md。

只管规则和数据，不依赖 NoneBot / Flask。「今天」都可以传进来（测试用），不传就取本地日期。
日期一律 'YYYY-MM-DD'；到期日 / DDL 当天还有效，第二天 0 点起算过期。
"""
from __future__ import annotations

import datetime as _dt
import re
import secrets
import time

from .db import get_config, get_conn, set_config

REMIND_DAYS_QUOTA = (5, 3, 1)          # 额度到期前几天提醒客户
REMIND_DAYS_ORDER = (5, 3, 1, 0)       # 工期 DDL 前几天（0 = 当天）催 owner
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # 去掉 0 O 1 I，免得抄错
_HEAD = re.compile(r"^\s*【\s*([A-Za-z])\s*下单\s*】\s*$")
_NO = re.compile(r"\b([A-Za-z])\s*-\s*(\d{1,6})\b")
_OPTIONAL = "选填"
QQ_FIELD = "QQ号"                       # 下单格式里要有这一栏（私聊 owner 时显示）；认 QQ号 / QQ / qq 号


def enabled() -> bool:
    from . import extensions
    return extensions.is_enabled("paidan")


# ---------- 日期 ----------

def today() -> str:
    return _dt.date.today().isoformat()


def add_days(day: str, n: int) -> str:
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=n)).isoformat()


def days_between(a: str, b: str) -> int:
    """b - a 的天数。"""
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days


def valid_date(s: str) -> bool:
    try:
        _dt.date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


def _now() -> int:
    return int(time.time())


def _md(day: str) -> str:
    d = _dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}"


# ---------- 业务 ----------

def businesses() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_businesses ORDER BY letter").fetchall()


def business(bid: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_businesses WHERE id=?", (bid,)).fetchone()


def business_by_letter(letter: str):
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_businesses WHERE letter=?", (letter.strip().upper(),)).fetchone()


def business_by_group(group_openid: str):
    if not group_openid:
        return None
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_businesses WHERE group_openid=?", (group_openid,)).fetchone()


def save_business(letter: str, name: str, days: int, default_slots: int, template: str, bid: int | None = None) -> tuple[int, str]:
    """新建（bid 为空）或修改业务。返回 (id, 错误)；错误为空 = 成功。"""
    letter = letter.strip().upper()
    if not re.fullmatch(r"[A-Z]", letter):
        return 0, "业务字母要是一个英文字母（单号前缀，比如 A）。"
    if not name.strip():
        return 0, "要写业务名称。"
    if days < 0 or default_slots < 1:
        return 0, "工期天数不能是负数，默认单口至少 1。"
    other = business_by_letter(letter)
    if other and other["id"] != bid:
        return 0, f"字母 {letter} 已经被「{other['name']}」用了。"
    with get_conn() as c:
        if bid:
            c.execute("UPDATE pd_businesses SET letter=?, name=?, days=?, default_slots=?, template=? WHERE id=?",
                      (letter, name.strip(), days, default_slots, template.strip(), bid))
            return bid, ""
        cur = c.execute("INSERT INTO pd_businesses(letter, name, days, default_slots, template, created_ts) VALUES (?,?,?,?,?,?)",
                        (letter, name.strip(), days, default_slots, template.strip(), _now()))
        new_id = cur.lastrowid
        for cl in c.execute("SELECT id FROM pd_clients").fetchall():       # 已有客户也给一个默认单口
            c.execute("INSERT OR IGNORE INTO pd_slots(client_id, business_id, slots) VALUES (?,?,?)", (cl["id"], new_id, default_slots))
        return new_id, ""


def sync_default_slots(bid: int) -> int:
    """把业务的默认单口同步给没单独改过的客户。返回改了几个。"""
    b = business(bid)
    if not b:
        return 0
    with get_conn() as c:
        return c.execute("UPDATE pd_slots SET slots=? WHERE business_id=? AND custom=0 AND slots<>?",
                         (b["default_slots"], bid, b["default_slots"])).rowcount


def order_format(b, sandan: bool = False) -> str:
    """点「获取格式」填进输入框的整段文字。"""
    lines = [f"【{b['letter']}下单】"] + [ln for ln in (b["template"] or "").splitlines() if ln.strip()]
    if template_missing_qq(b["template"] or ""):
        lines.append(QQ_FIELD + "：")        # owner 的格式里漏了也补上：没有 QQ号 交不了单
    if sandan:
        lines.append("兑换码：")
    return "\n".join(lines)


def _fields(template: str) -> list[tuple[str, bool]]:
    """格式里的字段：[(名字, 必填)]。名字里带「选填」的不必填。"""
    out = []
    for ln in template.splitlines():
        m = re.match(r"^\s*([^：:]+?)\s*[：:]", ln)
        if m:
            out.append((m.group(1), _OPTIONAL not in m.group(1)))
    return out


def _is_qq_key(name: str) -> bool:
    return re.sub(r"\s|（.*?）|\(.*?\)", "", name).upper() in ("QQ号", "QQ")


def template_missing_qq(template: str) -> bool:
    """业务的下单格式里没有 QQ号 这一栏：后台编辑业务时要提醒 owner 加上。"""
    return not any(_is_qq_key(name) for name, _ in _fields(template or ""))


def parse_order(text: str) -> dict | None:
    """认出一条下单消息。不是下单格式返回 None；是的话返回 {letter, values, code}。"""
    lines = (text or "").strip().splitlines()
    if not lines:
        return None
    m = _HEAD.match(lines[0])
    if not m:
        return None
    values = {}
    for ln in lines[1:]:
        mm = re.match(r"^\s*([^：:]+?)\s*[：:]\s*(.*)$", ln)
        if mm:
            values[mm.group(1)] = mm.group(2).strip()
    code = values.pop("兑换码", "").strip().upper()
    qq = next((v for k, v in values.items() if _is_qq_key(k)), "")
    return {"letter": m.group(1).upper(), "values": values, "code": code, "qq": re.sub(r"\D", "", qq)}


def missing_fields(b, values: dict) -> list[str]:
    return [name for name, required in _fields(b["template"] or "") if required and not values.get(name, "").strip()]


def check_submission(b, parsed: dict) -> str:
    """提交的下单格式能不能收：必填都填了，并且有「QQ号：数字」（5–12 位）。能收返回空串。"""
    miss = [n for n in missing_fields(b, parsed["values"]) if not _is_qq_key(n)]
    if miss:
        return "还没填：" + "、".join(miss) + "。填好再发一次～"
    raw = next((v for k, v in parsed["values"].items() if _is_qq_key(k)), None)
    if raw is None or not re.fullmatch(r"\d{5,12}", raw.replace(" ", "")):
        return "要写「QQ号：」加你的 QQ 号（数字），没有这一栏交不了单。"
    return ""


# ---------- 客户和单口 ----------

def clients() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_clients ORDER BY id").fetchall()


def client(cid: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_clients WHERE id=?", (cid,)).fetchone()


def client_by_group(group_openid: str):
    if not group_openid:
        return None
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_clients WHERE group_openid=?", (group_openid,)).fetchone()


def create_client(name: str, slots: dict[int, int] | None = None, note: str = "", qq: str = "", real_gid: str = "") -> tuple[int, str]:
    """slots = {业务 id: 单口}，没写的用业务默认。"""
    if not name.strip():
        return 0, "要写客户名字。"
    slots = slots or {}
    with get_conn() as c:
        cid = c.execute("INSERT INTO pd_clients(name, note, qq, real_gid, created_ts) VALUES (?,?,?,?,?)",
                        (name.strip(), note, re.sub(r"\D", "", qq), re.sub(r"\D", "", real_gid), _now())).lastrowid
        for b in c.execute("SELECT id, default_slots FROM pd_businesses").fetchall():
            n = slots.get(b["id"])
            custom = 1 if n is not None and n != b["default_slots"] else 0
            c.execute("INSERT INTO pd_slots(client_id, business_id, slots, custom) VALUES (?,?,?,?)",
                      (cid, b["id"], max(1, n if n is not None else b["default_slots"]), custom))
        return cid, ""


def set_archived(cid: int, archived: bool) -> str:
    """客户已完结 / 恢复。完结时还有在排的单不让完结。"""
    if archived and orders("open", cid=cid):
        return "还有在排的单，先处理完再标记已完结。"
    with get_conn() as c:
        c.execute("UPDATE pd_clients SET archived=? WHERE id=?", (1 if archived else 0, cid))
    return ""


def rename_client(cid: int, name: str, note: str | None = None) -> str:
    if not name.strip():
        return "要写客户名字。"
    with get_conn() as c:
        if note is None:
            c.execute("UPDATE pd_clients SET name=? WHERE id=?", (name.strip(), cid))
        else:
            c.execute("UPDATE pd_clients SET name=?, note=? WHERE id=?", (name.strip(), note, cid))
    return ""


def set_client_qq(cid: int, qq: str) -> str:
    qq = re.sub(r"\D", "", qq or "")
    if qq and not 5 <= len(qq) <= 12:
        return "QQ 号应该是 5–12 位数字。"
    with get_conn() as c:
        c.execute("UPDATE pd_clients SET qq=? WHERE id=?", (qq, cid))
    return ""


def set_client_gid(cid: int, real_gid: str) -> str:
    """客户群的真实群号（选填）。"""
    gid = re.sub(r"\D", "", real_gid or "")
    if gid and not 5 <= len(gid) <= 12:
        return "群号应该是 5–12 位数字。"
    with get_conn() as c:
        c.execute("UPDATE pd_clients SET real_gid=? WHERE id=?", (gid, cid))
    return ""


def slots_of(cid: int, bid: int) -> int:
    with get_conn() as c:
        r = c.execute("SELECT slots FROM pd_slots WHERE client_id=? AND business_id=?", (cid, bid)).fetchone()
        if r:
            return r["slots"]
        b = c.execute("SELECT default_slots FROM pd_businesses WHERE id=?", (bid,)).fetchone()
        return b["default_slots"] if b else 1


def set_slots(cid: int, bid: int, n: int) -> str:
    if n < 1:
        return "单口至少 1。"
    with get_conn() as c:
        c.execute("INSERT INTO pd_slots(client_id, business_id, slots, custom) VALUES (?,?,?,1) "
                  "ON CONFLICT(client_id, business_id) DO UPDATE SET slots=excluded.slots, custom=1", (cid, bid, n))
    return ""


def open_count(cid: int, bid: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM pd_orders WHERE client_id=? AND business_id=? AND status='open'", (cid, bid)).fetchone()[0]


def client_businesses(cid: int, day: str | None = None) -> list:
    """这个客户有有效额度的业务（下单时给按钮用）。"""
    day = day or today()
    with get_conn() as c:
        return c.execute("SELECT DISTINCT b.* FROM pd_businesses b JOIN pd_quotas q ON q.business_id=b.id "
                         "WHERE q.client_id=? AND q.expires>=? AND q.used<q.total ORDER BY b.letter", (cid, day)).fetchall()


# ---------- 额度 ----------

def add_quota(cid: int, bid: int, total: int, expires: str, day: str | None = None) -> tuple[int, str]:
    day = day or today()
    if total < 1:
        return 0, "张数至少 1。"
    if not valid_date(expires):
        return 0, "到期日期不对。"
    if expires < day:
        return 0, "到期日期不能早于今天。"
    with get_conn() as c:
        return c.execute("INSERT INTO pd_quotas(client_id, business_id, total, expires, created_ts) VALUES (?,?,?,?,?)",
                         (cid, bid, total, expires, _now())).lastrowid, ""


def quota(qid: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_quotas WHERE id=?", (qid,)).fetchone()


def quotas_of(cid: int, bid: int | None = None) -> list:
    """客户的全部额度（含过期、用完的），按到期日排。"""
    sql, args = "SELECT * FROM pd_quotas WHERE client_id=?", [cid]
    if bid is not None:
        sql, args = sql + " AND business_id=?", args + [bid]
    with get_conn() as c:
        return c.execute(sql + " ORDER BY expires, id", args).fetchall()


def usable_quotas(cid: int, bid: int, day: str | None = None) -> list:
    """还能扣的：没过期、还有剩余。最早到期的在前。"""
    day = day or today()
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_quotas WHERE client_id=? AND business_id=? AND expires>=? AND used<total "
                         "ORDER BY expires, id", (cid, bid, day)).fetchall()


def quota_left(cid: int, bid: int, day: str | None = None) -> int:
    return sum(q["total"] - q["used"] for q in usable_quotas(cid, bid, day))


def quota_state(q, day: str | None = None) -> str:
    day = day or today()
    if q["expires"] < day:
        return "已过期"
    if q["used"] >= q["total"]:
        return "已用完"
    return "有效"


def extend_quota(qid: int, new_date: str, day: str | None = None, by: str = "owner") -> str:
    """改到期日。只能在过期之前改；过期了就彻底作废。"""
    day = day or today()
    q = quota(qid)
    if not q:
        return "找不到这笔额度。"
    if q["expires"] < day:
        return "这笔额度已经过期作废了，不能延期。"
    if not valid_date(new_date) or new_date < day:
        return "新的到期日期不对（不能早于今天）。"
    if new_date == q["expires"]:
        return ""
    with get_conn() as c:
        c.execute("UPDATE pd_quotas SET expires=? WHERE id=?", (new_date, qid))
        c.execute("INSERT INTO pd_quota_extends(quota_id, old_date, new_date, by, ts) VALUES (?,?,?,?,?)",
                  (qid, q["expires"], new_date, by, _now()))
    return ""


def quota_extends(qid: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_quota_extends WHERE quota_id=? ORDER BY id", (qid,)).fetchall()


# ---------- 暂停 ----------

def is_paused(day: str | None = None) -> bool:
    day = day or today()
    with get_conn() as c:
        return c.execute("SELECT 1 FROM pd_pauses WHERE day=?", (day,)).fetchone() is not None


def pauses(from_day: str | None = None) -> list:
    with get_conn() as c:
        if from_day:
            return c.execute("SELECT * FROM pd_pauses WHERE day>=? ORDER BY day", (from_day,)).fetchall()
        return c.execute("SELECT * FROM pd_pauses ORDER BY day DESC").fetchall()


def add_pause(day: str, today_: str | None = None) -> str:
    """设一天暂停。当天的马上顺延（apply_pause）；以后的到那天 0 点由定时任务顺延。"""
    today_ = today_ or today()
    if not valid_date(day) or day < today_:
        return "只能暂停今天或以后的日子。"
    with get_conn() as c:
        if c.execute("SELECT 1 FROM pd_pauses WHERE day=?", (day,)).fetchone():
            return "这天已经设了暂停。"
        c.execute("INSERT INTO pd_pauses(day, created_ts) VALUES (?,?)", (day, _now()))
    if day == today_:
        apply_pause(day)
    return ""


def cancel_pause(day: str) -> str:
    with get_conn() as c:
        r = c.execute("SELECT applied FROM pd_pauses WHERE day=?", (day,)).fetchone()
        if not r:
            return "这天没有设暂停。"
        if r["applied"]:
            return "这天的暂停已经生效（期限已经顺延），不能取消了。"
        c.execute("DELETE FROM pd_pauses WHERE day=?", (day,))
    return ""


def apply_pause(day: str) -> dict:
    """让这天的暂停生效：有效额度到期日、没用的兑换码有效期、在排订单 DDL 各 +1 天。只做一次。"""
    with get_conn() as c:
        r = c.execute("SELECT applied FROM pd_pauses WHERE day=?", (day,)).fetchone()
        if not r or r["applied"]:
            return {}
        qs = c.execute("SELECT id, expires FROM pd_quotas WHERE expires>=? AND used<total", (day,)).fetchall()
        for q in qs:
            new = add_days(q["expires"], 1)
            c.execute("UPDATE pd_quotas SET expires=? WHERE id=?", (new, q["id"]))
            c.execute("INSERT INTO pd_quota_extends(quota_id, old_date, new_date, by, ts) VALUES (?,?,?,'pause',?)",
                      (q["id"], q["expires"], new, _now()))
        codes = c.execute("UPDATE pd_codes SET expires=date(expires, '+1 day') "
                          "WHERE order_id IS NULL AND voided=0 AND expires>=?", (day,)).rowcount
        orders = c.execute("UPDATE pd_orders SET deadline=date(deadline, '+1 day') WHERE status='open'").rowcount
        c.execute("UPDATE pd_pauses SET applied=1 WHERE day=?", (day,))
        return {"quotas": len(qs), "codes": codes, "orders": orders}


def due_pauses(day: str | None = None) -> list[str]:
    """到了还没生效的暂停（定时任务 0 点用）。"""
    day = day or today()
    with get_conn() as c:
        return [r["day"] for r in c.execute("SELECT day FROM pd_pauses WHERE day<=? AND applied=0 ORDER BY day", (day,)).fetchall()]


def pause_notice(cid: int | None, day: str) -> str:
    """暂停时发到客户群（cid 为空 = 散单群）的话。"""
    head = f"📢 今天（{_md(day)}）暂停一天，今天无法下单"
    if cid is None:
        return head + "，没用的兑换码有效期和在排的散单都顺延 1 天～"
    with get_conn() as c:
        has_open = c.execute("SELECT 1 FROM pd_orders WHERE client_id=? AND status='open'", (cid,)).fetchone()
        has_quota = c.execute("SELECT 1 FROM pd_quotas WHERE client_id=? AND expires>=? AND used<total", (cid, day)).fetchone()
    if has_open:
        return head + "。你在排的单和额度到期日都会自动顺延 1 天～"
    return head + "，明天恢复～" + ("额度到期日已顺延 1 天。" if has_quota else "")


def pause_preview(days: list[str]) -> str:
    """提前设暂停时的预告。"""
    return "📢 " + "、".join(_md(d) for d in sorted(days)) + " 将暂停接单，这几天无法下单，期限会自动顺延～"


# ---------- 兑换码（散单） ----------

def gen_codes(bid: int, n: int, expires: str, day: str | None = None) -> tuple[list[str], str]:
    day = day or today()
    if not business(bid):
        return [], "找不到这个业务。"
    if not 1 <= n <= 500:
        return [], "一次生成 1–500 个。"
    if not valid_date(expires) or expires < day:
        return [], "有效期不对（不能早于今天）。"
    out = []
    with get_conn() as c:
        while len(out) < n:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            if c.execute("INSERT OR IGNORE INTO pd_codes(code, business_id, expires, created_ts) VALUES (?,?,?,?)",
                         (code, bid, expires, _now())).rowcount:
                out.append(code)
    return out, ""


def codes(bid: int | None = None) -> list:
    sql, args = "SELECT * FROM pd_codes", []
    if bid is not None:
        sql, args = sql + " WHERE business_id=?", [bid]
    with get_conn() as c:
        return c.execute(sql + " ORDER BY created_ts DESC, code", args).fetchall()


def code_state(r, day: str | None = None) -> str:
    day = day or today()
    if r["voided"]:
        return "已作废"
    if r["order_id"] is not None or r["used_ts"]:
        return "已使用"
    if r["expires"] < day:
        return "已过期"
    return "未使用"


def check_code(code: str, bid: int, day: str | None = None) -> str:
    day = day or today()
    if not code:
        return "要填兑换码。"
    with get_conn() as c:
        r = c.execute("SELECT * FROM pd_codes WHERE code=?", (code.strip().upper(),)).fetchone()
    if not r:
        return "兑换码不对，请检查一下。"
    st = code_state(r, day)
    if st != "未使用":
        return f"这个兑换码{st}。"
    if r["business_id"] != bid:
        b = business(r["business_id"])
        return f"这个兑换码是「{b['name'] if b else '别的业务'}」的，不能下这个业务的单。"
    return ""


# ---------- 下单 ----------

def can_order(cid: int, bid: int, day: str | None = None) -> str:
    """普通客户能不能下这个业务的单。能返回空串。"""
    day = day or today()
    if is_paused(day):
        return "今天暂停接单，明天恢复～"
    cl = client(cid)
    if not cl or cl["archived"]:
        return "你的单口已经完结了，需要的话请联系 owner。"
    b = business(bid)
    if not b:
        return "没有这个业务。"
    if quota_left(cid, bid, day) < 1:
        return f"你的「{b['name']}」没有可用的额度了，请联系 owner。"
    n, cap = open_count(cid, bid), slots_of(cid, bid)
    if n >= cap:
        return f"「{b['name']}」的单口满了（{n}/{cap} 单在排），等出图后再下～"
    return ""


def place_order(bid: int, content: str, *, cid: int | None = None, code: str = "", orderer_openid: str = "",
                orderer_name: str = "", qq: str = "", day: str | None = None, manual: bool = False) -> tuple[dict | None, str]:
    """登记订单。cid 为空 = 散单（要兑换码）。manual=True 是 owner 在网页上补录：不管暂停、单口、额度。
    返回 (订单, 错误)。"""
    day = day or today()
    b = business(bid)
    if not b:
        return None, "没有这个业务。"
    if not manual:
        err = can_order(cid, bid, day) if cid is not None else ("今天暂停接单，明天恢复～" if is_paused(day) else check_code(code, bid, day))
        if err:
            return None, err
    code = code.strip().upper()
    qq = re.sub(r"\D", "", qq or "")
    if not qq and cid is not None:
        cl = client(cid)
        qq = cl["qq"] if cl else ""
    try:
        return _insert_order(b, content, cid, code, orderer_openid, orderer_name, qq, day, manual), ""
    except _CodeTaken:
        return None, "这个兑换码刚被别人用掉了。"


class _CodeTaken(Exception):
    pass


def _insert_order(b, content, cid, code, orderer_openid, orderer_name, qq, day, manual):
    """编号 +1、写订单、占兑换码在同一个事务里；兑换码被抢先用掉就整个回滚。不用 RETURNING（老 SQLite 没有）。"""
    bid = b["id"]
    with get_conn() as c:
        c.execute("UPDATE pd_businesses SET seq=seq+1 WHERE id=?", (bid,))
        seq = c.execute("SELECT seq FROM pd_businesses WHERE id=?", (bid,)).fetchone()[0]
        no = f"{b['letter']}-{seq:04d}"
        oid = c.execute("INSERT INTO pd_orders(no, business_id, client_id, orderer_openid, orderer_name, qq, redeem_code, content, deadline, created_ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (no, bid, cid, orderer_openid, orderer_name, qq, code if cid is None else "", content, add_days(day, b["days"]), _now())).lastrowid
        if cid is None and code:
            n = c.execute("UPDATE pd_codes SET order_id=?, used_by=?, used_ts=? WHERE code=? AND order_id IS NULL AND voided=0",
                          (oid, orderer_openid, _now(), code)).rowcount
            if not n and not manual:
                raise _CodeTaken()                                   # 事务回滚
    return order(oid)


def order(oid: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_orders WHERE id=?", (oid,)).fetchone()


def order_by_no(no: str):
    m = _NO.search(no or "")
    if not m:
        return None
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_orders WHERE no=?", (f"{m.group(1).upper()}-{int(m.group(2)):04d}",)).fetchone()


def find_no(text: str) -> str:
    """从一段文字里找单号（A-12 → A-0012）。没有返回空串。"""
    m = _NO.search(text or "")
    return f"{m.group(1).upper()}-{int(m.group(2)):04d}" if m else ""


def orders(status: str | None = None, bid: int | None = None, cid: int | None = None) -> list:
    sql, args = "SELECT * FROM pd_orders WHERE 1=1", []
    if status:
        sql, args = sql + " AND status=?", args + [status]
    if bid is not None:
        sql, args = sql + " AND business_id=?", args + [bid]
    if cid is not None:
        sql, args = sql + " AND client_id=?", args + [cid]
    with get_conn() as c:
        return c.execute(sql + " ORDER BY id DESC", args).fetchall()


def who(o) -> str:
    """订单是谁的（显示用）。"""
    if o["client_id"] is None:
        return f"散单·{o['orderer_name']}" if o["orderer_name"] else "散单"
    cl = client(o["client_id"])
    return cl["name"] if cl else "（已删除的客户）"


def new_order_dm(o) -> str:
    """下单后私聊 owner 的那条：谁、几点、QQ 号、有没有兑换码、下单内容。"""
    t = time.localtime(o["created_ts"])
    lines = [f"【{o['qq'] or '没填QQ'}/{who(o)} 已排单-{o['no']}-DDL {_md(o['deadline'])}】",     # 作者定的播报格式
             f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d} 下单",
             f"兑换码：{'有：' + o['redeem_code'] if o['redeem_code'] else '无'}"]
    cl = client(o["client_id"]) if o["client_id"] is not None else None
    if cl and cl["real_gid"]:
        lines.append(f"客户群：{cl['real_gid']}")
    body = [ln for ln in (o["content"] or "").splitlines() if ln.strip() and not _HEAD.match(ln)]
    return "\n".join(lines + body)


def edit_content(oid: int, content: str) -> str:
    """owner 在网页上改下单内容（客户填错了）。只能改在排的单。"""
    o = order(oid)
    if not o or o["status"] != "open":
        return "只能改在排的单。"
    if not content.strip():
        return "内容不能是空的。"
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET content=? WHERE id=?", (content.strip(), oid))
    return ""


def order_target(o) -> tuple[str, str, str]:
    """订单通知发到哪：(群, @谁的 openid, @谁的名字)。没绑定返回空群。"""
    if o["client_id"] is None:
        return get_config("pd_sandan_group", ""), o["orderer_openid"], o["orderer_name"]
    cl = client(o["client_id"])
    return (cl["group_openid"], cl["member_openid"], cl["name"]) if cl else ("", "", "")


def _at(openid: str, name: str) -> str:
    """qqsend 认这个开头，变成真的 @。openid 不像样或者名字里有花括号会让它认不出来、原样露在消息里，先清掉。"""
    if not openid or not re.fullmatch(r"\w+", openid):
        return ""
    return f"{{{{AT:{openid}:{re.sub(r'[{}]', '', name or '')[:20]}}}}}"


def done_notice(o, day: str | None = None) -> str:
    """网页上扣完张数后发给客户的话。"""
    gid, at, name = order_target(o)
    head = _at(at, name) + f"{o['no']} 已完成"
    if o["client_id"] is None:
        return head + "～"
    b = business(o["business_id"])
    left = quota_left(o["client_id"], o["business_id"], day)
    text = head + f"，扣 {o['charged']} 张，{b['name'] if b else ''}还剩 {left} 张。"
    if o["shortfall"]:
        text += f"额度不够，还差 {o['shortfall']} 张，owner 会跟你联系。"
    return text


def cancel_notice(o) -> str:
    gid, at, name = order_target(o)
    text = _at(at, name) + f"{o['no']} 已取消。"
    if o["redeem_code"]:
        text += "兑换码已作废。"
    return text + "有问题请联系 owner。"


def cancel_order(oid: int) -> str:
    """owner 取消在排的单。散单的兑换码直接作废，不退回。"""
    o = order(oid)
    if not o or o["status"] != "open":
        return "只能取消在排的单。"
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET status='cancelled', done_ts=? WHERE id=?", (_now(), oid))
        if o["redeem_code"]:
            c.execute("UPDATE pd_codes SET voided=1 WHERE code=?", (o["redeem_code"],))
    return ""


# ---------- 补图、锁定、发送 ----------

def is_sandan_group(group_openid: str) -> bool:
    return bool(group_openid) and get_config("pd_sandan_group", "") == group_openid


def _own(o, group_openid: str) -> bool:
    """这单是不是这个群的：客户的单只认他的客户群，散单只认散单群。"""
    if o["client_id"] is None:
        return is_sandan_group(group_openid)
    cl = client(o["client_id"])
    return bool(cl) and cl["group_openid"] == group_openid


NOTE_MAX = 500


def add_note(no: str, group_openid: str, text: str, now: int | None = None):
    """客户下单后补充需求：接在下单内容后面，标上时间。锁定了图也能补充文字；完成、取消的不行。返回 (订单, 错误)。"""
    o = order_by_no(no)
    if not o:
        return None, f"找不到单号 {no}。"
    if not _own(o, group_openid):
        return None, f"{o['no']} 不是这个群的单。"
    if o["status"] != "open":
        return None, f"{o['no']} 已{'完成' if o['status'] == 'done' else '取消'}，不能再补充了。"
    text = (text or "").strip()
    if not text:
        return None, f"要写补充的内容，例：补充 {o['no']} 背景换成蓝色"
    if len(text) > NOTE_MAX:
        return None, f"补充内容太长了，最多 {NOTE_MAX} 字，可以分几次发。"
    t = time.localtime(now or _now())
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET content=content||? WHERE id=?",
                  (f"\n【补充 {t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}】{text}", o["id"]))
    return order(o["id"]), ""


def check_upload(no: str, group_openid: str):
    """客户群 / 散单群里给单号补图前的检查。返回 (订单, 错误)。"""
    o = order_by_no(no)
    if not o:
        return None, f"找不到单号 {no}。"
    if not _own(o, group_openid):
        return None, f"{o['no']} 不是这个群的单。"
    if o["status"] == "done":
        return None, f"{o['no']} 已出图，无法补图。"
    if o["status"] == "cancelled":
        return None, f"{o['no']} 已取消。"
    if o["locked"]:
        return None, f"{o['no']} 已锁定，无法继续上传。需要补图请联系 owner。"
    return o, ""


def record_upload(oid: int, n: int = 1) -> None:
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET images_in=images_in+? WHERE id=?", (n, oid))


def lock_uploads(no: str, group_openid: str):
    """已上传所有图。返回 (订单, 错误)。"""
    o, err = check_upload(no, group_openid)
    if err:
        return None, err
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET locked=1 WHERE id=?", (o["id"],))
    return order(o["id"]), ""


def unlock_uploads(oid: int) -> str:
    """网页上的「打开传图」。"""
    o = order(oid)
    if not o or o["status"] != "open":
        return "只能给在排的单打开传图。"
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET locked=0 WHERE id=?", (oid,))
    return ""


def deliver_target(no: str):
    """owner 在工作群「发送 单号」：返回 (订单, 发到哪个群, @谁的 openid, @谁的名字, 错误)。"""
    o = order_by_no(no)
    if not o:
        return None, "", "", "", f"找不到单号 {no}。"
    if o["status"] != "open":
        return None, "", "", "", f"{o['no']} 已{'完成' if o['status'] == 'done' else '取消'}，不能再发送。"
    if o["client_id"] is None:
        gid = get_config("pd_sandan_group", "")
        if not gid:
            return None, "", "", "", "还没绑定散单群。"
        return o, gid, o["orderer_openid"], o["orderer_name"], ""
    cl = client(o["client_id"])
    if not cl or not cl["group_openid"]:
        return None, "", "", "", f"{who(o)} 还没绑定客户群。"
    return o, cl["group_openid"], cl["member_openid"], cl["name"], ""


def record_delivery(oid: int, n: int = 1) -> None:
    with get_conn() as c:
        c.execute("UPDATE pd_orders SET images_out=images_out+? WHERE id=?", (n, oid))


# ---------- 完成、扣张数 ----------

def plan_charge(oid: int, n: int, first_quota: int | None = None, day: str | None = None) -> tuple[list[tuple[int, int]], int, str]:
    """算出要从哪几笔各扣几张，不改数据。返回 ([(额度 id, 张数)], 差几张, 错误)。散单返回 ([], 0, '')。"""
    day = day or today()
    o = order(oid)
    if not o:
        return [], 0, "找不到这单。"
    if n < 0:
        return [], 0, "张数不能是负数。"
    if o["client_id"] is None:
        return [], 0, ""
    qs = list(usable_quotas(o["client_id"], o["business_id"], day))
    if first_quota is not None:
        pick = next((q for q in qs if q["id"] == first_quota), None)
        if pick is None:
            return [], 0, "选的那笔额度已经过期或用完了。"
        qs.remove(pick)
        qs.insert(0, pick)
    plan, need = [], n
    for q in qs:
        if need <= 0:
            break
        take = min(need, q["total"] - q["used"])
        plan.append((q["id"], take))
        need -= take
    return plan, need, ""


def complete_order(oid: int, n: int, first_quota: int | None = None, day: str | None = None) -> tuple[dict | None, str]:
    """网页上填完张数：扣额度（跨笔，不够就扣到 0 并记下差几张），订单变成已完成。"""
    o = order(oid)
    if not o or o["status"] != "open":
        return None, "只能完成在排的单。"
    plan, short, err = plan_charge(oid, n, first_quota, day)
    if err:
        return None, err
    with get_conn() as c:
        for qid, k in plan:
            c.execute("UPDATE pd_quotas SET used=used+? WHERE id=?", (k, qid))
            c.execute("INSERT INTO pd_charges(order_id, quota_id, n) VALUES (?,?,?)", (oid, qid, k))
        c.execute("UPDATE pd_orders SET status='done', charged=?, shortfall=?, done_ts=? WHERE id=?",
                  (n, short if o["client_id"] is not None else 0, _now(), oid))
    return {"plan": plan, "shortfall": short}, ""


def charges(oid: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_charges WHERE order_id=?", (oid,)).fetchall()


def recharge(oid: int, n: int, first_quota: int | None = None, day: str | None = None) -> tuple[dict | None, str]:
    """扣错了：原样退回各笔，再按新的张数 / 额度重扣。"""
    o = order(oid)
    if not o or o["status"] != "done":
        return None, "只能改已完成的单。"
    with get_conn() as c:
        for ch in c.execute("SELECT * FROM pd_charges WHERE order_id=?", (oid,)).fetchall():
            c.execute("UPDATE pd_quotas SET used=MAX(0, used-?) WHERE id=?", (ch["n"], ch["quota_id"]))
        c.execute("DELETE FROM pd_charges WHERE order_id=?", (oid,))
        c.execute("UPDATE pd_orders SET status='open', charged=0, shortfall=0, done_ts=0 WHERE id=?", (oid,))
    res, err = complete_order(oid, n, first_quota, day)
    if err:                               # 重扣失败：先按原张数默认扣回去，不让单子停在半路
        complete_order(oid, o["charged"], None, day)
    return res, err


def shortfall_orders(cid: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM pd_orders WHERE client_id=? AND shortfall>0 ORDER BY id DESC", (cid,)).fetchall()


# ---------- 每日提醒 ----------

def quota_reminders(day: str | None = None) -> list[dict]:
    """要提醒客户的快到期额度：[{client, quota, left, days}]。暂停那天不发。"""
    day = day or today()
    if is_paused(day):
        return []
    out = []
    with get_conn() as c:
        rows = c.execute("SELECT q.*, c.name AS cname FROM pd_quotas q JOIN pd_clients c ON c.id=q.client_id "
                         "WHERE q.used<q.total AND q.expires>=? AND c.group_openid<>'' AND c.archived=0 ORDER BY q.expires", (day,)).fetchall()
    for q in rows:
        d = days_between(day, q["expires"])
        if d in REMIND_DAYS_QUOTA:
            out.append({"client_id": q["client_id"], "quota": q, "left": q["total"] - q["used"], "days": d})
    return out


def quota_reminder_text(q, left: int) -> str:
    b = business(q["business_id"])
    return f"你的{b['name'] if b else ''}额度还剩 {left} 张，将于 {_md(q['expires'])} 到期，过期作废，请尽快使用～"


def work_reminders(day: str | None = None) -> dict[int, dict]:
    """每个业务工作群要催的：{业务 id: {due: {天数: [订单]}, overdue: [(订单, 超期天数)], pending: [订单]}}。
    发过图的单不再催 DDL，改进「待扣张数」。暂停那天不发。"""
    day = day or today()
    if is_paused(day):
        return {}
    out: dict[int, dict] = {}
    for o in orders("open"):
        e = out.setdefault(o["business_id"], {"due": {}, "overdue": [], "pending": []})
        if o["images_out"] > 0:
            e["pending"].append(o)
            continue
        d = days_between(day, o["deadline"])
        if d < 0:
            e["overdue"].append((o, -d))
        elif d in REMIND_DAYS_ORDER:
            e["due"].setdefault(d, []).append(o)
    return {k: v for k, v in out.items() if v["due"] or v["overdue"] or v["pending"]}


def work_reminder_text(entry: dict) -> str:
    def tag(o):
        return f"{o['no']} {who(o)}"
    lines = ["【工期提醒】"]
    for d in sorted(entry["due"]):
        label = "今日到期" if d == 0 else f"{d} 天后到期"
        lines.append(f"{label}：" + "、".join(tag(o) for o in entry["due"][d]))
    if entry["overdue"]:
        lines.append("已超期：" + "、".join(f"{tag(o)}（超期 {n} 天）" for o, n in sorted(entry["overdue"], key=lambda x: -x[1])))
    if entry["pending"]:
        lines.append("已发图待扣张数：" + "、".join(tag(o) for o in entry["pending"]))
    return "\n".join(lines)


# ---------- 绑定 ----------

def new_bind_code(kind: str, target_id: int = 0) -> str:
    """生成绑定码（同一个对象的旧码作废）。"""
    with get_conn() as c:
        c.execute("DELETE FROM pd_bind_codes WHERE kind=? AND target_id=?", (kind, target_id))
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            if c.execute("INSERT OR IGNORE INTO pd_bind_codes(code, kind, target_id, created_ts) VALUES (?,?,?,?)",
                         (code, kind, target_id, _now())).rowcount:
                return code


def bind_code_of(kind: str, target_id: int = 0) -> str:
    with get_conn() as c:
        r = c.execute("SELECT code FROM pd_bind_codes WHERE kind=? AND target_id=?", (kind, target_id)).fetchone()
        return r["code"] if r else ""


def bind(code: str, group_openid: str, member_openid: str) -> tuple[bool, str]:
    """群里发「绑定 绑定码」。返回 (认得这个码, 回复)。不认得的码返回 (False, '')，留给别的功能处理。"""
    code = (code or "").strip().upper()
    with get_conn() as c:
        r = c.execute("SELECT * FROM pd_bind_codes WHERE code=?", (code,)).fetchone()
        if not r:
            return False, ""
        kind, tid = r["kind"], r["target_id"]
        if kind == "work":
            b = c.execute("SELECT * FROM pd_businesses WHERE id=?", (tid,)).fetchone()
            if not b:
                return True, "这个业务已经删掉了。"
            c.execute("UPDATE pd_businesses SET group_openid='' WHERE group_openid=?", (group_openid,))
            c.execute("UPDATE pd_businesses SET group_openid=? WHERE id=?", (group_openid, tid))
            msg = f"✅ 已绑定：这里是「{b['name']}」的工作群。"
        elif kind == "client":
            cl = c.execute("SELECT * FROM pd_clients WHERE id=?", (tid,)).fetchone()
            if not cl:
                return True, "这个客户已经删掉了。"
            c.execute("UPDATE pd_clients SET group_openid='' WHERE group_openid=? AND id<>?", (group_openid, tid))
            c.execute("UPDATE pd_clients SET group_openid=?, member_openid=? WHERE id=?", (group_openid, member_openid, tid))
            msg = f"✅ 绑定成功，{cl['name']}～以后在这个群里发「下单」就可以下单了。"
        elif kind == "sandan":
            set_config("pd_sandan_group", group_openid)
            msg = "✅ 已绑定：这里是散单群。"
        else:
            set_config("pd_owner_openid", member_openid)
            msg = "✅ 已绑定 owner。"
        c.execute("DELETE FROM pd_bind_codes WHERE code=?", (code,))
    return True, msg


def unbind_client(cid: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE pd_clients SET group_openid='', member_openid='' WHERE id=?", (cid,))


def owner_openid() -> str:
    return get_config("pd_owner_openid", "")


def is_owner(member_openid: str) -> bool:
    return bool(member_openid) and member_openid == owner_openid()


BUNDLE_COOLDOWN = 600          # 同一个群 10 分钟内只通知 owner 一次，免得连发刷屏


def bundle_request(group_openid: str, sender_name: str = "", now: int | None = None) -> tuple[str, str]:
    """客户群 / 散单群里发「发送图包」（图太多，要发合并转发）：私聊 owner 去群里拿。
    返回 (私聊 owner 的话, 群里回复)。不是绑定过的群返回 ('', '')，不响应。"""
    now = now or _now()
    cl = client_by_group(group_openid)
    if cl:
        where, gid = cl["name"], cl["real_gid"]
    elif is_sandan_group(group_openid):
        where, gid = "散单群" + (f"·{sender_name}" if sender_name else ""), get_config("pd_sandan_real_gid", "")
    else:
        return "", ""
    key = f"pd_bundle_last:{group_openid}"
    try:
        last = int(get_config(key, "0") or 0)
    except ValueError:
        last = 0
    if now - last < BUNDLE_COOLDOWN:
        return "", "已经通知过 owner 了，请稍等～"
    set_config(key, str(now))
    t = time.localtime(now)
    dm = (f"【图包】{where} 要发送图包\n"
          f"群号：{gid or '（没填真实群号）'}\n"
          f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}，请到群里拿一下图包")
    return dm, "已通知 owner 来拿图包，请把图包（合并转发）发在群里～"


def set_sandan_real_gid(real_gid: str) -> str:
    gid = re.sub(r"\D", "", real_gid or "")
    if gid and not 5 <= len(gid) <= 12:
        return "群号应该是 5–12 位数字。"
    set_config("pd_sandan_real_gid", gid)
    return ""


def notify_groups() -> list[tuple[int | None, str]]:
    """暂停通知发给谁：所有绑定过的客户群 + 散单群。[(客户 id 或 None, 群)]"""
    out = [(cl["id"], cl["group_openid"]) for cl in clients() if cl["group_openid"] and not cl["archived"]]
    sd = get_config("pd_sandan_group", "")
    if sd:
        out.append((None, sd))
    return out


def my_status(cid: int, day: str | None = None) -> str:
    """客户在群里发「我的额度」看到的：各业务还剩几张、哪天到期，在排的单和 DDL。"""
    day = day or today()
    cl = client(cid)
    if not cl:
        return ""
    lines = [f"【{cl['name']} 的额度】" + ("（已完结）" if cl["archived"] else "")]
    any_q = False
    for b in businesses():
        qs = usable_quotas(cid, b["id"], day)
        if not qs:
            continue
        any_q = True
        parts = "、".join(f"{q['total'] - q['used']} 张 {_md(q['expires'])} 到期" for q in qs)
        lines.append(f"{b['name']}：共 {sum(q['total'] - q['used'] for q in qs)} 张（{parts}）· 单口 {open_count(cid, b['id'])}/{slots_of(cid, b['id'])}")
    if not any_q:
        lines.append("没有有效额度。")
    open_ = orders("open", cid=cid)
    if open_:
        lines.append("在排：" + "、".join(f"{o['no']}（DDL {_md(o['deadline'])}{'，已发图' if o['images_out'] else ''}）" for o in reversed(open_)))
    return "\n".join(lines)


def work_target(bid: int) -> tuple[str, str]:
    """工期催单发到哪：('group', 工作群) 或 ('owner', owner openid，私聊)；都没有返回 ('', '')。"""
    b = business(bid)
    if b and b["group_openid"]:
        return "group", b["group_openid"]
    if owner_openid():
        return "owner", owner_openid()
    return "", ""


async def broadcast(send, bot_id: str, text_for) -> tuple[int, int]:
    """给所有绑定过的客户群和散单群发一条。text_for(客户 id 或 None) -> 文字。返回 (成功, 失败)。"""
    ok = bad = 0
    for cid, gid in notify_groups():
        try:
            await send(bot_id, gid, text_for(cid))
            ok += 1
        except Exception:  # noqa: BLE001 一个群发不出去不影响别的群
            bad += 1
    return ok, bad


# ---------- 后台总览 ----------

def overview(day: str | None = None) -> dict:
    day = day or today()
    open_ = orders("open")
    by_biz = {b["id"]: {"b": b, "open": 0} for b in businesses()}
    for o in open_:
        if o["business_id"] in by_biz:
            by_biz[o["business_id"]]["open"] += 1
    pending = [o for o in open_ if o["images_out"] > 0]
    waiting = [o for o in open_ if o["images_out"] == 0]
    due = [o for o in waiting if 0 <= days_between(day, o["deadline"]) <= 1]
    overdue = [o for o in waiting if o["deadline"] < day]
    with get_conn() as c:
        expiring = c.execute("SELECT q.*, c.name AS cname FROM pd_quotas q JOIN pd_clients c ON c.id=q.client_id "
                             "WHERE q.used<q.total AND q.expires>=? AND q.expires<=? AND c.archived=0 ORDER BY q.expires", (day, add_days(day, 5))).fetchall()
    return {"biz": list(by_biz.values()), "open": len(open_), "pending": pending, "due": due, "overdue": overdue,
            "expiring": expiring, "paused": is_paused(day), "owner": bool(owner_openid())}
