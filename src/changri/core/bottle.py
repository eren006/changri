"""漂流瓶（额外）：不填收件人的匿名短信，随机送到某个玩家手里；双方用同一个编号来回回信。
规则在这里，发送由 plugins/bottle.py 负责。NPC 不参与；会避开拉黑了抛瓶人的人。"""
import random
import time

from . import blocks, season
from .db import get_conn, get_int
from .roles import list_groups


def daily_limit() -> int:
    return get_int("bottle_daily_limit", 3)


def day_count(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM drift_msgs WHERE from_id=? AND day=?", (role_id, season.day_key())).fetchone()[0]


def _precheck(role_id: int, content: str) -> str:
    if not content.strip():
        return "写点什么再扔吧。"
    lim = get_int("msg_max_len", 200)
    if len(content) > lim:
        return f"内容太长了，最多 {lim} 字。"
    cap = daily_limit()
    if cap > 0 and day_count(role_id) >= cap:
        return f"今天的漂流瓶次数用完了（{day_count(role_id)}/{cap}）。"
    return ""


def _log(bottle_id: int, from_id: int, content: str) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO drift_msgs(bottle_id, from_id, content, day, ts) VALUES(?,?,?,?,?)",
                  (bottle_id, from_id, content, season.day_key(), int(time.time())))


def throw(me, content: str) -> tuple[bool, str, int | None, int | None]:
    """扔一个瓶子。返回 (成功, 告诉扔瓶人的话, 捡到的人, 瓶子编号)。"""
    bad = _precheck(me["id"], content)
    if bad:
        return False, bad, None, None
    with get_conn() as c:
        cands = [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0 AND id!=?", (me["id"],))]
    cands = [i for i in cands if not blocks.get_block(i, me["id"]) and any(g["group_openid"] for g in list_groups(i))]
    if not cands:
        return False, "🌊 大海太安静了，暂时没有能接到漂流瓶的人。", None, None
    catcher = random.choice(cands)
    with get_conn() as c:
        bid = c.execute("INSERT INTO drift_bottles(thrower_id, catcher_id, created_ts) VALUES(?,?,?)", (me["id"], catcher, int(time.time()))).lastrowid
    _log(bid, me["id"], content)
    return True, f"🍾 漂流瓶已抛入大海（编号{bid}），说不定哪天会有回信……", catcher, bid


def get(bottle_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM drift_bottles WHERE id=?", (bottle_id,)).fetchone()


def mine(me_id: int, bottle_id: int):
    """这个瓶子是不是我参与的；是就返回记录。"""
    b = get(bottle_id)
    return b if b and me_id in (b["thrower_id"], b["catcher_id"]) else None


def reply(me, bottle_id: int, content: str) -> tuple[bool, str, int | None, bool]:
    """回信。返回 (成功, 告诉回信人的话, 对方角色 id, 是否要真的投递)——对方拉黑了我时「静默」仍显示成功但不投递。"""
    b = mine(me["id"], bottle_id)
    if not b:
        return False, "找不到这个漂流瓶（可能不是你的）。", None, False
    bad = _precheck(me["id"], content)
    if bad:
        return False, bad, None, False
    other = b["catcher_id"] if b["thrower_id"] == me["id"] else b["thrower_id"]
    blk = blocks.get_block(other, me["id"])
    if blk and not blk["silent"]:
        return False, "❌ 对方已拒绝你的联络。", other, False
    _log(bottle_id, me["id"], content)
    return True, f"🍾 回信已经通过漂流瓶（编号{bottle_id}）送出。", other, not blk


def my_bottles(role_id: int) -> list[dict]:
    """我参与的瓶子（新的在前），每个带来回的信：mine = 是不是我写的。对方是谁不显示，保持匿名。"""
    with get_conn() as c:
        out = []
        for b in c.execute("SELECT * FROM drift_bottles WHERE thrower_id=? OR catcher_id=? ORDER BY id DESC LIMIT 50", (role_id, role_id)):
            msgs = [{"mine": m["from_id"] == role_id, "text": m["content"], "ts": m["ts"]} for m in c.execute("SELECT * FROM drift_msgs WHERE bottle_id=? ORDER BY id", (b["id"],))]
            out.append({"id": b["id"], "thrown": b["thrower_id"] == role_id, "msgs": msgs})
        return out
