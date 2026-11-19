"""小手机匿名对话（照老系统 rp_archive 的「匿名对话（化名）」）：
- 「新信息」页写化名、选收件人，开一段能来回聊的对话；对同一个人最多 3 个化名，同一收件人下化名不能重名，化名最多 10 个字；
- 收件人那边对话标题是化名；收件人回复送回化名主人；
- 跟短信共用每日次数、冷却、开放时段、功能权限；
- 混乱短信开着时也会出岔子（作者 2026-11-18 定的）：抹字、撕半照常；误投给第三个人时落款仍是化名，进他的署名会话（不能回），
  发件人那边还在这段匿名对话里看到原文；不出「落款写错」（落款就是化名，换成真名会串线）；公告播报只写化名，不写背后是谁；
- 不看按真实身份的拉黑（不然「拉黑某人看化名对话断不断」就能反查出化名是谁）；匿名对话有自己的「拉黑并结束」，满 2 小时才能解除；
- 只有管理员能看到化名背后是谁。
短信本身走 messages 表（alias_id 不为空）和 messaging.deliver：化名主人发的落款是化名；收件人回的落款写明「回你的化名」。"""
import time

from .db import get_conn, get_int

MAX_PER_TARGET = 3
MAX_LEN = 10
UNBLOCK_SECS = 2 * 3600
RESERVED = {"未知号码", "点歌台", "偷盗记录", "群号通知", "大家", "所有人", "管理员", "小手机", "有人"}


def get(alias_id: int):
    with get_conn() as c:
        return c.execute("SELECT a.*, o.name AS owner_name, t.name AS target_name FROM phone_aliases a "
                         "JOIN roles o ON o.id=a.owner_id JOIN roles t ON t.id=a.target_id WHERE a.id=?", (alias_id,)).fetchone()


def side(a, role_id: int) -> str:
    """'owner'（化名主人）/ 'target'（收件人）/ ''（不相干）。"""
    return "owner" if a and a["owner_id"] == role_id else ("target" if a and a["target_id"] == role_id else "")


def title_for(a, role_id: int) -> str:
    """对话标题：主人那边是「对方＠化名」，收件人那边只有化名（绝不露真名）。"""
    return f"{a['target_name']}＠{a['alias']}" if side(a, role_id) == "owner" else a["alias"]


def create(owner_id: int, target_id: int, name: str) -> tuple[bool, str, int]:
    """建化名（或复用自己对这个人已有的同名化名）→ (ok, 提示, alias_id)。"""
    from . import charprofile
    name = (name or "").strip()
    if target_id == owner_id:
        return False, "选一个要联系的人。", 0
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM roles WHERE id=?", (target_id,)).fetchone():
            return False, "选一个要联系的人。", 0
    if not name:
        return False, "写一个化名。", 0
    if len(name) > MAX_LEN:
        return False, f"化名最多 {MAX_LEN} 字。", 0
    if "＠" in name or "@" in name or name in RESERVED or charprofile.name_taken(name):
        return False, "这个化名不能用，换一个。", 0
    with get_conn() as c:
        mine = c.execute("SELECT id, alias FROM phone_aliases WHERE owner_id=? AND target_id=?", (owner_id, target_id)).fetchall()
        hit = next((r for r in mine if r["alias"] == name), None)
        if hit:
            return True, "", hit["id"]
        if len(mine) >= MAX_PER_TARGET:
            return False, f"对同一个人最多用 {MAX_PER_TARGET} 个化名，已经用了：" + "、".join(r["alias"] for r in mine), 0
        if c.execute("SELECT 1 FROM phone_aliases WHERE target_id=? AND alias=?", (target_id, name)).fetchone():
            return False, "这个化名已经被别人用了，换一个。", 0
        aid = c.execute("INSERT INTO phone_aliases(owner_id, target_id, alias, created_ts) VALUES(?,?,?,?)",
                        (owner_id, target_id, name, int(time.time()))).lastrowid
    return True, "", aid


def precheck(role, a, text: str) -> str:
    """能发返回空串。规则同短信（时段、功能权限、长度、每日次数、冷却），但不看真实身份的拉黑。"""
    from . import godmode, messaging, windows
    if not side(a, role["id"]):
        return "找不到这段匿名对话。"
    if a["blocked_by"]:
        return "这段匿名对话已经结束了。"
    closed = windows.check("sms") or godmode.check(role["id"], "sms")
    if closed:
        return closed
    text = (text or "").strip()
    if not text:
        return "内容不能为空。"
    lim_len = get_int("msg_max_len", 200)
    if len(text) > lim_len:
        return f"内容太长了，最多 {lim_len} 字。"
    lim, used = messaging.daily_limit("sms"), messaging.day_count(role["id"], "sms")
    if lim > 0 and used >= lim:
        return f"今天的短信次数用完了（{used}/{lim}），天数更新后再发。"
    left = messaging.cooldown_left(role["id"], "sms")
    if left:
        return f"发得太快了，{left} 秒后再试。"
    return ""


