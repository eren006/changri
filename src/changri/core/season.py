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
    """NPC 是角色上的标记（设为NPC / 创建NPC），不再来自季度预订名单。"""
    from . import npc
    return npc.names()


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


def find_conflict(start: str, end: str, supp: str = "", exclude_id: int | None = None):
    """档期（含补戏期）和别的预订/进行中的季度撞车就返回那个季度，没撞返回 None。已结束的历史季度不算。"""
    last = supp or end
    with get_conn() as conn:
        for r in conn.execute("SELECT * FROM seasons WHERE status IN ('booked','running') ORDER BY start_date"):
            if exclude_id is not None and r["id"] == exclude_id:
                continue
            if start <= last_end(r) and r["start_date"] <= last:
                return r
    return None


def month_grid(ym: str = "", now: str | None = None) -> dict:
    """月历：周一开头的整周格子，每格列出那天占着档期的季度。ym 形如 2026-11，不合法就用本月。
    items 里 kind=main 是正式档期、supp 是补戏期；first=这一格是它在本月的起点（只在起点写名字）。"""
    import calendar as _cal
    t = now or today()
    try:
        y, m = int(ym[:4]), int(ym[5:7])
        if not 1 <= m <= 12:
            raise ValueError
    except (ValueError, IndexError):
        y, m = int(t[:4]), int(t[5:7])
    seasons = all_seasons()
    weeks = []
    for week in _cal.Calendar(0).monthdatescalendar(y, m):
        row = []
        for d in week:
            ds = d.isoformat()
            items = []
            for sn in seasons:
                if not sn["start_date"] <= ds <= last_end(sn):
                    continue
                kind = "main" if ds <= sn["end_date"] else "supp"
                items.append({"id": sn["id"], "name": sn["name"], "status": sn["status"], "kind": kind,
                              "first": ds == sn["start_date"] or (d.weekday() == 0) or d.day == 1 or (kind == "supp" and ds == _next_day(sn["end_date"]))})
            row.append({"date": ds, "day": d.day, "in_month": d.month == m, "today": ds == t, "items": items})
        weeks.append(row)
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return {"year": y, "month": m, "label": f"{y} 年 {m} 月", "weeks": weeks, "prev": f"{py:04d}-{pm:02d}", "next": f"{ny:04d}-{nm:02d}",
            "this": f"{t[:7]}"}


def _next_day(ds: str) -> str:
    import datetime
    return (datetime.date.fromisoformat(ds) + datetime.timedelta(days=1)).isoformat()


def upcoming_line(now: str | None = None) -> str:
    """dashboard 日历上方一句话：现在在哪一季 / 下一季几天后开始。"""
    t = now or today()
    s = running()
    if s:
        return f"「{s['name']}」进行中（{day_label()}），档期到 {last_end(s)}"
    b = next((r for r in booked() if last_end(r) >= t), None)
    if not b:
        return "还没有预订的季度"
    import datetime
    n = (datetime.date.fromisoformat(b["start_date"]) - datetime.date.fromisoformat(t)).days
    return f"下一季「{b['name']}」" + (f"{n} 天后开始（{b['start_date']}）" if n > 0 else f"档期已到（{b['start_date']}），还没点开始")


# ---------- 预订 / 开始 / 结束 ----------

def book(name: str, start: str, end: str, supp: str = "", npcs: str = "") -> tuple[bool, str]:
    name = name.strip()
    if not name or len(name) > 30:
        return False, "季度名要 1–30 个字。"
    if not (start and end) or start > end:
        return False, "档期的开始日要不晚于结束日。"
    if supp and supp < end:
        return False, "补戏截止日不能早于档期结束日。"
    clash = find_conflict(start, end, supp)
    if clash:
        return False, f"档期和「{clash['name']}」（{clash['start_date']} ~ {last_end(clash)}）撞了，换个时间。"
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


