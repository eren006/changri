"""定时收集：后台建一个收集（标题、说明、开始/结束时间、重复几天），玩家在群里「收集 内容」提交，后台「收集」页看内容。
重复 N 天 = 每天同一时段再开一轮（第 1 天、第 2 天……），每一轮每人一份，窗口内重复提交会覆盖自己上一份。"""
import time

from .db import get_conn

DAY = 86400
MAX_LEN = 1000
MAX_REPEAT = 60
NO_END = 4102444800            # 2100 年：没填结束时间 = 不限，手动关闭才停


def parse_local(s: str) -> int:
    """网页 datetime-local（2026-11-15T20:00）→ 时间戳；格式不对返回 0。"""
    try:
        return int(time.mktime(time.strptime(s.strip().replace(" ", "T")[:16], "%Y-%m-%dT%H:%M")))
    except (ValueError, OverflowError):
        return 0


def create(title: str, prompt: str, start_ts: int = 0, end_ts: int = 0, repeat_days: int = 1) -> tuple[bool, str]:
    """开始时间不填 = 现在就开始；结束时间不填 = 不限（在后台手动关闭）。网页不再提供「重复几天」，老的重复收集照常工作。"""
    title = title.strip()
    if not title:
        return False, "标题不能空。"
    start_ts = start_ts or int(time.time())
    end_ts = end_ts or NO_END
    if end_ts <= start_ts:
        return False, "结束时间要晚于开始时间。"
    repeat_days = max(1, min(MAX_REPEAT, repeat_days or 1))
    if repeat_days > 1 and end_ts - start_ts > DAY:
        return False, "要重复的话，每轮不能超过 24 小时，不然两轮会叠在一起。"
    with get_conn() as c:
        cur = c.execute("INSERT INTO collections(title, prompt, start_ts, end_ts, repeat_days, created_ts) VALUES(?,?,?,?,?,?)",
                        (title[:40], prompt.strip()[:300], start_ts, end_ts, repeat_days, int(time.time())))
    return True, str(cur.lastrowid)


def get(cid: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()


def window(col, k: int) -> tuple[int, int]:
    return col["start_ts"] + k * DAY, col["end_ts"] + k * DAY


def active_round(col, now: float | None = None) -> int | None:
    """现在正处在第几轮（从 0 数）；不在任何一轮里或已手动关闭返回 None。"""
    now = now or time.time()
    if col["closed"]:
        return None
    for k in range(col["repeat_days"]):
        s, e = window(col, k)
        if s <= now < e:
            return k
    return None


def status(col, now: float | None = None) -> tuple[str, int]:
    """(状态文字, 该显示的轮次)：收集中显示当前轮；否则显示最近一个已开始的轮。"""
    now = now or time.time()
    k = active_round(col, now)
    if col["closed"]:
        return "已关闭", last_round(col, now)
    if k is not None:
        return ("收集中" if col["repeat_days"] == 1 else f"收集中（第 {k + 1}/{col['repeat_days']} 天）"), k
    if now < col["start_ts"]:
        return "未开始", 0
    if now >= window(col, col["repeat_days"] - 1)[1]:
        return "已结束", col["repeat_days"] - 1
    return "等下一轮", last_round(col, now)


def last_round(col, now: float) -> int:
    ks = [k for k in range(col["repeat_days"]) if window(col, k)[0] <= now]
    return ks[-1] if ks else 0


def listing() -> list[dict]:
    now = time.time()
    out = []
    with get_conn() as c:
        for col in c.execute("SELECT * FROM collections ORDER BY id DESC"):
            label, k = status(col, now)
            n = c.execute("SELECT COUNT(*) FROM collect_items WHERE collection_id=?", (col["id"],)).fetchone()[0]
            out.append({**dict(col), "label": label, "round": k, "total": n, "live": active_round(col, now) is not None})
    return out


def open_now() -> list:
    now = time.time()
    with get_conn() as c:
        return [r for r in c.execute("SELECT * FROM collections WHERE closed=0 ORDER BY id") if active_round(r, now) is not None]


def set_closed(cid: int, closed: bool) -> None:
    with get_conn() as c:
        c.execute("UPDATE collections SET closed=? WHERE id=?", (1 if closed else 0, cid))


def delete(cid: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM collections WHERE id=?", (cid,))


def submit(role_id: int, text: str, cid: int | None = None) -> tuple[bool, str]:
    """玩家提交。只有一个正在收集的可以不写编号。返回 (成功, 提示)。"""
    text = text.strip()
    if not text:
        return False, "写点内容再交吧。"
    if len(text) > MAX_LEN:
        return False, f"内容太长了，最多 {MAX_LEN} 字。"
    now = time.time()
    opens = open_now()
    if cid is None:
        if not opens:
            return False, "现在没有正在进行的收集。"
        if len(opens) > 1:
            return False, "现在有好几个收集，要写编号：收集 编号 内容\n" + "\n".join(f"{r['id']}. {r['title']}" for r in opens)
        col = opens[0]
    else:
        col = next((r for r in opens if r["id"] == cid), None)
        if not col:
            return False, f"收集 {cid} 现在没在收集（没开始、已结束或已关闭）。"
    k = active_round(col, now)
    with get_conn() as c:
        had = c.execute("SELECT 1 FROM collect_items WHERE collection_id=? AND round=? AND role_id=?", (col["id"], k, role_id)).fetchone()
        c.execute("INSERT INTO collect_items(collection_id, round, role_id, content, ts) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(collection_id, round, role_id) DO UPDATE SET content=excluded.content, ts=excluded.ts",
                  (col["id"], k, role_id, text, int(now)))
    return True, f"✅ 已{'更新' if had else '提交'}「{col['title']}」" + (f"（第 {k + 1} 天）" if col["repeat_days"] > 1 else "") + "。窗口没关之前再发一次会覆盖这份。"


def describe_open() -> str:
    opens = open_now()
    if not opens:
        return "现在没有正在进行的收集。"
    lines = []
    for r in opens:
        k = active_round(r)
        e = window(r, k)[1]
        end = "不限时" if e >= NO_END else time.strftime("%m-%d %H:%M", time.localtime(e)) + " 截止"
        lines.append(f"{r['id']}. {r['title']}（{end}）" + (f"\n   {r['prompt']}" if r["prompt"] else ""))
    return "📥 正在收集：\n" + "\n".join(lines) + "\n\n提交：收集 内容（有多个时：收集 编号 内容）"


def items(cid: int, k: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT i.*, r.name AS role_name, r.is_npc FROM collect_items i JOIN roles r ON r.id=i.role_id WHERE i.collection_id=? AND i.round=? ORDER BY i.ts", (cid, k))]


def missing(cid: int, k: int) -> list[str]:
    with get_conn() as c:
        return [r["name"] for r in c.execute(
            "SELECT name FROM roles WHERE is_npc=0 AND id NOT IN (SELECT role_id FROM collect_items WHERE collection_id=? AND round=?) ORDER BY id", (cid, k))]


def export_text(col, k: int) -> str:
    rows = items(col["id"], k)
    head = f"{col['title']}" + (f"（第 {k + 1} 天）" if col["repeat_days"] > 1 else "")
    return head + "\n\n" + "\n\n".join(f"【{r['role_name']}】{time.strftime('%m-%d %H:%M', time.localtime(r['ts']))}\n{r['content']}" for r in rows)
