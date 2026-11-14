"""季度与游戏天数（不依赖 QQ/NoneBot，方便测试）。

一个季度 = 预订（名字 + 档期 + 补戏期 + NPC 名单）→ 开始 → 结束 → 收尾（清空本季数据）。
- 开始后、档期开始日之前是「筹备期」，没有天数；开始日到了自动进入 D0，并打开自动天数和心动信。
- 自动天数：每天 23:59 加一天；档期（含补戏期）结束后自动关闭自动天数和心动信。
- 「每天」的计数（心动信每天投稿上限）按 day_key() 分，天数一变计数就是新的；没有进行中的季度时退回自然日。
"""
import re
import time

from .db import get_config, get_conn, set_config

SEP = re.compile(r"[、,，\s]+")


def today(now: time.struct_time | None = None) -> str:
    return time.strftime("%Y-%m-%d", now or time.localtime())


# ---------- 日期 ----------

def parse_range(text: str, base: str | None = None) -> tuple[str, str] | None:
    """「1115-1120」→ ('YYYY-11-15', 'YYYY-11-20')；也认 2026-11-15~2026-11-20。结束早于开始就算跨年。"""
    text = text.strip()
    m1 = re.fullmatch(r"(\d{2})(\d{2})\s*[-~到至]\s*(\d{2})(\d{2})", text)
    if m1:
        y = int((base or today())[:4])
        a = (y, int(m1[1]), int(m1[2]))
        b = (y, int(m1[3]), int(m1[4]))
        if b < a:
            b = (y + 1, b[1], b[2])
        try:
            sa, sb = "%04d-%02d-%02d" % a, "%04d-%02d-%02d" % b
            time.strptime(sa, "%Y-%m-%d"); time.strptime(sb, "%Y-%m-%d")
        except ValueError:
            return None
        return sa, sb
    m2 = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*[~到至-]\s*(\d{4}-\d{2}-\d{2})", text)
    if m2 and m2[1] <= m2[2]:
        return m2[1], m2[2]
    return None


def parse_mmdd(text: str, base: str | None = None) -> str | None:
    text = text.strip().lstrip("补戏")
    m = re.fullmatch(r"(\d{2})(\d{2})", text)
    if m:
        d = "%s-%s-%s" % ((base or today())[:4], m[1], m[2])
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        d = text
    else:
        return None
    try:
        time.strptime(d, "%Y-%m-%d")
    except ValueError:
        return None
    return d


# ---------- 查询 ----------

def running():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM seasons WHERE status='running'").fetchone()


def booked() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM seasons WHERE status='booked' ORDER BY start_date").fetchall()


def all_seasons() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM seasons ORDER BY start_date DESC, id DESC").fetchall()


def last_end(s) -> str:
    return s["supp_end"] or s["end_date"]


def npc_names(s=None) -> list[str]:
    s = s or running()
    return [x for x in SEP.split(s["npcs"]) if x] if s else []


def current_day() -> int | None:
    v = get_config("game_day", "")
    return int(v) if v.strip().lstrip("-").isdigit() else None


def phase(now: str | None = None) -> str:
    """none 没有季度 / prep 筹备期 / playing 进行中 / over 档期已过还没结束 / ended 已封存待收尾"""
    s = running()
    if s:
        t = now or today()
        if current_day() is None:
            return "prep"
        return "over" if t > last_end(s) else "playing"
    return "ended" if get_config("season_wiped", "1") == "0" else "none"


def day_label() -> str:
    p = phase()
    if p in ("none", "ended"):
        return "—"
    d = current_day()
    return "筹备期" if d is None else f"D{d}"


def day_key() -> str:
    """每日计数用的键：进行中的季度按「季度+天数+重置次数」，否则按自然日。"""
    s, d = running(), current_day()
    if s and d is not None:
        return f"S{s['id']}D{d}.{get_config('day_epoch', '0')}"
    return today()


# ---------- 预订 / 开始 / 结束 ----------

def book(name: str, start: str, end: str, supp: str = "", npcs: str = "") -> tuple[bool, str]:
    name = name.strip()
    if not name or len(name) > 30:
        return False, "季度名要 1–30 个字。"
    if not (start and end) or start > end:
        return False, "档期的开始日要不晚于结束日。"
    if supp and supp < end:
        return False, "补戏截止日不能早于档期结束日。"
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO seasons(name, start_date, end_date, supp_end, npcs) VALUES(?,?,?,?,?)",
                         (name, start, end, supp, npcs.strip()))
    except Exception:  # noqa: BLE001
        return False, f"已经有叫「{name}」的季度了。"
    return True, f"已预订「{name}」：{start} ~ {end}" + (f"，补戏到 {supp}" if supp else "")


def delete_booking(season_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM seasons WHERE id=? AND status='booked'", (season_id,)).rowcount > 0


def update_schedule(start: str, end: str, supp: str = "") -> tuple[bool, str]:
    s = running()
    if not s:
        return False, "现在没有进行中的季度。"
    if start > end or (supp and supp < end):
        return False, "日期不合理：开始日 ≤ 结束日 ≤ 补戏截止日。"
    with get_conn() as conn:
        conn.execute("UPDATE seasons SET start_date=?, end_date=?, supp_end=? WHERE id=?", (start, end, supp, s["id"]))
    return True, f"「{s['name']}」档期改为 {start} ~ {end}" + (f"，补戏到 {supp}" if supp else "")


def pick_booking(name: str = "", now: str | None = None):
    t = now or today()
    rows = booked()
    if name:
        return next((r for r in rows if r["name"] == name), None)
    inside = [r for r in rows if r["start_date"] <= t <= last_end(r)]
    if inside:
        return inside[0]
    upcoming = [r for r in rows if r["start_date"] > t]
    return upcoming[0] if upcoming else None


def role_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]


