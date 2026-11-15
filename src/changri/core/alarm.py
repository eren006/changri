"""加百列闹钟（额外）：个人提醒（提醒 / 再提醒我 / 我的提醒 / 删除提醒）和管理员的群体闹钟（只在后台页面里管）。"""
import random
import re
import string
import time

from .db import get_conn, get_config, set_config

MAX_PER_ROLE = 20
GROUP_MAX = 50
GROUP_LATE_LIMIT = 3600          # 机器人停机错过超过 1 小时的群体闹钟不补发
WEEK = "日一二三四五六"
_WD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 0, "天": 0}


def _fold(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９：．", "0123456789:."))


def _at(base: float, h: int, mi: int, add_days: int = 0) -> int:
    t = time.localtime(base)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday + add_days, h, mi, 0, 0, 0, -1)))


def parse_time(text: str, now: float | None = None) -> tuple[int, str] | None:
    """「30分钟后 / 2小时后 / 1天后 / 14:30 / 明天9:00 / 每天20:00 / 每周一9:00 / 9月14日 20:00」→ (时间戳, 重复方式)；认不出来返回 None。"""
    now = now or time.time()
    s = _fold(text.strip())
    m = re.fullmatch(r"(\d+)\s*(分钟?|小时|天)后", s)
    if m:
        n = int(m.group(1)) * {"分": 60, "小": 3600, "天": 86400}[m.group(2)[0]]
        return int(now) + n, "none"
    m = re.fullmatch(r"每天\s*(\d{1,2})[:.](\d{2})", s)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        t = _at(now, int(m.group(1)), int(m.group(2)))
        return (t if t > now else t + 86400), "daily"
    m = re.fullmatch(r"每周([一二三四五六日天])\s*(\d{1,2})[:.](\d{2})", s)
    if m and int(m.group(2)) < 24 and int(m.group(3)) < 60:
        wd = _WD[m.group(1)]
        diff = (wd - (time.localtime(now).tm_wday + 1) % 7) % 7
        t = _at(now, int(m.group(2)), int(m.group(3)), diff)
        if t <= now:
            t = _at(now, int(m.group(2)), int(m.group(3)), diff + 7)
        return t, f"weekly:{wd}"
    m = re.fullmatch(r"(明天|后天)\s*(\d{1,2})[:.](\d{2})", s)
    if m and int(m.group(2)) < 24 and int(m.group(3)) < 60:
        return _at(now, int(m.group(2)), int(m.group(3)), 1 if m.group(1) == "明天" else 2), "none"
    m = re.fullmatch(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:.](\d{2})", s)
    if m:
        mo, d, h, mi = (int(x) for x in m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60:
            y = time.localtime(now).tm_year
            t = int(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))
            if t <= now:
                t = int(time.mktime((y + 1, mo, d, h, mi, 0, 0, 0, -1)))
            return t, "none"
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", s)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        t = _at(now, int(m.group(1)), int(m.group(2)))
        return (t if t > now else t + 86400), "none"
    return None


def fmt(ts: int, now: float | None = None) -> str:
    now = now or time.time()
    d = time.localtime(ts)
    hm = f"{d.tm_hour:02d}:{d.tm_min:02d}"
    if time.strftime("%Y%m%d", d) == time.strftime("%Y%m%d", time.localtime(now)):
        return f"今天 {hm}"
    if time.strftime("%Y%m%d", d) == time.strftime("%Y%m%d", time.localtime(now + 86400)):
        return f"明天 {hm}"
    return f"{d.tm_mon}/{d.tm_mday} {hm}"


def repeat_label(rep: str) -> str:
    if rep == "daily":
        return "每天"
    if rep.startswith("weekly:"):
        return "每周" + WEEK[int(rep[7:])]
    return ""


def _new_id(c) -> str:
    while True:
        i = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        if not c.execute("SELECT 1 FROM alarms WHERE id=?", (i,)).fetchone():
            return i


def add(role_id: int, trigger_ts: int, content: str, repeat: str = "none") -> tuple[bool, str]:
    with get_conn() as c:
        if c.execute("SELECT COUNT(*) FROM alarms WHERE role_id=?", (role_id,)).fetchone()[0] >= MAX_PER_ROLE:
            return False, f"已有 {MAX_PER_ROLE} 条提醒了，先删除一些旧的。"
        i = _new_id(c)
        c.execute("INSERT INTO alarms(id, role_id, trigger_ts, content, repeat, created_ts) VALUES(?,?,?,?,?,?)", (i, role_id, trigger_ts, content, repeat, int(time.time())))
    return True, i


