"""故事历（个性化模块，默认关）：把一个 D（现实里的一天，00:00–24:00）平分成若干「故事日」，只用来显示和写时间。

- 年号写一次，所有 D 共用（比如「康熙十四年」）。
- 每个 D 填故事日期的起止（比如 3月1日 到 3月31日）；这一天有多少个故事日就把现实的一天平分成几份，
  所以每个 D 的流速可以不一样（31 天 ≈ 46 分钟一天，30 天 = 48 分钟一天）。
- 不改心动信、每日上限、自动天数这些：它们照旧按 D 和现实时间走。
- 开房间的时间写「3月5日」（只能写一天），换成当天的现实钟点；开着且这个 D 填了日期时不再认「1400-1500」。
- 月份长度按公历（2 月 28 天）；跨月的范围（3月20日-4月10日）也能算。
"""
import json
import re

from .db import get_config, set_config

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MD = re.compile(r"^\s*(\d{1,2})\s*(?:月|[./])\s*(\d{1,2})\s*(?:日|号)?\s*$")
_RANGE_SPLIT = re.compile(r"\s*(?:[-~—到至])\s*")
MAX_STAGES = 12          # 后台最多填到 D11


def enabled() -> bool:
    from . import extensions
    return extensions.is_enabled("storycal")


# ---------- 存取 ----------

def era() -> str:
    return get_config("story_era", "").strip()


def stages() -> dict[int, tuple[tuple[int, int], tuple[int, int]]]:
    """{D 几: ((起月, 起日), (止月, 止日))}"""
    try:
        raw = json.loads(get_config("story_stages", "") or "{}")
    except ValueError:
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[int(k)] = ((int(v[0][0]), int(v[0][1])), (int(v[1][0]), int(v[1][1])))
        except (ValueError, TypeError, IndexError):
            continue
    return out


def save(era_text: str, rows: dict[int, str]) -> list[str]:
    """rows = {D 几: 「3月1日-3月31日」}；空的跳过。返回错误列表（空 = 全部存了）。"""
    errs, out, prev = [], {}, None
    for d, text in sorted(rows.items()):
        if not text.strip():
            continue
        rng = parse_span(text)
        if isinstance(rng, str):
            errs.append(f"D{d}：{rng}")
            continue
        if prev and rng[0] <= prev[1]:
            errs.append(f"D{d}：开头 {_fmt_md(rng[0])} 要在 D{prev[0]} 的结束日 {_fmt_md(prev[1])} 之后（日期不能重叠）。")
            continue
        out[str(d)] = [list(rng[0]), list(rng[1])]
        prev = (d, rng[1])
    if errs:
        return errs
    set_config("story_era", era_text.strip()[:20])
    set_config("story_stages", json.dumps(out, ensure_ascii=False))
    return []


# ---------- 日期 ----------

def parse_md(text: str) -> tuple[int, int] | None:
    m = _MD.match(text or "")
    if not m:
        return None
    mo, d = int(m[1]), int(m[2])
    return (mo, d) if 1 <= mo <= 12 and 1 <= d <= MONTH_DAYS[mo - 1] else None


def parse_span(text: str) -> tuple[tuple[int, int], tuple[int, int]] | str:
    """「3月1日-3月31日」「3月5日」「3月5日-7日」→ ((3,1),(3,31))；写错返回原因。"""
    parts = [p for p in _RANGE_SPLIT.split(text.strip()) if p]
    if not parts or len(parts) > 2:
        return "写成「3月1日-3月31日」。"
    a = parse_md(parts[0])
    if not a:
        return f"看不懂「{parts[0]}」，写成「3月1日」。"
    if len(parts) == 1:
        return a, a
    b = parse_md(parts[1])
    if not b and re.fullmatch(r"\d{1,2}\s*(日|号)?", parts[1]):        # 「3月5日-7日」：结束日省略月份
        b = parse_md(f"{a[0]}月{parts[1]}")
    if not b:
        return f"看不懂「{parts[1]}」，写成「3月31日」。"
    if b < a:
        return "结束日不能早于开始日。"
    return a, b


def _days(span) -> list[tuple[int, int]]:
    (m, d), end = span
    out = []
    while (m, d) <= end:
        out.append((m, d))
        d += 1
        if d > MONTH_DAYS[m - 1]:
            m, d = m + 1, 1
        if m > 12:
            break
    return out


def day_list(day: int) -> list[tuple[int, int]]:
    st = stages().get(day)
    return _days(st) if st else []


def day_minutes(day: int) -> float | None:
    n = len(day_list(day))
    return 1440 / n if n else None


def fmt_len(minutes: float) -> str:
    if minutes >= 60:
        h, m = divmod(round(minutes), 60)
        return f"{h} 小时" + (f" {m} 分钟" if m else "")
    return f"{minutes:.0f} 分钟" if abs(minutes - round(minutes)) < 0.05 else f"约 {minutes:.1f} 分钟"


