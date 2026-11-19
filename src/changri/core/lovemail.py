"""心动信：先投进信池，到设定时间统一派送给收信人（发到个人群）。规则对齐长日系统：
- 署名最长 20 字，默认「匿名」；每天投稿有上限（默认 3 封），上限 <= 0 表示当天关闭；
- 派送前可「撤回心动信 序号」，撤回会退回那封信算的那一天的次数；
- 当天的信派送过以后再投的，算第二天（占第二天的次数、第二天派送）——作者定的：没赶上当天派送的都算后一天；
- 收信人拉黑了寄信人：照短信的规矩，明着拉黑直接拒；静默拉黑看着跟投成功一样，信记成 held，派送时吞掉（不送、寄信人那边显示已送出）；
- 简单格式「发送心动信 收信人 内容」不写署名，署名默认是真名（插件里补）；【】格式不写署名还是「匿名」。
- 没有可用群的收信人，信留在池里下次再派；
- 可选「曝光」：派送时按概率把信公开到公告群。
「每天」按游戏天数算（没有进行中的季度时按自然日）。
"""
import random
import re
import time

from . import blocks, season, windows
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


def _key_day(key: str) -> int | None:
    m = re.fullmatch(r"S\d+D(-?\d+)\.\d+", key or "")
    return int(m[1]) if m else None


def next_key(key: str) -> str:
    """计数键的下一天：S3D2.0 → S3D3.0；自然日 2026-11-19 → 2026-11-20。"""
    m = re.fullmatch(r"(S\d+D)(-?\d+)(\.\d+)", key or "")
    if m:
        return f"{m[1]}{int(m[2]) + 1}{m[3]}"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(time.mktime(time.strptime(key, "%Y-%m-%d")) + 36 * 3600))
    except ValueError:
        return key


def day_label(key: str) -> str:
    """给玩家看的那一天：S3D2.0 → D2；自然日原样。"""
    d = _key_day(key)
    return f"D{d}" if d is not None else key


def post_key() -> str:
    """现在投的信算哪一天：当天的定时派送（或后台「立即派送」）跑过了，就算第二天。"""
    k = today()
    return next_key(k) if get_config("lovemail_last_run_key", "") == k else k


def limit_for(key: str) -> int:
    """那一天每人最多投几封：先看这一天有没有特殊上限，没有就用默认。"""
    d = _key_day(key)
    if d is None and key == today():            # 没有进行中的季度、但设了天数（老用法）：照旧按 game_day
        d = season.current_day()
    if d is not None and d in day_limits():
        return day_limits()[d]
    return get_int("lovemail_default_limit", 3)


def today_limit() -> int:
    """今天每人最多投几封。"""
    return limit_for(today())


def parse_post(text: str) -> tuple[str, str, str]:
    """解析投稿。支持旧格式【发送对象】【内容】【署名】（不写署名 =「匿名」），也支持「收信人 内容」（署名留空，由调用的地方填真名）。返回 (收信人, 内容, 署名)。"""
    def tag(name: str) -> str:
        m = re.search(rf"【{name}】\s*(.*?)(?=【|\Z)", text, re.S)
        return m.group(1).strip() if m else ""

    if "【" in text:
        return tag("发送对象") or tag("收件人"), tag("内容"), tag("署名") or "匿名"
    parts = text.strip().split(None, 1)
    return (parts[0], parts[1].strip(), "") if len(parts) == 2 else ("", "", "")       # 简单格式：署名留空，插件补成真名


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
    if to_id == from_id:
        return False, "⚠️ 不能给自己写心动信。", 0
    key = post_key()
    late = key != today()
    limit = limit_for(key)
    if limit <= 0:
        return False, ("📪 今天的心动信已经派送过了，明天的投稿没有开放。" if late else "📪 当前的心动信投稿已关闭。"), 0
    used = day_count(from_id, key)
    if used >= limit:
        return False, (f"📪 今天的心动信已经派送过了，现在投的算明天，明天的 {limit} 封你已经投满了。" if late
                       else f"📪 今天你已投稿 {used} 封（上限 {limit} 封）。请等明天再试。"), 0
    blk = blocks.get_block(to_id, from_id)
    if blk and not blk["silent"]:
        from . import roles
        got = roles.get_role(to_id)
        return False, f"❌ 「{got['name'] if got else '对方'}」已拒绝你的联络。", 0
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO lovemails(from_role_id, to_role_id, signature, content, day, created_ts, status) VALUES(?,?,?,?,?,?,?)",
            (from_id, to_id, signature, content, key, int(time.time()), "held" if blk else "pool"),
        )
    return True, ("🌙 今天的心动信已经派送过了，这封算明天的，明天派送。" if late else ""), limit - used - 1


