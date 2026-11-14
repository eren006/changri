"""把 QQ 导出的聊天记录（txt）导进 log，和机器人记下来的骰点按时间合并（不依赖 QQ / NoneBot）。

认两种写法：
  2026-11-14 21:03:11 小明(123456789)      ← 手机 / 电脑 QQ 导出
  小明(2026-11-14 21:03:11)               ← 本系统和 SealDice 的 log 导出
"""
from __future__ import annotations

import re
import time

from . import trpglog

MAX_BYTES = 2 * 1024 * 1024
_DT = r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})"
_A = re.compile(r"^" + _DT + r"\s+(.+?)\s*$")
_B = re.compile(r"^(.+?)\s*[(（]" + _DT + r"[)）]\s*$")
_TAIL_ID = re.compile(r"\s*[(（<][^()（）<>]*[)）>]\s*$")      # 昵称(QQ号) / 昵称<邮箱> 的尾巴
DEDUP_SECONDS = 3


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _ts(g) -> int | None:
    try:
        return int(time.mktime((int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), 0, 0, -1)))
    except (ValueError, OverflowError):
        return None


def parse(text: str) -> list[tuple[int, str, str]]:
    """→ [(时间戳, 发言人, 内容)]，按出现顺序。第一个表头之前的行（标题、日期栏）都忽略。"""
    out: list[tuple[int, str, str]] = []
    cur = None
    for line in text.replace("\r\n", "\n").split("\n"):
        h = None
        m = _A.match(line.strip())
        if m:
            h = (_ts(m.groups()[:6]), m.group(7))
        else:
            m = _B.match(line.strip())
            if m:
                h = (_ts(m.groups()[1:7]), m.group(1))
        if h and h[0]:
            if cur:
                out.append(cur)
            name = _TAIL_ID.sub("", h[1]).strip() or h[1].strip()
            cur = [h[0], name[:20], []]
        elif cur is not None:
            cur[2].append(line)
    if cur:
        out.append(cur)
    return [(ts, name, "\n".join(body).strip()) for ts, name, body in out if "\n".join(body).strip()]


def import_into(log_id: int, entries: list[tuple[int, str, str]], skip_names: set[str]) -> dict:
    """合并进 log：机器人昵称的、括号等规则跳过的、和已有内容重复的（同一句话时间差 3 秒内）都不要。"""
    existing: dict[str, list[int]] = {}
    for l in trpglog.lines(log_id):
        existing.setdefault(l["text"].strip(), []).append(l["ts"])
    rules = trpglog.rules()
    added = skipped = dup = 0
    with trpglog.get_conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM trpg_log_lines WHERE log_id=?", (log_id,)).fetchone()["n"]
        for ts, name, text in entries:
            if name in skip_names or trpglog.should_skip(text, False, rules):
                skipped += 1
                continue
            if any(abs(ts - t) <= DEDUP_SECONDS for t in existing.get(text.strip(), [])):
                dup += 1
                continue
            if n >= trpglog.MAX_LINES:
                skipped += 1
                continue
            c.execute("INSERT INTO trpg_log_lines(log_id,ts,speaker_key,speaker_name,kind,text,ref) VALUES (?,?,?,?, 'say', ?, NULL)",
                      (log_id, ts, "imp:" + name, name, text[:2000]))
            existing.setdefault(text.strip(), []).append(ts)
            added += 1
            n += 1
    return {"added": added, "skipped": skipped, "dup": dup, "total": len(entries)}