def _fmt_md(md: tuple[int, int]) -> str:
    return f"{md[0]}月{md[1]}日"


def span_text(day: int) -> str:
    st = stages().get(day)
    return "" if not st else (_fmt_md(st[0]) if st[0] == st[1] else f"{_fmt_md(st[0])}-{_fmt_md(st[1])}")


# ---------- 现实时间 ↔ 故事日期 ----------

def story_date(day: int, minute: int) -> tuple[int, int] | None:
    """D{day} 的第 minute 分钟（0–1439）是故事里的几月几日。"""
    days = day_list(day)
    if not days:
        return None
    return days[min(len(days) - 1, minute * len(days) // 1440)]


def label_at(day: int, minute: int) -> str:
    md = story_date(day, minute)
    return "" if not md else f"{era()}{_fmt_md(md)}"


def now_label() -> str:
    """现在（真实钟点）的故事日期；没打开、没填这一天、没进行中返回空串。"""
    if not enabled():
        return ""
    import time
    from . import season
    d = season.current_day()
    if d is None:
        return ""
    t = time.localtime()
    return label_at(d, t.tm_hour * 60 + t.tm_min)


def stage_line(day: int) -> str:
    """「D1 = 康熙十四年6月1日-6月30日」；没填返回空串。"""
    if not enabled() or not day_list(day):
        return ""
    return f"D{day} = {era()}{span_text(day)}"


def hm(minute: int) -> str:
    minute = min(minute, 1439)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse_when(text: str, day: int | None = None) -> tuple[tuple[str, str] | None, str]:
    """把「3月5日」换成当前这个 D 里的现实钟点 ('HH:MM','HH:MM')（只认一天，写时间段会提示）。
    返回 (时间, 错误)：不是日期写法时 (None, "")，让调用的人按原来的格式报错；是日期但不在这个 D 里时 (None, 原因)。"""
    if not enabled():
        return None, ""
    from .textutil import norm_time
    span = parse_span(norm_time(text))         # 全角数字、「～」这类也认
    if isinstance(span, str):
        return None, ""
    if day is None:
        from . import season
        day = season.current_day()
    days = day_list(day) if day is not None else []
    if not days:
        return None, "这一天还没填故事日期，请管理员在「故事历」里填。"
    if span[0] not in days or span[1] not in days:
        return None, f"现在是 D{day}，故事日期只到 {span_text(day)}。"
    if span[0] != span[1]:
        return None, "约时间只能写一天，比如 " + _fmt_md(span[0]) + "（不能写时间段）。"
    n = len(days)
    i = days.index(span[0])
    a, b = i * 1440 // n, (i + 1) * 1440 // n
    return (hm(a), hm(b)), ""


def exclusive(day: int | None = None) -> bool:
    """故事历开着、并且这一个 D 填了故事日期：约时间只认故事日期（不能写 1400-1500）。这个 D 没填就还是写钟点，免得谁都约不了。"""
    if not enabled():
        return False
    if day is None:
        from . import season
        day = season.current_day()
    return day is not None and bool(day_list(day))


def format_error(day: int | None = None) -> str:
    """约时间写错时告诉玩家怎么写。"""
    if exclusive(day):
        if day is None:
            from . import season
            day = season.current_day()
        return f"时间要写故事里的某一天，比如 {_fmt_md(day_list(day)[0])}（D{day} 是 {span_text(day)}）。"
    return "时间格式不对，写成 1400-1500（跨午夜写 2300-0100，0 点以后算第二天）。"


def example_time(day: int | None = None) -> str:
    """用法提示里的时间例子：故事历开着并且这个 D 填了日期就给一个故事日期（3月5日），否则是 1400-1500。"""
    if exclusive(day):
        if day is None:
            from . import season
            day = season.current_day()
        return _fmt_md(day_list(day)[0])
    return "1400-1500"


def hm_minute(hm_text: str) -> int | None:
    """「14:00」→ 840；写错返回 None。"""
    m = re.match(r"^\s*(\d{1,2}):?(\d{2})\s*$", hm_text or "")
    return int(m[1]) * 60 + int(m[2]) if m else None


def label_for(day, minute: int | None) -> str:
    """D{day} 的第 minute 分钟是故事里的哪天（「康熙十四年3月5日」）；没开故事历、这个 D 没填日期、没有天数时返回空串。"""
    try:
        day = int(day)
    except (TypeError, ValueError):
        return ""
    if not enabled() or minute is None or not day_list(day):
        return ""
    return label_at(day, minute)


def label_for_ts(day, ts: float) -> str:
    """同上，用现实时间戳（按本机时区的钟点）。"""
    import time
    t = time.localtime(ts)
    return label_for(day, t.tm_hour * 60 + t.tm_min)


def label_for_slot(day, start_hm: str) -> str:
    return label_for(day, hm_minute(start_hm))