def update_booking(season_id: int, name: str, start: str, end: str, supp: str = "") -> tuple[bool, str]:
    """修改还没开始的预订（名字、档期、补戏期）。"""
    name = name.strip()
    if not name or len(name) > 30:
        return False, "季度名要 1–30 个字。"
    if not (start and end) or start > end:
        return False, "档期的开始日要不晚于结束日。"
    if supp and supp < end:
        return False, "补戏截止日不能早于档期结束日。"
    clash = find_conflict(start, end, supp, exclude_id=season_id)
    if clash:
        return False, f"档期和「{clash['name']}」（{clash['start_date']} ~ {last_end(clash)}）撞了，换个时间。"
    try:
        with get_conn() as conn:
            n = conn.execute("UPDATE seasons SET name=?, start_date=?, end_date=?, supp_end=? WHERE id=? AND status='booked'",
                             (name, start, end, supp, season_id)).rowcount
    except Exception:  # noqa: BLE001
        return False, f"已经有叫「{name}」的季度了。"
    return (True, f"已修改「{name}」") if n else (False, "只能修改还没开始的预订。")


def get_season(season_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM seasons WHERE id=?", (season_id,)).fetchone()


def latest_ended():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM seasons WHERE status='ended' ORDER BY COALESCE(ended_ts, 0) DESC, id DESC LIMIT 1").fetchone()


def rename_season(season_id: int, name: str) -> tuple[bool, str]:
    """给历史季度改名（进行中、预订的季度不在这里改）。"""
    name = name.strip()
    s = get_season(season_id)
    if not s or s["status"] != "ended":
        return False, "只能给已结束的季度改名。"
    if not name or len(name) > 30:
        return False, "季度名要 1–30 个字。"
    try:
        with get_conn() as conn:
            conn.execute("UPDATE seasons SET name=? WHERE id=?", (name, season_id))
    except Exception:  # noqa: BLE001
        return False, f"已经有叫「{name}」的季度了。"
    return True, f"已改名为「{name}」"


def edit_history(season_id: int, name: str, start: str, end: str, supp: str = "") -> tuple[bool, str]:
    """编辑历史季度：名字和档期（只是记录，改了不影响别的季度，所以不查撞车）。"""
    s = get_season(season_id)
    if not s or s["status"] != "ended":
        return False, "只能编辑已结束的季度。"
    if not (start and end) or start > end:
        return False, "档期的开始日要不晚于结束日。"
    if supp and supp < end:
        return False, "补戏截止日不能早于档期结束日。"
    ok, msg = rename_season(season_id, name) if name.strip() != s["name"] else (True, "")
    if not ok:
        return ok, msg
    with get_conn() as conn:
        conn.execute("UPDATE seasons SET start_date=?, end_date=?, supp_end=? WHERE id=?", (start, end, supp, season_id))
    return True, f"已保存「{name.strip()}」"


def delete_history(season_id: int) -> tuple[bool, str]:
    """删除一个已结束的历史季度（连同它的档案）。刚结束、还没收尾的那一季不能删：收尾时还要给它存档案。"""
    s = get_season(season_id)
    if not s or s["status"] != "ended":
        return False, "只能删除已结束的季度。"
    last = latest_ended()
    if get_config("season_wiped", "1") == "0" and last and last["id"] == season_id:
        return False, "这一季刚结束、还没收尾，先收尾再删。"
    with get_conn() as conn:
        conn.execute("DELETE FROM season_archives WHERE season_id=?", (season_id,))
        conn.execute("DELETE FROM seasons WHERE id=?", (season_id,))
    return True, f"已删除历史季度「{s['name']}」和它的档案"


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
        enter_d0(t)
        return True, f"✅ 「{s['name']}」开始了，档期已经开始，直接进入 D0（自动天数、心动信已打开）。"
    return True, f"✅ 「{s['name']}」开始了，现在是筹备期；{s['start_date']} 自动进入 D0。\n玩家和 NPC 可以先「创建新角色 名字」。筹备期的互动（短信、礼物、心动信、关系线等）不会留下记录，进入 D0 时会清零。"


def clear_prep_records() -> dict[str, int]:
    """筹备期的互动不记录：从筹备期进入 D0 时，把这段时间发的短信/礼物、心动信、关系线、心愿和已经结束的房间复盘清掉，
    正式季第一天从零开始。进行中的房间不动（人正在里面聊）。角色、属性、群位、论坛帖子不动。返回各清了多少条。"""
    out = {}
    with get_conn() as conn:
        out["短信和礼物"] = conn.execute("DELETE FROM messages").rowcount
        out["心动信"] = conn.execute("DELETE FROM lovemails").rowcount
        conn.execute("DELETE FROM rel_details")
        out["关系线"] = conn.execute("DELETE FROM relations").rowcount
        out["心愿"] = conn.execute("DELETE FROM wishes").rowcount
        out["已结束的房间"] = conn.execute("DELETE FROM rooms WHERE status!='open'").rowcount      # 连带成员、发言、加入申请（外键级联）
    from . import milestone
    milestone.reset()                                                         # 「第 N 次记录」也从正式季第一天重新数
    return out


def enter_d0(day: str | None = None, skip_tonight: bool = True) -> None:
    """进入 D0。day = 进入的那天（不传就是真实的今天）。
    skip_tonight：当晚 23:59 不加一天（管理员手动开始时，可能已经很晚，D0 至少留到第二天）；
    到了开始日自动进入（凌晨）时传 False：开始日当天就是 D0，当晚 23:59 进 D1。"""
    if running() and current_day() is None:
        clear_prep_records()
    set_config("game_day", "0")
    set_config("day_epoch", str(int(get_config("day_epoch", "0")) + 1))
    set_config("auto_day", "1")
    set_config("auto_day_last", (day or today()) if skip_tonight else "")
    set_config("lovemail_enabled", "1")


def end() -> tuple[bool, str]:
    s = running()
    if not s:
        return False, "现在没有进行中的季度。"
    with get_conn() as conn:
        conn.execute("UPDATE seasons SET status='ended', ended_ts=? WHERE id=?", (int(time.time()), s["id"]))
    set_config("auto_day", "0")
    set_config("season_wiped", "0")
    _archive(s["id"])
    return True, f"⏹️ 「{s['name']}」已结束。核对完存档后发「收尾」清空本季的角色和互动记录（先预览，再「收尾 确认」）。"


async def end_and_report(send, send_image=None) -> tuple[bool, str]:
    """结束季度；「基础」里开了季末报告就顺带给每个玩家发一份（给了 send_image 就先发一句文字、再单独发结算海报）。"""
    ok, msg = end()
    if ok:
        from . import seasonreport
        if seasonreport.enabled():
            sent, bad = await seasonreport.send_all(send, send_image)
            msg += f"\n📖 季末报告：已发给 {sent} 位玩家" + (f"，{bad} 位没发出去（个人群连不上）" if bad else "") + "。"
        msg += "\n" + await _season_view_to_staff(send)
    return ok, msg


async def _season_view_to_staff(send) -> str:
    """结束季度时自动打开复盘网页，把链接发到后台群（作者定的：不发公告群；没设后台群就不发）。返回给管理员看的一句。"""
    from . import announce, seasonview
    try:
        last = seasonview.latest_ended()
        if not last:
            return ""
        seasonview.publish(last["id"])
        if not seasonview.url(last["id"]):
            return "📖 复盘网页没生成：" + (seasonview.why_no_url() or "看后台「季度与出场 → 往季」。")
        staff = announce.get_backstage()
        if not staff:
            return "📖 复盘网页已打开，但还没设后台群，链接没发出去：在后台「季度与出场 → 往季」里能看到。"
        await send(staff[0], staff[1], seasonview.message(last["id"]))
        return "📖 复盘网页的链接已发到后台群。"
    except Exception:  # noqa: BLE001 发不出去不能拦住结束季度
        import logging
        logging.getLogger(__name__).exception("复盘网页链接没发出去")
        return "📖 复盘网页的链接没发出去，可以在后台「季度与出场 → 往季」里点「发到后台群」。"


def wipe_preview() -> dict[str, int]:
    names = {"roles": "角色", "messages": "短信和礼物", "lovemails": "心动信", "relations": "关系线"}
    with get_conn() as conn:
        return {label: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t, label in names.items()}


def _archive(season_id: int) -> None:
    from . import seasonarchive
    try:
        seasonarchive.snapshot(season_id)
    except Exception:  # noqa: BLE001 存档失败不能拦住结束/收尾
        import logging
        logging.getLogger(__name__).exception("季度档案没存下来")


def wipe() -> None:
    """清空本季数据：角色（连带群位、属性、出场、认群码）、短信、拉黑、心动信、关系线。管理员、设置、季度记录都保留。
    清空之前先给刚结束的那一季存一份档案（房间复盘、统计），之后还能在「季度」页回看。"""
    last = latest_ended()
    if last and get_config("season_wiped", "1") == "0":
        _archive(last["id"])
    with get_conn() as conn:
        for t in ("tap_outbox", "item_taps", "letter_effects", "letters", "votes", "replies", "posts", "wishes", "join_requests", "room_lines", "room_members", "rooms", "lovemails", "rel_details", "relations", "messages", "blocks", "entrance_images", "entrance", "bind_codes", "role_attrs", "role_groups", "roles"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='rooms'")        # 新季度的 R 号从 1 重新开始
        conn.execute("UPDATE pool_groups SET state='free', room_id=NULL")
    from . import milestone
    milestone.reset()                                                         # 「第 N 次记录」的计数跟着季度清零
    set_config("season_wiped", "1")
    set_config("game_day", "")
    set_config("entrance_active", "0")


def set_day(n: int) -> None:
    if running() and current_day() is None:         # 手动从筹备期设成某一天，也算正式开始
        clear_prep_records()
    set_config("game_day", str(n))
    set_config("day_epoch", str(int(get_config("day_epoch", "0")) + 1))   # 换新的计数键，当天的投稿次数等于清零


def day_summary_line() -> str:
    """全员今天（当前 day_key）一共发生了多少：天数推进时发到公告群。撤回的短信、取消的房间不算；
    管理员在上帝模式里「重置今日次数」只影响个人上限，这里照实算。"""
    key = day_key()
    with get_conn() as c:
        room = {r[0]: r[1] for r in c.execute(
            "SELECT kind, COUNT(*) FROM rooms WHERE dkey=? AND status!='cancelled' GROUP BY kind", (key,))}
        msg = {r[0]: r[1] for r in c.execute(
            "SELECT kind, COUNT(*) FROM messages WHERE day=? AND error!='已撤回' GROUP BY kind", (key,))}
        wish = c.execute("SELECT COUNT(*) FROM wishes WHERE dkey=?", (key,)).fetchone()[0]
    parts = [f"{k} {room.pop(k, 0)} 场" for k in ("约会", "电话", "官约")]
    parts += [f"{k} {n} 场" for k, n in room.items() if n]             # 微信、踩点、前置电话……有才显示
    parts += [f"短信 {msg.get('sms', 0)} 条", f"礼物 {msg.get('gift', 0)} 份"]
    if wish:
        parts.append(f"心愿 {wish} 个")
    return "🌐 全员今天：" + "｜".join(parts)


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
        enter_d0(t, skip_tonight=False)            # 到开始日自动进 D0：当天就是 D0，当晚 23:59 进 D1
        from . import storycal
        sl = storycal.stage_line(0)
        await say(f"📜 「{s['name']}」档期开始，现在是 D0。" + (f"\n🕰️ {sl}" if sl else "") + "\n⚙️ 已自动打开：自动天数（每天 23:59 加一天）、心动信。\n🧹 筹备期的互动记录已清零，从今天重新算。")
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
        try:
            counts = "\n" + day_summary_line()          # 换天之前算，换了就是新一天的键了
        except Exception:  # noqa: BLE001
            counts = ""
        set_day(prev + 1)
        from . import storycal
        sl = storycal.stage_line(prev + 1)
        counts += f"\n🕰️ {sl}" if sl else ""
        await say(f"📜 自动天数推进：D{prev} → D{prev + 1}（今天的计数已清空）{counts}")
        return "advanced"
    return "idle"