def mine(role_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM alarms WHERE role_id=? ORDER BY trigger_ts", (role_id,)).fetchall()


def delete(role_id: int, alarm_id: str) -> str | None:
    """删除成功返回被删提醒的内容，没有这条返回 None。"""
    with get_conn() as c:
        r = c.execute("SELECT content FROM alarms WHERE id=? AND role_id=?", (alarm_id.upper(), role_id)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM alarms WHERE id=?", (alarm_id.upper(),))
    return r["content"]


def last_fired(role_id: int) -> str:
    return get_config(f"alarm_last:{role_id}", "")


def snooze(role_id: int, minutes: int | None) -> tuple[bool, str]:
    text = last_fired(role_id)
    if not text:
        return False, "加百列还没有提醒过你什么……等到提醒触发后再试吧。"
    mins = min(max(minutes or 10, 1), 1440)
    ok, info = add(role_id, int(time.time()) + mins * 60, text)
    return (True, f"好，{mins} 分钟后再提醒你一次。") if ok else (False, info)


def due(now: float | None = None) -> list:
    """到点的个人提醒：重复的顺延到下一次，一次性的删掉；返回要发的 (role_id, content)。"""
    now = int(now or time.time())
    out = []
    with get_conn() as c:
        for r in c.execute("SELECT * FROM alarms WHERE trigger_ts<=? ORDER BY trigger_ts", (now,)).fetchall():
            out.append((r["role_id"], r["content"]))
            step = 86400 if r["repeat"] == "daily" else 7 * 86400 if r["repeat"].startswith("weekly:") else 0
            if step:
                nxt = r["trigger_ts"] + step
                while nxt <= now:
                    nxt += step
                c.execute("UPDATE alarms SET trigger_ts=? WHERE id=?", (nxt, r["id"]))
            else:
                c.execute("DELETE FROM alarms WHERE id=?", (r["id"],))
    for rid, text in out:
        set_config(f"alarm_last:{rid}", text)
    return out


# ---------- 群体闹钟（只在后台管） ----------

def parse_group_lines(text: str, now: float | None = None) -> tuple[list[tuple[int, str]], list[str]]:
    """一行一条「日期 时间 内容」：日期可以是 今天/明天/后天/9月12日/9.12/2026-9-12，时间 20:00 / 20点30。返回 (解析好的, 出错的行说明)。"""
    now = now or time.time()
    ok, errs = [], []
    for raw in text.splitlines():
        line = _fold(raw.strip())
        if not line:
            continue
        m = re.match(r"^(今天|明天|后天|(?:(\d{4})[-/.年])?(\d{1,2})(?:月|[-/.])(\d{1,2})[日号]?)\s*(\d{1,2})(?:[:.](\d{2})|点(半|\d{1,2}分?)?)\s*(.*)$", line)
        if not m:
            errs.append(f"「{raw.strip()[:20]}」格式不对（要写成：9月12日 20:00 内容）")
            continue
        content = m.group(8).strip()
        h = int(m.group(5))
        mi = int(m.group(6)) if m.group(6) else (30 if m.group(7) == "半" else int(re.sub(r"\D", "", m.group(7) or "") or 0))
        if not content or h > 23 or mi > 59:
            errs.append(f"「{raw.strip()[:20]}」缺内容或时间不对")
            continue
        lt = time.localtime(now)
        if m.group(1) in ("今天", "明天", "后天"):
            ts = _at(now, h, mi, {"今天": 0, "明天": 1, "后天": 2}[m.group(1)])
        else:
            y = int(m.group(2)) if m.group(2) else lt.tm_year
            mo, d = int(m.group(3)), int(m.group(4))
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                errs.append(f"「{raw.strip()[:20]}」日期不对")
                continue
            ts = int(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))
            if not m.group(2) and ts < now - 30 * 86400:
                ts = int(time.mktime((y + 1, mo, d, h, mi, 0, 0, 0, -1)))
        if ts <= now:
            errs.append(f"「{raw.strip()[:20]}」这个时间已经过了")
            continue
        ok.append((ts, content))
    return ok, errs


def group_add(items: list[tuple[int, str]]) -> tuple[int, str]:
    with get_conn() as c:
        have = c.execute("SELECT COUNT(*) FROM group_alarms WHERE done=0").fetchone()[0]
        if have + len(items) > GROUP_MAX:
            return 0, f"群体闹钟最多 {GROUP_MAX} 条，现有 {have} 条，这次要加 {len(items)} 条，超出了。"
        n = 0
        for ts, content in items:
            if not c.execute("SELECT 1 FROM group_alarms WHERE done=0 AND trigger_ts=? AND content=?", (ts, content)).fetchone():
                c.execute("INSERT INTO group_alarms(trigger_ts, content, created_ts) VALUES(?,?,?)", (ts, content, int(time.time())))
                n += 1
    return n, ""


def group_list() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM group_alarms WHERE done=0 ORDER BY trigger_ts").fetchall()


def group_delete(alarm_id: int | None) -> int:
    with get_conn() as c:
        if alarm_id is None:
            return c.execute("DELETE FROM group_alarms WHERE done=0").rowcount
        return c.execute("DELETE FROM group_alarms WHERE id=? AND done=0", (alarm_id,)).rowcount


def group_due(now: float | None = None) -> list[str]:
    """到点的群体闹钟：标成已发，返回内容；错过超过 1 小时的直接作废不发。"""
    now = int(now or time.time())
    out = []
    with get_conn() as c:
        for r in c.execute("SELECT * FROM group_alarms WHERE done=0 AND trigger_ts<=? ORDER BY trigger_ts", (now,)).fetchall():
            c.execute("UPDATE group_alarms SET done=1 WHERE id=?", (r["id"],))
            if now - r["trigger_ts"] <= GROUP_LATE_LIMIT:
                out.append(r["content"])
    return out


def player_ids() -> list[int]:
    with get_conn() as c:
        return [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0 ORDER BY id")]
