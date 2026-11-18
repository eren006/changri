"""每日一句（小手机功能，默认关，开关在后台「小手机」页）：发现页顶上一张卡片，一言、今日诗词各一句，点一下换一句。
照老系统 rp_archive 的做法：每天第一次有人打开发现页时，机器人去这两个网站各拉一条，存起来当天都用这条；拉不到就用最近一次的。
- 这是长日bot 主动连第三方网站的地方（v1.hitokoto.cn、v2.jinrishici.com），只发一个不带任何玩家信息的 GET，所以默认关，README「安全提醒」里写着。
- 一言只要文学 / 诗词 / 哲学三类，避开动漫游戏类的句子。
- 存在 config 的 dailyquote_cache 里（{来源: {day, text, source}}），每个机器人各一份；不加表。"""
import json
import time
import urllib.request

_KEY = "dailyquote_cache"
_UA = "changri-bot"


def _hitokoto(j: dict) -> tuple[str, str]:
    who, frm = (j.get("from_who") or "").strip(), (j.get("from") or "").strip()
    return j.get("hitokoto") or "", ("——" + who + (f"《{frm}》" if frm else "")) if (who or frm) else ""


def _shici(j: dict) -> tuple[str, str]:
    d = j.get("data") or {}
    o = d.get("origin") or {}
    return d.get("content") or "", "——" + (o.get("author") or "") + (f"《{o['title']}》" if o.get("title") else "")


SOURCES = {
    "hitokoto": ("https://v1.hitokoto.cn/?c=d&c=i&c=k&max_length=40&encode=json", _hitokoto),
    "shici": ("https://v2.jinrishici.com/one.json", _shici),
}


def enabled() -> bool:
    from . import extensions
    return extensions.is_enabled("dailyquote")


def fetch(kind: str, timeout: float = 3) -> tuple[str, str] | None:
    """去网站拉一条，拉不到返回 None。"""
    url, parse = SOURCES[kind]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=timeout) as r:
            text, source = parse(json.loads(r.read().decode("utf-8")))
    except Exception:  # noqa: BLE001 网络、格式各种错，一律当没拉到
        return None
    text = (text or "").strip()
    return (text[:80], (source or "").strip()[:40]) if text else None


def _cache() -> dict:
    from .db import get_config
    try:
        d = json.loads(get_config(_KEY, "") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def today(now: float | None = None) -> list[dict]:
    """今天的几句：[{kind, text, source}]。今天还没拉过的去拉一次；拉不到就用最近一次存的，一次都没有就不给这一条。"""
    from .db import set_config
    day = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    cache, changed, out = _cache(), False, []
    for kind in SOURCES:
        row = cache.get(kind) if isinstance(cache.get(kind), dict) else {}
        if row.get("day") != day and row.get("tried") != day:
            got = fetch(kind)
            if got:
                row = {"day": day, "text": got[0], "source": got[1]}
            else:
                row = {**row, "tried": day}            # 今天拉过没拉到：记一下，当天不再反复去试，先用旧的
            cache[kind], changed = row, True
        if row and row.get("text"):
            out.append({"kind": kind, "text": row["text"], "source": row.get("source", "")})
    if changed:
        set_config(_KEY, json.dumps(cache, ensure_ascii=False))
    return out
