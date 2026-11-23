"""公知、关系线的截止时间：后台可选填，不填就没有截止，和以前完全一样。按运行机器人的电脑的本地时间算。

· 公知截止后：自动对所有人展示（不用管理员再去开「展示公知」），公告群播一句（tick），玩家不能再交、也不能再改；
  管理员在后台照样能改文字。「展示公知」开关留着当后门：可以提前手动打开；要延后就改截止时间。
· 关系线截止后：全部锁定、只能看（relations.closed_note）（作者定，2026-11-22）。
存成 "YYYY-MM-DD HH:MM"，空字符串 = 没有截止。
"""
from __future__ import annotations

import time
from datetime import datetime

from .db import get_config, set_config

KEYS = {"gongzhi": "gongzhi_deadline", "rel": "rel_deadline"}
FMT = "%Y-%m-%d %H:%M"


def get(which: str) -> str:
    return (get_config(KEYS[which], "") or "").strip()


def parse(raw: str) -> str | None:
    """后台填的值规整成 "YYYY-MM-DD HH:MM"。空 → ""（清掉截止）；看不懂 → None。网页的 datetime-local 带 T，也收。"""
    raw = (raw or "").strip().replace("T", " ")
    if not raw:
        return ""
    for f in (FMT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, f).strftime(FMT)
        except ValueError:
            continue
    return None


def set(which: str, raw: str) -> bool:
    """改截止时间。顺手把「重新开放」清掉：新的截止时间重新算。"""
    v = parse(raw)
    if v is None:
        return False
    set_config(KEYS[which], v)
    set_config(REOPEN[which], "0")
    return True


# 管理员的手动后门：截止以后按「重新开放」，玩家又能交/写；再按一次「重新关闭」又锁上。同一个按钮来回切。
# 只管能不能写，不管公知展不展示（截止了照样对所有人展示）。
REOPEN = {"gongzhi": "gongzhi_reopened", "rel": "rel_reopened"}


def reopened(which: str) -> bool:
    return get_config(REOPEN[which], "0") == "1"


def set_reopened(which: str, on: bool) -> None:
    set_config(REOPEN[which], "1" if on else "0")


def locked(which: str, now: float | None = None) -> bool:
    """过了截止时间、管理员也没有重新开放：玩家不能写。"""
    return passed(which, now) and not reopened(which)


def passed(which: str, now: float | None = None) -> bool:
    d = get(which)
    if not d:
        return False
    try:
        at = datetime.strptime(d, FMT).timestamp()
    except ValueError:
        return False
    return (time.time() if now is None else now) >= at


GONGZHI_NOTICE = "📜 公知已截止，可以互相查看了。发「查看公知」看所有人的。"


async def tick(send, announce, now: float | None = None) -> bool:
    """定时检查（跟季度的定时任务一起，30 秒一次）：公知过了截止时间，往公告群播一句。
    每个截止时间只播一次：记下播过的是哪个时间，管理员改了截止时间会再播。没设公告群就先不播、也不记，设了以后补播。
    返回这次播了没有。"""
    at = get("gongzhi")
    if not at or not passed("gongzhi", now) or get_config("gongzhi_deadline_announced", "") == at or not announce:
        return False
    set_config("gongzhi_deadline_announced", at)        # 先记再发：发失败也不会每 30 秒刷一遍
    await send(*announce, GONGZHI_NOTICE)
    return True


def label(which: str) -> str:
    """给玩家看的短写法，比如「11-25 20:00」。没有截止返回空。"""
    return get(which)[5:]
