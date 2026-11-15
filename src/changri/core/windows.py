"""功能开放时段：每个功能可以只在几点到几点开放（按服务器现在的钟点，和游戏天数无关）。
设置里写成「10-23」「20:00-02:00」（跨午夜也行），留空 = 全天开放。官方发起的（官约、官电）不受限。"""
import re
import time

from .db import get_config

# 功能名 → (设置键, 对玩家显示的名字)
FEATURES = {
    "sms": ("window_sms", "短信与礼物"),
    "date": ("window_date", "私约/电话/微信"),
    "lovemail": ("window_lovemail", "心动信"),
    "wish": ("window_wish", "心愿"),
    "forum": ("window_forum", "论坛"),
}
_RANGE = re.compile(r"^\s*(\d{1,2})(?::?(\d{2}))?\s*[-~—到]\s*(\d{1,2})(?::?(\d{2}))?\s*$")


def parse(text: str) -> tuple[int, int] | None:
    """「10-23」→ (600, 1380)，单位是一天里的第几分钟；空或写错返回 None（当作全天）。"""
    m = _RANGE.match(text or "")
    if not m:
        return None
    a = int(m.group(1)) * 60 + int(m.group(2) or 0)
    b = int(m.group(3)) * 60 + int(m.group(4) or 0)
    if a > 24 * 60 or b > 24 * 60 or a == b:
        return None
    return a, b


def _fmt(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def is_open(feature: str, now_minute: int | None = None) -> tuple[bool, str]:
    key, name = FEATURES[feature]
    rng = parse(get_config(key, ""))
    if not rng:
        return True, ""
    if now_minute is None:
        t = time.localtime()
        now_minute = t.tm_hour * 60 + t.tm_min
    a, b = rng
    ok = a <= now_minute < b if a < b else (now_minute >= a or now_minute < b)
    return ok, "" if ok else f"⏰ {name}现在没有开放，开放时间是 {_fmt(a)}–{_fmt(b)}。"


def check(feature: str) -> str:
    """能用返回空串，不能用返回要告诉玩家的话。"""
    return is_open(feature)[1]
