"""短信与礼物：入库、冷却、投递（发到个人群）、重试。

发送函数 send(bot_id, group_openid, text) 由调用方传入（不同的群可能属于不同的机器人），这里不依赖 QQ。
"""
import time
from typing import Awaitable, Callable

from . import season
from .db import get_conn, get_int
from .roles import list_groups

Send = Callable[[str, str, str], Awaitable[object]]   # send(bot_id, group_openid, text)，可以返回发出去的那条消息的 id（撤回要用）

MAX_ATTEMPTS = 3
RETRY_WINDOW = 24 * 3600


def cooldown_left(role_id: int, kind: str) -> int:
    key = "sms_cooldown_sec" if kind == "sms" else "gift_cooldown_sec"
    cd = get_int(key, 60)
    if cd <= 0:
        return 0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(created_ts) AS t FROM messages WHERE from_role_id=? AND kind=?", (role_id, kind)
        ).fetchone()
    last = row["t"] or 0
    return max(0, last + cd - int(time.time()))


RECALL_WINDOW = 120   # 官方接口只允许撤回 2 分钟内的消息


def day_count(role_id: int, kind: str) -> int:
    """今天（游戏天数，没有季度按自然日）已经发了几条；撤回的不算。"""
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE from_role_id=? AND kind=? AND day=? AND error!='已撤回'",
                         (role_id, kind, season.day_key())).fetchone()[0]
    from . import godmode
    return max(0, n - godmode.offset(role_id, kind))


def daily_limit(kind: str) -> int:
    return get_int("sms_daily_limit" if kind == "sms" else "gift_daily_limit", 0)


def queue_message(kind: str, from_id: int, to_id: int, title: str, content: str, sign: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages(kind, from_role_id, to_role_id, title, content, created_ts, day, sign) VALUES(?,?,?,?,?,?,?,?)",
            (kind, from_id, to_id, title, content, int(time.time()), season.day_key(), sign),
        )
        return cur.lastrowid


def last_recallable(role_id: int):
    """我最近发的、已送到对方群里、还在 2 分钟内、没撤回过的一条短信/礼物。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.*, t.name AS to_name FROM messages m JOIN roles t ON t.id=m.to_role_id "
            "WHERE m.from_role_id=? AND m.status='sent' AND m.delivered_msg_id!='' AND m.error!='已撤回' AND m.delivered_ts>=? "
            "ORDER BY m.id DESC LIMIT 1", (role_id, int(time.time()) - RECALL_WINDOW)).fetchone()


def mark_recalled(msg_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE messages SET status='failed', error='已撤回' WHERE id=?", (msg_id,))


def render(kind: str, from_name: str, title: str, content: str) -> str:
    if kind == "sms":
        return f"📩 {from_name} 给你发来一条短信\n{content}"
    text = f"🎁 {from_name} 送了你「{title}」"
    return text + (f"\n留言：{content}" if content else "")


async def push_to_role_ex(role_id: int, text: str, send: Send, at: bool = True) -> tuple[bool, str, str, str]:
    """把一段文字发到某角色：发到个人群。返回 (是否送达, 群 openid 或错误, 机器人 ID, 消息 id)。"""
    errors = []
    with get_conn() as conn:
        r = conn.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
    name = (r["name"] if r else "").replace("}", "")
    for g in list_groups(role_id):
        if not g["group_openid"]:   # 后台登记了群号但还没认群
            continue
        # 发给本人的通知（短信、礼物、邀请……）开头 @ 本人；认过群成员才能 @，文本已经带了 @ 的不重复加
        body = "{{AT:%s:%s}}%s" % (g["member_openid"], name, text) if at and g["member_openid"] and name and not text.startswith("{{AT:") else text
        try:
            mid = await send(g["bot_id"], g["group_openid"], body)
        except Exception as e:  # noqa: BLE001 官方接口失败原因很杂，原样记下
            errors.append(f"{g['kind']}:{str(e)[:80]}")
            continue
        return True, g["group_openid"], g["bot_id"], (mid if isinstance(mid, str) else "")
    return False, "; ".join(errors) or "收件人没有可用的群", "", ""


async def push_to_role(role_id: int, text: str, send: Send, at: bool = True) -> tuple[bool, str]:
    ok, info, _bot, _mid = await push_to_role_ex(role_id, text, send, at)
    return ok, info


def mark_blocked(msg_id: int, silent: bool) -> None:
    """被拉黑的消息不投递。status 只能是 pending/sent/failed，所以记成 failed 并写明原因。"""
    with get_conn() as conn:
        conn.execute("UPDATE messages SET status='failed', error=? WHERE id=?",
                     ("被拉黑（静默，发件人以为已送达）" if silent else "被拉黑（已告知发件人）", msg_id))


async def _maybe_broadcast(m, send: Send) -> None:
    """礼物送到后，按设置的概率公开到公告群（图个热闹）。公开失败不影响送达。"""
    import random

    from .announce import get_announce
    from .db import get_config

    if get_config("gift_public_send", "0") != "1" or random.random() * 100 >= get_int("gift_public_chance", 50):
        return
    ann = get_announce()
    if not ann:
        return
    with get_conn() as conn:
        to = conn.execute("SELECT name FROM roles WHERE id=?", (m["to_role_id"],)).fetchone()
    who = (m["sign"] or m["from_name"]) 
    target = "某人" if get_config("drop_hide_receiver", "0") == "1" or not to else to["name"]
    try:
        line = f"🎁 {who} 送出了一份礼物「{m['title']}」给 {target}"
        await send(ann[0], ann[1], line)
        from . import publicfeed
        publicfeed.record("gift", line)
    except Exception:  # noqa: BLE001
        pass


async def deliver(msg_id: int, send: Send) -> bool:
    """投递一条消息：发到个人群。返回是否送达。"""
    with get_conn() as conn:
        m = conn.execute(
            "SELECT m.*, f.name AS from_name FROM messages m JOIN roles f ON f.id=m.from_role_id WHERE m.id=?",
            (msg_id,),
        ).fetchone()
    if not m or m["status"] == "sent":
        return bool(m and m["status"] == "sent")
    from . import msgimages
    imgs = msgimages.blobs(msg_id)
    text = render(m["kind"], m["sign"] or m["from_name"], m["title"], m["content"] or ("（图片）" if imgs else ""))
    ok, info, bot_id, mid = await push_to_role_ex(m["to_role_id"], text, send)
    if ok:
        for data in imgs:                                  # 图片紧跟文字发到同一个群；发不出去不影响短信算送达
            try:
                from changri.core.qqsend import make_send_image
                await make_send_image()(bot_id, info, data)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning("[短信图片] 发到对方群失败：%s", e)
        with get_conn() as conn:
            conn.execute(
                "UPDATE messages SET status='sent', attempts=attempts+1, delivered_group=?, delivered_ts=?, error='', "
                "delivered_bot=?, delivered_msg_id=? WHERE id=?",
                (info, int(time.time()), bot_id, mid, msg_id),
            )
        if m["kind"] == "gift":
            await _maybe_broadcast(m, send)
        return True
    attempts = m["attempts"] + 1
    status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
    err = info
    with get_conn() as conn:
        conn.execute("UPDATE messages SET status=?, attempts=?, error=? WHERE id=?", (status, attempts, err, msg_id))
    return False


async def retry_pending(send: Send) -> int:
    cutoff = int(time.time()) - RETRY_WINDOW
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM messages WHERE status='pending' AND attempts>0 AND created_ts>=? ORDER BY id LIMIT 20", (cutoff,)
        )]
    ok = 0
    for i in ids:
        if await deliver(i, send):
            ok += 1
    return ok