def my_pool(role_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.*, r.name AS to_name FROM lovemails m JOIN roles r ON r.id=m.to_role_id "
            "WHERE m.from_role_id=? AND m.status IN ('pool','held') ORDER BY m.id", (role_id,)
        ).fetchall()


def revoke(role_id: int, index: int) -> tuple[bool, str]:
    """撤回自己信池里的第 index 封（从 1 开始）。删除后当天次数自然恢复。"""
    mine = my_pool(role_id)
    if index < 1 or index > len(mine):
        return False, "⚠️ 请输入正确的序号，例如：撤回心动信 1"
    m = mine[index - 1]
    with get_conn() as conn:
        conn.execute("DELETE FROM lovemails WHERE id=?", (m["id"],))
    return True, f"✅ 已成功撤回发送给「{m['to_name']}」的信件。\n📪 已恢复你 {day_label(m['day'])} 的 1 次发送机会。"


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
    with get_conn() as conn:                    # 静默拉黑的信：寄信人那边显示已送出，收信人收不到
        conn.execute("UPDATE lovemails SET status='swallowed', sent_ts=? WHERE status='held'", (int(time.time()),))
    pool = pool_all()
    if not pool:
        return {"success": 0, "fail": 0, "empty": True}
    expose = get_config("lovemail_expose", "0") == "1" and bool(announce)
    chance = get_int("lovemail_expose_chance", 10)
    from .messaging import hide_receiver as _hide
    hide_receiver = _hide("lovemail")
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
        from . import milestone
        milestone.bump("心动信", len(mails))
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
        sent = [dict(r) for r in conn.execute("SELECT m.*, t.name AS to_name FROM lovemails m JOIN roles t ON t.id=m.to_role_id WHERE m.from_role_id=? AND m.status IN ('sent','swallowed') ORDER BY m.id DESC", (role_id,))]
    return recv, sent


def revoke_id(role_id: int, mail_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        m = conn.execute("SELECT m.*, t.name AS to_name FROM lovemails m JOIN roles t ON t.id=m.to_role_id WHERE m.id=? AND m.from_role_id=? AND m.status IN ('pool','held')", (mail_id, role_id)).fetchone()
        if not m:
            return False, "这封信已经派送了，或者不在信池里。"
        conn.execute("DELETE FROM lovemails WHERE id=?", (mail_id,))
    return True, f"已撤回寄给「{m['to_name']}」的信，{day_label(m['day'])} 的次数也恢复了。"


def state(role_id: int) -> dict:
    """页面顶上那行和能不能写信。"""
    from . import godmode
    why = ""
    if not enabled():
        why = "心动信目前没有开放。"
    else:
        why = windows.check("lovemail") or godmode.check(role_id, "lovemail")
        if not why and limit_for(post_key()) <= 0:
            why = "当前的心动信投稿已关闭。"
    key = post_key()
    limit, used = limit_for(key), day_count(role_id, key)
    if not why and used >= limit:
        why = (f"今天的已经派送过了，现在投的算明天；明天的 {limit} 封已经投满了。" if key != today()
               else f"今天已经投了 {used} 封（上限 {limit} 封），明天再来吧。")
    return {"game_day": today(), "used": used, "limit": limit, "can": not why, "why": why,
            "delivery_time": (get_config("lovemail_delivery_time", "22:00").replace('"', "").strip() or "22:00")}