def start(name: str = "", clear: bool = False, now: str | None = None) -> tuple[bool, str]:
    if running():
        return False, f"已经有进行中的季度「{running()['name']}」，要先「结束季度」。"
    s = pick_booking(name, now)
    if not s:
        return False, (f"没有叫「{name}」的预订。" if name else "没有可以开始的预订：先在设置平台「季度」页预订，或发「预订季度 名字 MMDD-MMDD」。")
    if role_count() and not clear:
        return False, "NEEDS_WIPE"
    if role_count() and clear:
        wipe()
    t = now or today()
    with get_conn() as conn:
        conn.execute("UPDATE seasons SET status='running', started_ts=?, ended_ts=NULL WHERE id=?", (int(time.time()), s["id"]))
    set_config("season_wiped", "1")
    set_config("season_closed_notice", "")
    set_config("game_day", "")
    set_config("day_epoch", "0")
    if t >= s["start_date"]:
        enter_d0()
        return True, f"✅ 「{s['name']}」开始了，档期已经开始，直接进入 D0（自动天数、心动信已打开）。"
    return True, f"✅ 「{s['name']}」开始了，现在是筹备期；{s['start_date']} 自动进入 D0。\n玩家和 NPC 可以先「创建新角色 名字」。"


def enter_d0() -> None:
    set_config("game_day", "0")
    set_config("day_epoch", str(int(get_config("day_epoch", "0")) + 1))
    set_config("auto_day", "1")
    set_config("auto_day_last", today())
    set_config("lovemail_enabled", "1")


def end() -> tuple[bool, str]:
    s = running()
    if not s:
        return False, "现在没有进行中的季度。"
    with get_conn() as conn:
        conn.execute("UPDATE seasons SET status='ended', ended_ts=? WHERE id=?", (int(time.time()), s["id"]))
    set_config("auto_day", "0")
    set_config("season_wiped", "0")
    return True, f"⏹️ 「{s['name']}」已结束。核对完存档后发「收尾」清空本季的角色和互动记录（先预览，再「收尾 确认」）。"


def wipe_preview() -> dict[str, int]:
    names = {"roles": "角色", "messages": "短信和礼物", "lovemails": "心动信", "relations": "关系线"}
    with get_conn() as conn:
        return {label: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t, label in names.items()}


def wipe() -> None:
    """清空本季数据：角色（连带群位、属性、出场、认群码）、短信、拉黑、心动信、关系线。管理员、设置、季度记录都保留。"""
    with get_conn() as conn:
        for t in ("votes", "replies", "posts", "wishes", "join_requests", "room_members", "rooms", "lovemails", "rel_details", "relations", "messages", "blocks", "entrance_images", "entrance", "bind_codes", "role_attrs", "role_groups", "roles"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("UPDATE pool_groups SET state='free', room_id=NULL")
    set_config("season_wiped", "1")
    set_config("game_day", "")
    set_config("entrance_active", "0")


def set_day(n: int) -> None:
    set_config("game_day", str(n))
    set_config("day_epoch", str(int(get_config("day_epoch", "0")) + 1))   # 换新的计数键，当天的投稿次数等于清零


# ---------- 定时 ----------

async def tick(send, announce, now: time.struct_time | None = None) -> str:
    """每 30 秒一次：到开始日进 D0；23:59 自动加一天；档期结束自动关闭。announce 是 (bot_id, group_openid) 或 None。返回做了什么。"""
    s = running()
    if not s:
        return "idle"
    now = now or time.localtime()
    t = today(now)

    async def say(text):
        if announce:
            try:
                await send(*announce, text)
            except Exception:  # noqa: BLE001
                pass

    if current_day() is None and t >= s["start_date"] and t <= last_end(s):
        enter_d0()
        await say(f"📜 「{s['name']}」档期开始，现在是 D0。\n⚙️ 已自动打开：自动天数（每天 23:59 加一天）、心动信。")
        return "d0"
    if t > last_end(s) and get_config("season_closed_notice") != str(s["id"]):
        set_config("season_closed_notice", str(s["id"]))
        set_config("auto_day", "0")
        set_config("lovemail_enabled", "0")
        await say(f"📅 「{s['name']}」档期（含补戏期）已结束，已自动关闭自动天数和心动信（天数停在 {day_label()}）。\n接下来管理员发「结束季度」→「收尾」。")
        return "closed"
    if now.tm_hour * 60 + now.tm_min == 1439 and get_config("auto_day") == "1" and get_config("auto_day_last") != t and current_day() is not None:
        set_config("auto_day_last", t)
        prev = current_day()
        set_day(prev + 1)
        await say(f"📜 自动天数推进：D{prev} → D{prev + 1}（今天的计数已清空）")
        return "advanced"
    return "idle"
