"""混乱短信（额外）：发出去的短信会按概率出岔子——错字、丢失后半截、涂黑、句子乱序、落款写错、送错人、被撕成两半。
每种出岔子的概率（百分数）在设置里调，全是 0 就是普通短信。接在 core/sendmsg.py 的短信发送里（群里和小手机共用）。
（以前有单独的「寄信」指令，0.21 起并进了短信；表名 chaos_letters 沿用，只记出了岔子的短信。）"""
import random
import re
import time

from . import season
from .db import get_conn, get_int

POOL = list("梦影幻虚无断零终念尘迹雾嘘寂")
EFFECTS = [("typo", "错字", "chaos_typo", 10), ("lose", "丢失后半截", "chaos_lose", 5), ("blackout", "涂黑", "chaos_blackout", 10),
           ("reverse", "句子乱序", "chaos_reverse", 5), ("sign", "落款写错", "chaos_sign", 5), ("mis", "误投", "chaos_mis", 5), ("torn", "撕成两半", "chaos_torn", 5)]


def pct(key: str, default: int) -> int:
    return min(100, max(0, get_int(key, default)))


def daily_limit() -> int:
    return get_int("letter_daily_limit", 5)


def day_count(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM chaos_letters WHERE from_id=? AND day=?", (role_id, season.day_key())).fetchone()[0]


def cooldown_left(role_id: int) -> int:
    cd = get_int("letter_cooldown_min", 2) * 60
    with get_conn() as c:
        last = c.execute("SELECT MAX(ts) FROM chaos_letters WHERE from_id=?", (role_id,)).fetchone()[0] or 0
    return max(0, last + cd - int(time.time())) if cd > 0 else 0


def _hit(key: str, default: int, rng) -> bool:
    return rng.random() * 100 < pct(key, default)


def erode(text: str, rng=random) -> tuple[str, list[str]]:
    """内容侵蚀：错字、丢后半截、涂黑、句序打乱，各自按概率独立触发。返回 (新内容, 触发了哪些)。"""
    hit = []
    if _hit("chaos_typo", 10, rng) and text:
        chars = list(text)
        for _ in range(max(1, int(len(chars) * (0.15 + rng.random() * 0.1)))):
            chars[rng.randrange(len(chars))] = rng.choice(POOL)
        text = "".join(chars)
        hit.append("错字")
    if _hit("chaos_lose", 5, rng) and len(text) > 5:
        text = text[: int(len(text) * 0.7)] + "……"
        hit.append("丢失后半截")
    if _hit("chaos_blackout", 10, rng):
        text = "".join(rng.choice("◼█■▮") if rng.random() < 0.2 else c for c in text)
        hit.append("涂黑")
    if _hit("chaos_reverse", 5, rng):
        parts = re.findall(r"[^。！？!?\n]+[。！？!?\n]*", text)
        if len(parts) > 1:
            orig = "".join(parts)
            rng.shuffle(parts)
            new = "".join(parts)
            text = new if new != orig else "".join(parts[1:]) + parts[0]
            hit.append("句子乱序")
    return text, hit


def pick_others(exclude: set[int], rng=random) -> int | None:
    with get_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0")]
    ids = [i for i in ids if i not in exclude]
    return rng.choice(ids) if ids else None


def plain(me, to, content: str) -> dict:
    """没开「混乱短信」：原样送到。"""
    return {"shown": content, "sign": me["name"], "true_to": to["id"], "torn": None, "effects": []}


def compose(me, to, content: str, rng=random) -> dict:
    """生成这封信的最终样子。返回 shown（收件人看到的）、sign、true_to（真正收到的人）、torn（None 或 (第三人, 后半页)）、effects。"""
    shown, effects = erode(content, rng)
    sign = me["name"]
    if _hit("chaos_sign", 5, rng):
        other = pick_others({me["id"]}, rng)
        if other:
            with get_conn() as c:
                sign = c.execute("SELECT name FROM roles WHERE id=?", (other,)).fetchone()["name"]
            effects.append("落款写错")
    true_to = to["id"]
    if _hit("chaos_mis", 5, rng):
        other = pick_others({me["id"], to["id"]}, rng)
        if other:
            true_to = other
            effects.append("误投")
    torn = None
    if _hit("chaos_torn", 5, rng) and len(shown) >= 4:
        cut = len(shown) // 2
        third = pick_others({me["id"], true_to}, rng)
        if third:
            torn = (third, shown[cut:])
            shown = shown[:cut] + "……（后半截被撕走了）"
            effects.append("撕成两半")
    return {"shown": shown, "sign": sign, "true_to": true_to, "torn": torn, "effects": effects}


def log(me_id: int, to_id: int, c: dict, original: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO chaos_letters(from_id, to_id, true_to_id, content, shown, effects, day, ts) VALUES(?,?,?,?,?,?,?,?)",
                     (me_id, to_id, c["true_to"], original, c["shown"], "、".join(c["effects"]), season.day_key(), int(time.time())))
