"""小手机里「玩家能写字」的入口都要过的三道闸：违禁词、暂停通讯、档期。后续每个能发内容的功能都从这里调。"""
import os
import re
import time
from pathlib import Path

from . import season
from .db import DB_PATH, get_config

_ROOT = Path(__file__).resolve().parent.parent
_NOISE = re.compile(r"[\s　`~!@#$%^&*()_+\-=\[\]{};:'\",.<>/?\\|，。！？、；：“”‘’（）【】《》…—·~]+")
_cache: dict = {"path": None, "mtime": None, "words": [], "ascii": []}
PAUSE_MSG = "通讯暂时关闭了，稍后开放"


def blocklist_path() -> Path:
    """data/blocklist.txt 优先（管理员自己改的），没有就用随包带的起步词表。"""
    mine = DB_PATH.parent / "blocklist.txt"
    return mine if mine.exists() else _ROOT / "assets" / "blocklist.txt"


def _load() -> tuple[list[str], list[str]]:
    """含中文的词去掉空格符号后按子串匹配；纯英文/数字短词（sb、av、np）整词匹配，否则 have、happy 会被误伤。按修改时间热加载。"""
    path = blocklist_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return [], []
    if _cache["path"] != str(path) or _cache["mtime"] != mtime:
        words, ascii_words = [], []
        for ln in path.read_text(encoding="utf-8").splitlines():
            w = ln.strip().lower()
            if not w or w.startswith("#"):
                continue
            if re.fullmatch(r"[a-z0-9]+", w):
                ascii_words.append(w)
            else:
                w = _NOISE.sub("", w)
                if w:
                    words.append(w)
        _cache.update(path=str(path), mtime=mtime, words=words, ascii=ascii_words)
    return _cache["words"], _cache["ascii"]


def blocked_hit(role: str, field: str, *texts: str) -> str | None:
    """命中违禁词返回那个词并记一笔日志；干净返回 None。"""
    raw = "\n".join(t for t in texts if t)
    low = raw.lower()
    flat = _NOISE.sub("", low)
    words, ascii_words = _load()
    hit = next((w for w in words if w in flat), None) or next(
        (w for w in ascii_words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low)), None)
    if hit:
        try:
            log = DB_PATH.parent / "moderation.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\trole={role}\t{field}\t命中「{hit}」\t{raw}\n")
        except OSError:
            pass
    return hit


def comm_paused() -> bool:
    return get_config("phone_paused", "0") == "1"


def check_send(role: str, field: str, *texts: str) -> str:
    """能发返回空串，不能发返回要告诉玩家的原因。顺序：暂停通讯 → 档期 → 违禁词。"""
    if comm_paused():
        return PAUSE_MSG
    if season.phase() in ("none", "ended"):
        return "现在没有进行中的季度，暂时不能发送"
    if blocked_hit(role, field, *texts):
        return "内容里有不能发送的词，请改一改再发"
    return ""
