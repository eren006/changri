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
# 对方明确拒收（非静默拉黑）：发件人当场被告知没发出去，所以不算今天的次数、不进冷却。静默拉黑照算，不然发件人能看出来。
REFUSED = "被拉黑（已告知发件人）"


def cooldown_left(role_id: int, kind: str) -> int:
    key = "sms_cooldown_sec" if kind == "sms" else "gift_cooldown_sec"
    cd = get_int(key, 60)
    if cd <= 0:
        return 0
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT MAX(created_ts) AS t FROM messages WHERE from_role_id=? AND kind=? AND error!='{REFUSED}'", (role_id, kind)
        ).fetchone()
    last = row["t"] or 0
    return max(0, last + cd - int(time.time()))


RECALL_WINDOW = 120   # 官方接口只允许撤回 2 分钟内的消息


def day_count(role_id: int, kind: str) -> int:
    """今天（游戏天数，没有季度按自然日）已经发了几条；撤回的不算。"""
    with get_conn() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM messages WHERE from_role_id=? AND kind=? AND day=? AND error NOT IN ('已撤回', '{REFUSED}')",
                         (role_id, kind, season.day_key())).fetchone()[0]
    from . import godmode
    return max(0, n - godmode.offset(role_id, kind))


def daily_limit(kind: str) -> int:
    return get_int("sms_daily_limit" if kind == "sms" else "gift_daily_limit", 0)