async def send(role, alias_id: int, text: str, send_fn) -> tuple[bool, str]:
    from . import messaging
    a = get(alias_id)
    why = precheck(role, a, text)
    if why:
        return False, why
    text = text.strip()
    if side(a, role["id"]) == "owner":            # 化名主人 → 对方：落款是化名
        frm, to, sign, shown = a["owner_id"], a["target_id"], a["alias"], a["target_name"]
    else:                                         # 对方 → 化名主人：落款写清是回给哪个化名的
        frm, to, sign, shown = a["target_id"], a["owner_id"], f"{a['target_name']}（回你的化名「{a['alias']}」）", a["alias"]
    c = _chaos(frm, to, sign, text)
    content, true_to = (c["shown"], c["true_to"]) if c else (text, to)
    if true_to != to:                             # 误投：第三个人不在这段对话里，按落款进他的署名会话；orig_alias 让发件人这边还挂在匿名对话上
        mid = messaging.queue_message("sms", frm, true_to, "", content, sign)
        with get_conn() as conn:
            conn.execute("UPDATE messages SET orig_alias=? WHERE id=?", (alias_id, mid))
    else:
        mid = messaging.queue_message("sms", frm, to, "", content, sign, alias_id=alias_id)
    if c:
        from . import chaosletter
        messaging.set_original(mid, to if true_to != to else 0, text if content != text else None)
        chaosletter.attach(c["log_id"], mid, c, to_shown=shown)        # 公告里写化名 / 收件人，不写化名背后是谁
    lim, used = messaging.daily_limit("sms"), messaging.day_count(role["id"], "sms")
    tail = f"（今天 {used}/{lim}）" if lim > 0 else ""
    if await messaging.deliver(mid, send_fn):
        from . import sendmsg
        await sendmsg.after_delivered(mid, send_fn)
        return True, f"已送达「{shown}」。{tail}"
    return True, f"「{shown}」那边暂时收不到，已记下，稍后会自动重试。"


def _chaos(frm: int, to: int, sign: str, text: str) -> dict | None:
    """混乱短信开着：按概率改这条匿名短信。不出「落款写错」（落款就是化名）。没出岔子返回 None。"""
    from . import chaosletter, extensions
    if not extensions.is_enabled("chaos_letter"):
        return None
    c = chaosletter.compose({"id": frm, "name": sign}, {"id": to}, text)
    c["sign"] = sign
    c["effects"] = [e for e in c["effects"] if e != "落款写错"]
    if not c["effects"]:
        return None
    c["log_id"] = chaosletter.log(frm, to, c, text)
    return c


def block(role, alias_id: int, unblock: bool = False, now: int | None = None) -> tuple[bool, str]:
    """「拉黑并结束 / 解除拉黑」：只作用于这个化名；拉黑的一方满 2 小时才能解除。"""
    a = get(alias_id)
    who = side(a, role["id"])
    if not who:
        return False, "这段对话不能由你结束。"
    now = int(now or time.time())
    if unblock:
        if a["blocked_by"] != who:
            return False, "只有拉黑的一方能解除。"
        if now - a["blocked_ts"] < UNBLOCK_SECS:
            return False, f"拉黑满 2 小时才能解除，还要等 {-(-(a['blocked_ts'] + UNBLOCK_SECS - now) // 60)} 分钟。"
        with get_conn() as c:
            c.execute("UPDATE phone_aliases SET blocked_by='', blocked_ts=0 WHERE id=?", (alias_id,))
        return True, "已解除拉黑，这段匿名对话可以继续了。"
    if a["blocked_by"]:
        return False, "这段匿名对话已经结束了。"
    with get_conn() as c:
        c.execute("UPDATE phone_aliases SET blocked_by=?, blocked_ts=? WHERE id=?", (who, now, alias_id))
    return True, "已拉黑，这段匿名对话结束了。满 2 小时后可以在这里解除。"


def threads(role_id: int) -> list[dict]:
    """我参与的匿名对话（会话列表用）：{alias_id, title, preview, ts}。"""
    with get_conn() as c:
        rows = c.execute("SELECT a.*, o.name AS owner_name, t.name AS target_name FROM phone_aliases a JOIN roles o ON o.id=a.owner_id "
                         "JOIN roles t ON t.id=a.target_id WHERE a.owner_id=? OR a.target_id=?", (role_id, role_id)).fetchall()
        out = []
        for a in rows:
            ms = messages(a["id"], role_id)
            m = ms[-1] if ms else None
            if not m and a["target_id"] == role_id:          # 化名还没发过信：收件人那边先不出现
                continue
            from .phoneview import text_of
            out.append({"alias_id": a["id"], "title": title_for(a, role_id), "preview": ((text_of(m, role_id) or "") if m else "（还没有发信）")[:40],
                        "ts": m["created_ts"] if m else a["created_ts"], "blocked": bool(a["blocked_by"])})
    return out


def messages(alias_id: int, role_id: int) -> list:
    """这段匿名对话里 role_id 能看到的：真在对话里的，加上自己发出去却被误投给别人的（对方没收到，不给对方看）。"""
    with get_conn() as c:
        return c.execute("SELECT * FROM messages WHERE (alias_id=?1 OR (orig_alias=?1 AND from_role_id=?2)) AND error!='已撤回' ORDER BY id",
                         (alias_id, role_id)).fetchall()


def wait_minutes(a, now: int | None = None) -> int:
    if not a["blocked_by"]:
        return 0
    return max(0, -(-(a["blocked_ts"] + UNBLOCK_SECS - int(now or time.time())) // 60))
