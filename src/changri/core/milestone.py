"""「第 N 次记录」公告（照长日系统）：电话、约会、官约、心愿、短信、礼物、心动信、拉线各自计数，
每满「公告触发频率」次，往公告群发一句「【第 N 次××记录】」。默认每 2 次发一句，频率填 0 = 不发。计数跟着季度走，收尾时清零。
每一类还可以在「高级」里单独填频率（announce_frequency_<英文名>），留空就跟着总频率。

计数发生在各功能的规则代码里（那里发不了群消息），所以先把要发的话放进待发箱，由定时任务（plugins/social.py）发出去。"""
import json

from .db import get_config, get_int, set_config

LABELS = {"电话": ("☎️", "电话"), "约会": ("💫", "约会"), "官约": ("🏢", "官约"), "心愿": ("🌠", "心愿"),
          "短信": ("📮", "短信"), "礼物": ("🎁", "礼物赠送"), "心动信": ("💌", "心动信派送"), "拉线": ("🔗", "关系线")}
COUNTS, OUTBOX = "record_counts", "record_outbox"
# 每一类单独的频率存在 announce_frequency_<这里的英文名>，留空 = 跟总频率
KIND_KEYS = {"电话": "phone", "约会": "date", "官约": "official", "心愿": "wish",
             "短信": "sms", "礼物": "gift", "心动信": "lovemail", "拉线": "relation"}


def frequency(kind: str = "") -> int:
    """kind 这一类的公告频率：单独填过就用单独的（0 = 这一类不发），没填就用总频率。"""
    if kind in KIND_KEYS:
        own = get_config(f"announce_frequency_{KIND_KEYS[kind]}", "").strip()
        if own.lstrip("-").isdigit():
            return max(0, int(own))
    return max(0, get_int("announce_frequency", 2))


def _load(key: str, default):
    try:
        v = json.loads(get_config(key, "") or "null")
    except ValueError:
        v = None
    return v if isinstance(v, type(default)) else default


def counts() -> dict:
    return _load(COUNTS, {})


def bump(kind: str, n: int = 1) -> str:
    """kind 这类互动又发生了 n 次。正好跨过频率的整数倍就排一条公告（一次跨过好几个只报最后一个），返回排进去的那句话（没有返回空串）。
    频率是 0 时照样计数，只是不发：中途打开也能接着数。"""
    if kind not in LABELS or n <= 0:
        return ""
    c = counts()
    old = int(c.get(kind, 0))
    new = old + n
    c[kind] = new
    set_config(COUNTS, json.dumps(c, ensure_ascii=False))
    f = frequency(kind)
    if not f or new // f == old // f:
        return ""
    emoji, label = LABELS[kind]
    text = f"{emoji} 【第{new // f * f}次{label}记录】"
    set_config(OUTBOX, json.dumps(_load(OUTBOX, []) + [text], ensure_ascii=False))
    return text


def take() -> list[str]:
    """取出待发的公告（取了就清空）。"""
    out = _load(OUTBOX, [])
    if out:
        set_config(OUTBOX, "")
    return out


def reset() -> None:
    set_config(COUNTS, "")
    set_config(OUTBOX, "")


async def flush(send) -> int:
    """把待发的公告发到公告群。没设公告群就丢掉（不退到别的群）。返回发了几条。"""
    from .announce import get_announce
    texts = take()
    ann = get_announce()
    if not texts or not ann:
        return 0
    n = 0
    for t in texts:
        try:
            await send(ann[0], ann[1], t)
            n += 1
        except Exception:  # noqa: BLE001 公告发不出去就算了，不重试、不影响互动本身
            pass
    return n
