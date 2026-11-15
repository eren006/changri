"""心动信：先投进信池，到设定时间统一派送给收信人（主群优先，失败试备用群）。规则对齐长日系统：
- 署名最长 20 字，默认「匿名」；每天投稿有上限（默认 3 封），上限 <= 0 表示当天关闭；
- 派送前可「撤回心动信 序号」，撤回会退回当天的次数；
- 没有可用群的收信人，信留在池里下次再派；
- 可选「曝光」：派送时按概率把信公开到公告群。
「每天」按游戏天数算（没有进行中的季度时按自然日）。
"""
import random
import re
import time

from . import season, windows
from .textutil import CHUNK, chunk_text
from .db import get_config, get_conn, get_int
from .messaging import Send, push_to_role

SIGNATURE_MAX = 20


def today() -> str:
    """「每天」的计数键：有进行中的季度按游戏天数，否则按自然日（见 core/season.py）。"""
    return season.day_key()


def enabled() -> bool:
    return get_config("lovemail_enabled", "1") != "0"


def day_limits() -> dict[int, int]:
    """按天特殊上限：配置 lovemail_day_limits 写成「D0:3 D1:5」，没写到的天用默认上限。"""
    out = {}
    for m in re.finditer(r"[Dd](\d+)\s*[:：]\s*(\d+)", get_config("lovemail_day_limits", "")):
        out[int(m.group(1))] = int(m.group(2))
    return out


def today_limit() -> int:
    """今天每人最多投几封：先看这一天有没有特殊上限，没有就用默认。"""
    d = season.current_day()
    if d is not None and d in day_limits():
        return day_limits()[d]
    return get_int("lovemail_default_limit", 3)


def parse_post(text: str) -> tuple[str, str, str]:
    """解析投稿。支持旧格式【发送对象】【内容】【署名】，也支持「收信人 内容」。返回 (收信人, 内容, 署名)。"""
    def tag(name: str) -> str:
        m = re.search(rf"【{name}】\s*(.*?)(?=【|\Z)", text, re.S)
        return m.group(1).strip() if m else ""

    if "【" in text:
        return tag("发送对象") or tag("收件人"), tag("内容"), tag("署名") or "匿名"
    parts = text.strip().split(None, 1)
    return (parts[0], parts[1].strip(), "匿名") if len(parts) == 2 else ("", "", "匿名")


def day_count(role_id: int, day: str | None = None) -> int:
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM lovemails WHERE from_role_id=? AND day=?", (role_id, day or today())).fetchone()[0]
    if day is None or day == today():
        from . import godmode
        n = max(0, n - godmode.offset(role_id, "lovemail"))
    return n


def add_mail(from_id: int, to_id: int, signature: str, content: str) -> tuple[bool, str, int]:
    """投稿。返回 (是否成功, 提示, 今日剩余次数)。"""
    if not enabled():
        return False, "📪 心动信目前没有开放。", 0
    from . import godmode
    closed = windows.check("lovemail") or godmode.check(from_id, "lovemail")
    if closed:
        return False, closed, 0
    if len(signature) > SIGNATURE_MAX:
        return False, f"⚠️ 署名不得超过 {SIGNATURE_MAX} 个字（当前 {len(signature)} 个字），请修改后重新投递。", 0
    limit_len = get_int("lovemail_max_len", 500)
    if not content or len(content) > limit_len:
        return False, f"⚠️ 心动信内容不能为空，也不能超过 {limit_len} 字。", 0
    limit = today_limit()
    if limit <= 0:
        return False, "📪 当前的心动信投稿已关闭。", 0
    used = day_count(from_id)
    if used >= limit:
        return False, f"📪 今天你已投稿 {used} 封（上限 {limit} 封）。请等明天再试。", 0
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO lovemails(from_role_id, to_role_id, signature, content, day, created_ts) VALUES(?,?,?,?,?,?)",
            (from_id, to_id, signature, content, today(), int(time.time())),
        )
    return True, "", limit - used - 1


def my_pool(role_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.*, r.name AS to_name FROM lovemails m JOIN roles r ON r.id=m.to_role_id "
            "WHERE m.from_role_id=? AND m.status='pool' ORDER BY m.id", (role_id,)
        ).fetchall()


def revoke(role_id: int, index: int) -> tuple[bool, str]:
    """撤回自己信池里的第 index 封（从 1 开始）。删除后当天次数自然恢复。"""
    mine = my_pool(role_id)
    if index < 1 or index > len(mine):
        return False, "⚠️ 请输入正确的序号，例如：撤回心动信 1"
    m = mine[index - 1]
    with get_conn() as conn:
        conn.execute("DELETE FROM lovemails WHERE id=?", (m["id"],))
    return True, f"✅ 已成功撤回发送给「{m['to_name']}」的信件。\n📪 已恢复你在 {m['day']} 的 1 次发送机会。"