def queue_message(kind: str, from_id: int, to_id: int, title: str, content: str, sign: str = "", alias_id: int | None = None, photo: bool = False) -> int:
    """alias_id：匿名对话的短信，入库时一起写上（分两步写的话，中间那一下会被当成真名短信显示出来）。photo：照片礼物（见 sendmsg.photo_gift）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages(kind, from_role_id, to_role_id, title, content, created_ts, day, sign, alias_id, photo) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (kind, from_id, to_id, title, content, int(time.time()), season.day_key(), sign, alias_id, int(photo)),
        )
        return cur.lastrowid


# 收件人那边根本没收到的：礼物丢失、被拉黑。小手机（会话、未读、网页提醒）不给收件人显示这些，发件人照常看到。
def not_received_sql(p: str = "") -> str:
    """p：表别名前缀，比如 "m."。"""
    return f"NOT ({p}status='failed' AND ({p}error LIKE '被拉黑%' OR {p}error LIKE '礼物丢失%'))"


NOT_RECEIVED_SQL = not_received_sql()


def set_original(msg_id: int, orig_to: int, orig_content: str | None) -> None:
    """混乱短信出了岔子：记下发件人原本想发给谁（0 = 没换人）、原本写的什么（None = 没改字）。"""
    with get_conn() as conn:
        conn.execute("UPDATE messages SET orig_to=?, orig_content=? WHERE id=?", (orig_to, orig_content, msg_id))


def last_recallable(role_id: int):
    """我最近发的、已送到对方群里、还在 2 分钟内、没撤回过的一条短信/礼物。"""
    with get_conn() as conn:
        return conn.execute(
            # 误投了也写原本发给的人；匿名对话里回给化名的，写化名（写真名就把化名背后是谁说出去了）
            "SELECT m.*, CASE WHEN a.id IS NOT NULL AND a.owner_id!=m.from_role_id THEN a.alias ELSE t.name END AS to_name FROM messages m "
            "JOIN roles t ON t.id=COALESCE(NULLIF(m.orig_to, 0), m.to_role_id) LEFT JOIN phone_aliases a ON a.id=COALESCE(m.alias_id, NULLIF(m.orig_alias, 0)) "
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


def reply_buttons(m) -> list:
    """短信、礼物送到时下面的「回复」按钮：把「短信 对方名 」「送礼 对方名 」填进输入框，内容让收件人自己写。
    匿名或自定义署名（署名不是本人名字）的不挂：按钮里写的是真名，挂了就把匿名的人暴露了。"""
    from .buttons import Btn
    name = m["from_name"]
    if (m["sign"] and m["sign"] != name) or not name:
        return []
    if m["kind"] == "sms":
        return [Btn("回短信", f"短信 {name} ", enter=False)]
    return [Btn("回礼", f"送礼 {name} ", enter=False), Btn("回短信", f"短信 {name} ", enter=False, style="secondary")]


async def push_to_role_ex(role_id: int, text: str, send: Send, at: bool = True, buttons=None) -> tuple[bool, str, str, str]:
    """把一段文字发到某角色：发到个人群。返回 (是否送达, 群 openid 或错误, 机器人 ID, 消息 id)。
    buttons：消息下面挂的按钮（core/buttons.Btn），只有支持按钮的 send（qqsend.make_send）才会带上，发不了自动退回纯文字。"""
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
            if buttons and getattr(send, "buttons_ok", False):
                mid = await send(g["bot_id"], g["group_openid"], body, buttons=buttons)
            else:
                mid = await send(g["bot_id"], g["group_openid"], body)
        except Exception as e:  # noqa: BLE001 官方接口失败原因很杂，原样记下
            errors.append(f"{g['kind']}:{str(e)[:80]}")
            continue
        return True, g["group_openid"], g["bot_id"], (mid if isinstance(mid, str) else "")
    return False, "; ".join(errors) or "收件人没有可用的群", "", ""


async def push_to_role(role_id: int, text: str, send: Send, at: bool = True, buttons=None) -> tuple[bool, str]:
    ok, info, _bot, _mid = await push_to_role_ex(role_id, text, send, at, buttons)
    return ok, info


def mark_blocked(msg_id: int, silent: bool) -> None:
    """被拉黑的消息不投递。status 只能是 pending/sent/failed，所以记成 failed 并写明原因。"""
    with get_conn() as conn:
        conn.execute("UPDATE messages SET status='failed', error=? WHERE id=?",
                     ("被拉黑（静默，发件人以为已送达）" if silent else REFUSED, msg_id))


def mark_lost(msg_id: int) -> None:
    """礼物在路上丢了（混乱短信的「礼物丢失」）：不投递，记成 failed 并写明原因。"""
    with get_conn() as conn:
        conn.execute("UPDATE messages SET status='failed', error='礼物丢失（发件人以为已送达）' WHERE id=?", (msg_id,))


async def _maybe_broadcast(m, send: Send, shown_to: int = 0) -> None:
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
        to = conn.execute("SELECT name FROM roles WHERE id=?", (shown_to or m["to_role_id"],)).fetchone()
    who = (m["sign"] or m["from_name"]) 
    target = "某人" if hide_receiver("gift") or not to else to["name"]
    try:
        line = f"🎁 {who} 送出了一份礼物「{m['title']}」给 {target}"
        await send(ann[0], ann[1], line)
        from . import publicfeed
        publicfeed.record("gift", line)
    except Exception:  # noqa: BLE001
        pass


async def deliver(msg_id: int, send: Send, shown_to: int = 0) -> bool:
    """投递一条消息：发到个人群。返回是否送达。
    shown_to：礼物送错了人时，公开播报里照旧写原本想送的那个人（写真正收到的人等于把送错这件事说出去了）。"""
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
    ok, info, bot_id, mid = await push_to_role_ex(m["to_role_id"], text, send, buttons=reply_buttons(m))
    if ok:
        img_ids = []
        for data in imgs:                                  # 图片紧跟文字发到同一个群；发不出去不影响短信算送达
            try:
                from changri.core.qqsend import make_send_image
                img_ids.append(await make_send_image()(bot_id, info, data) or "")
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning("[短信图片] 发到对方群失败：%s", e)
        with get_conn() as conn:
            conn.execute(
                "UPDATE messages SET status='sent', attempts=attempts+1, delivered_group=?, delivered_ts=?, error='', "
                "delivered_bot=?, delivered_msg_id=?, delivered_img_ids=? WHERE id=?",
                (info, int(time.time()), bot_id, mid, ",".join(i for i in img_ids if i), msg_id),
            )
        if m["kind"] == "gift":
            await _maybe_broadcast(m, send, shown_to)
        return True
    attempts = m["attempts"] + 1
    status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
    err = info
    with get_conn() as conn:
        conn.execute("UPDATE messages SET status=?, attempts=?, error=? WHERE id=?", (status, attempts, err, msg_id))
    return False


async def retry_pending(send: Send, on_delivered=None) -> int:
    """on_delivered(msg_id, send)：补送成功后要补做的事（截信器 / 回音壁、混乱短信的后续），由 sendmsg.after_delivered 传进来。"""
    cutoff = int(time.time()) - RETRY_WINDOW
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM messages WHERE status='pending' AND attempts>0 AND created_ts>=? ORDER BY id LIMIT 20", (cutoff,)
        )]
    ok = 0
    for i in ids:
        if await deliver(i, send):
            ok += 1
            if on_delivered:
                try:
                    await on_delivered(i, send)
                except Exception as e:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning("[短信] 补送后的后续处理失败：%s", e)
    return ok


def admin_delete(msg_id: int) -> bool:
    """管理员删一条短信/礼物（照老系统管理手机「删除」）：收发双方的小手机里都不再显示；已经发到 QQ 群里的那一条撤不回来。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM message_images WHERE msg_id=?", (msg_id,))
        return conn.execute("DELETE FROM messages WHERE id=?", (msg_id,)).rowcount > 0


def hide_receiver(kind: str) -> bool:
    """公开播报时隐藏收件人：礼物（gift_hide_receiver）和心动信曝光（lovemail_hide_receiver）各一个开关。
    以前两个共用 drop_hide_receiver；没单独设过的，照老开关的值，升级上来行为不变。"""
    from .db import get_config
    return get_config(f"{kind}_hide_receiver", get_config("drop_hide_receiver", "0")) == "1"