def pool_all() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.*, f.name AS from_name, t.name AS to_name FROM lovemails m "
            "JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
            "WHERE m.status='pool' ORDER BY m.to_role_id, m.id"
        ).fetchall()


def render_for_receiver(name: str, mails: list) -> str:
    text = f"💌 亲爱的 {name}，你有一份包含 {len(mails)} 封信件的心动信件包：\n"
    for i, m in enumerate(mails, 1):
        text += f"\n第 {i} 封信件\n「 {m['content']} 」\n署名：{m['signature']}\n┈┈┈┈┈┈┈┈┈┈"
    return text


async def deliver_all(send: Send, announce: tuple[str, str] | None = None) -> dict:
    """派送信池里所有的信：按收信人归拢，每人一包。返回统计。"""
    pool = pool_all()
    if not pool:
        return {"success": 0, "fail": 0, "empty": True}
    expose = get_config("lovemail_expose", "0") == "1" and bool(announce)
    chance = get_int("lovemail_expose_chance", 10)
    hide_receiver = get_config("drop_hide_receiver", "0") == "1"
    by_receiver: dict[int, list] = {}
    for m in pool:
        by_receiver.setdefault(m["to_role_id"], []).append(m)
    success = fail = max_recv = 0
    public: list[str] = []
    for rid, mails in by_receiver.items():
        name = mails[0]["to_name"]
        ok = True
        first = True
        for part in chunk_text(render_for_receiver(name, mails)):
            ok_part, _ = await push_to_role(rid, part, send)
            if not ok_part:
                ok = False
                break
            first = False
        if not ok and first:        # 一条都没发出去：整包留在池里
            fail += len(mails)
            continue
        success += len(mails)
        max_recv = max(max_recv, len(mails))
        now = int(time.time())
        with get_conn() as conn:
            conn.execute(f"UPDATE lovemails SET status='sent', sent_ts=? WHERE id IN ({','.join('?' * len(mails))})",
                         (now, *[m["id"] for m in mails]))
        for m in mails:
            if expose and random.randint(1, 100) <= chance:
                who = "某人" if hide_receiver else name
                public.append(f"📩 寄给「{who}」的心动信\n来自「{m['signature']}」\n「 {m['content']} 」")
    if announce:
        try:
            from . import publicfeed
            if public:
                text = f"✨ 哎呀，有 {len(public)} 份心意在飞往信箱的途中被风吹散了……\n\n" + "\n\n".join(public)
                await send(*announce, text)
                publicfeed.record("lovemail", text)
            if success:
                text = f"💌 今日心动信已派送完毕，共派送 {success} 封信件，最高收信 {max_recv} 封～"
                await send(*announce, text)
                publicfeed.record("lovemail", text)
        except Exception:  # noqa: BLE001 公告发不出去不影响派送结果
            pass
    return {"success": success, "fail": fail, "empty": False, "public": len(public)}


# ---------- 小手机用 ----------

def history(role_id: int) -> tuple[list[dict], list[dict]]:
    """(收到的, 寄出且已派送的)，新的在前。收到的只有署名，不带寄信人。"""
    with get_conn() as conn:
        recv = [dict(r) for r in conn.execute("SELECT * FROM lovemails WHERE to_role_id=? AND status='sent' ORDER BY id DESC", (role_id,))]
        sent = [dict(r) for r in conn.execute("SELECT m.*, t.name AS to_name FROM lovemails m JOIN roles t ON t.id=m.to_role_id WHERE m.from_role_id=? AND m.status='sent' ORDER BY m.id DESC", (role_id,))]
    return recv, sent


def revoke_id(role_id: int, mail_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        m = conn.execute("SELECT m.*, t.name AS to_name FROM lovemails m JOIN roles t ON t.id=m.to_role_id WHERE m.id=? AND m.from_role_id=? AND m.status='pool'", (mail_id, role_id)).fetchone()
        if not m:
            return False, "这封信已经派送了，或者不在信池里。"
        conn.execute("DELETE FROM lovemails WHERE id=?", (mail_id,))
    return True, f"已撤回寄给「{m['to_name']}」的信，今天的次数也恢复了。"


def state(role_id: int) -> dict:
    """页面顶上那行和能不能写信。"""
    from . import godmode
    why = ""
    if not enabled():
        why = "心动信目前没有开放。"
    else:
        why = windows.check("lovemail") or godmode.check(role_id, "lovemail")
        if not why and today_limit() <= 0:
            why = "当前的心动信投稿已关闭。"
    limit, used = today_limit(), day_count(role_id)
    if not why and used >= limit:
        why = f"今天已经投了 {used} 封（上限 {limit} 封），明天再来吧。"
    return {"game_day": today(), "used": used, "limit": limit, "can": not why, "why": why,
            "delivery_time": (get_config("lovemail_delivery_time", "22:00").replace('"', "").strip() or "22:00")}
