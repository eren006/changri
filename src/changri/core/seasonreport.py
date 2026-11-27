"""季末报告：结束季度时，给每个玩家的个人群发一份本季互动报告（设置在「基础」里，默认关）。

先发一句文字，再单独发一张结算海报（core/render/season_poster.py，皮肤在「基础」里选）；
图片必须单独一条发：夹在文字或 Markdown 里，官方机器人发不出去。出图或发图失败就改发文字小结。
内容：约会场次、短信/礼物/心动信的发出和收到、互动最多的人、最常互动的时段、本季字数最多那场戏的开头几句。
心动信是匿名的，所以只报数量，不说是谁写的；对方用了自定义署名的短信，按署名算。
"""
import asyncio
import logging

from .db import get_config, get_conn
from .messaging import Send, push_to_role, push_to_role_ex

log = logging.getLogger(__name__)

PAUSE = 1.5           # 每人之间歇一下：连着往很多群发图容易被风控

# 最常互动的时段 → 称号（与「长日系统」存档站的 _TIME_TITLES 一致）
TIME_TITLES = [
    (0, 2, "零点主播", "深夜零点还在线，精神可嘉"),
    (2, 4, "破晓前哨", "凌晨不眠，最爱在无人的黑夜互动"),
    (4, 6, "黎明先锋", "天都没亮就开始活跃，比鸡起得早"),
    (6, 8, "晨曦使者", "清晨第一批上线，精力充沛"),
    (8, 10, "上午热线王", "上午就已经电话短信满天飞"),
    (10, 12, "阳光十点半", "上午十点的阳光和你一样活跃"),
    (12, 14, "午间话题人", "饭都不好好吃，忙着互动呢"),
    (14, 16, "下午茶常客", "下午茶时间最爱找人聊"),
    (16, 18, "傍晚漫步者", "放学放工后第一件事就是开始互动"),
    (18, 20, "黄昏浪漫派", "黄昏时分是你最爱发动攻势的时刻"),
    (20, 22, "夜间剧情家", "晚间黄金档，你的互动最密集"),
    (22, 24, "深夜电台长", "深夜还不睡，把私人群当电台在开"),
]


def enabled() -> bool:
    return get_config("season_report", "0") == "1"


def skin() -> str:
    return get_config("season_poster_skin", "antique")


def _excerpt_text(name: str, text: str) -> str:
    """戏文存的是「角色名：正文」，海报上名字单独一行，这里去掉开头的名字"""
    t = (text or "").strip()
    if name and t.startswith(name):
        t = t[len(name):].lstrip(" ：:　")
    return t[:60] + ("…" if len(t) > 60 else "")


def stats(role_id: int) -> dict:
    """一位玩家的本季数据，字段与旧存档站 /api/season_report 同名（海报直接用）。没有任何互动返回 {}。"""
    with get_conn() as c:
        me = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not me:
            return {}
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        d = {
            "role_name": me["name"],
            "sessions": q("SELECT COUNT(*) FROM room_members m JOIN rooms r ON r.id=m.room_id WHERE m.role_id=? AND r.status='ended'", role_id),
            "sms_sent": q("SELECT COUNT(*) FROM messages WHERE kind='sms' AND from_role_id=? AND status!='failed'", role_id),
            "sms_recv": q("SELECT COUNT(*) FROM messages WHERE kind='sms' AND to_role_id=? AND status='sent'", role_id),
            "gift_sent": q("SELECT COUNT(*) FROM messages WHERE kind='gift' AND from_role_id=? AND status!='failed'", role_id),
            "gift_recv": q("SELECT COUNT(*) FROM messages WHERE kind='gift' AND to_role_id=? AND status='sent'", role_id),
            "lovemail_sent": q("SELECT COUNT(*) FROM lovemails WHERE from_role_id=?", role_id),
            "lovemail_recv": q("SELECT COUNT(*) FROM lovemails WHERE to_role_id=? AND status='sent'", role_id),
            "letter_sent": 0, "letter_recv": 0,          # 寄信不留记录，算不出来
        }
        if not any(d[k] for k in ("sessions", "sms_sent", "sms_recv", "gift_sent", "gift_recv", "lovemail_sent", "lovemail_recv")):
            return {}
        for kind in ("sms", "gift"):
            sent = c.execute(f"SELECT r.name AS who, COUNT(*) AS n FROM messages m JOIN roles r ON r.id=m.to_role_id "
                             f"WHERE m.kind='{kind}' AND m.from_role_id=? AND m.status!='failed' GROUP BY m.to_role_id ORDER BY n DESC, r.id LIMIT 3", (role_id,)).fetchall()
            recv = c.execute(f"SELECT COALESCE(NULLIF(m.sign,''), r.name) AS who, COUNT(*) AS n FROM messages m JOIN roles r ON r.id=m.from_role_id "
                             f"WHERE m.kind='{kind}' AND m.to_role_id=? AND m.status='sent' GROUP BY who ORDER BY n DESC, who LIMIT 3", (role_id,)).fetchall()
            if sent:
                d[f"top_{kind}_sent_to"] = [[r["who"], r["n"]] for r in sent]
            if recv:
                d[f"top_{kind}_recv_from"] = [[r["who"], r["n"]] for r in recv]
        # 最常互动的时段：自己发的短信礼物、写的心动信、房间里的发言，按本机时间的钟点数
        hours = c.execute(
            "SELECT h, SUM(n) AS n FROM ("
            " SELECT CAST(strftime('%H', created_ts, 'unixepoch', 'localtime') AS INTEGER) AS h, COUNT(*) AS n FROM messages WHERE from_role_id=?1 AND status!='failed' GROUP BY h"
            " UNION ALL SELECT CAST(strftime('%H', created_ts, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) FROM lovemails WHERE from_role_id=?1 GROUP BY 1"
            " UNION ALL SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) FROM room_lines WHERE role_id=?1 GROUP BY 1"
            ") GROUP BY h ORDER BY n DESC, h LIMIT 1", (role_id,)).fetchone()
        if hours and hours["h"] is not None:
            for start, end, title, tagline in TIME_TITLES:
                if start <= hours["h"] < end:
                    d.update(peak_slot=f"{start:02d}:00–{end:02d}:00", time_title=title, time_tagline=tagline)
        # 名场面：自己戏文字数最多的那场（已结束的房间），取开头 4 句戏文；没有复盘记录就不画这块
        best = c.execute("SELECT l.room_id FROM room_lines l JOIN rooms r ON r.id=l.room_id AND r.status='ended' "
                         "WHERE l.role_id=? AND l.is_rp=1 AND r.kind!='微信' GROUP BY l.room_id ORDER BY SUM(l.chars) DESC, l.room_id LIMIT 1", (role_id,)).fetchone()
        if best:
            lines = c.execute("SELECT name, text FROM room_lines WHERE room_id=? AND is_rp=1 ORDER BY ts, id LIMIT 4", (best["room_id"],)).fetchall()
            ex = [{"role": ln["name"], "text": _excerpt_text(ln["name"], ln["text"])} for ln in lines]
            ex = [e for e in ex if e["text"]]
            if ex:
                d["best_excerpt"] = ex
    return d


def build(role_id: int, d: dict | None = None) -> str:
    """文字小结（出图失败时发这个）"""
    d = stats(role_id) if d is None else d
    if not d:
        return ""
    with get_conn() as c:
        top = c.execute(
            "SELECT r.name AS name, SUM(n) AS n FROM ("
            " SELECT to_role_id AS other, COUNT(*) AS n FROM messages WHERE from_role_id=?1 AND status!='failed' GROUP BY to_role_id"
            " UNION ALL SELECT from_role_id, COUNT(*) FROM messages WHERE to_role_id=?1 AND status='sent' AND sign='' GROUP BY from_role_id"   # 匿名的不算到真名头上
            " UNION ALL SELECT m2.role_id, COUNT(*) FROM room_members m1 JOIN room_members m2 ON m1.room_id=m2.room_id AND m2.role_id!=m1.role_id "
            "  JOIN rooms rm ON rm.id=m1.room_id AND rm.status='ended' WHERE m1.role_id=?1 GROUP BY m2.role_id"
            ") x JOIN roles r ON r.id=x.other GROUP BY x.other ORDER BY n DESC, r.id LIMIT 3", (role_id,)).fetchall()
    lines = [f"📖 {d['role_name']} 的本季小结", f"约会：{d['sessions']} 场", f"短信：发出 {d['sms_sent']} 条，收到 {d['sms_recv']} 条",
             f"礼物：送出 {d['gift_sent']} 份，收到 {d['gift_recv']} 份", f"心动信：写了 {d['lovemail_sent']} 封，收到 {d['lovemail_recv']} 封"]
    if top:
        lines.append("互动最多的人：" + "、".join(f"{t['name']}（{t['n']}）" for t in top))
    if d.get("time_title"):
        lines.append(f"最常互动的时间：{d['peak_slot']}，称号「{d['time_title']}」")
    return "\n".join(lines) + "\n谢谢你这一季的陪伴。"


def last_season_name() -> str:
    with get_conn() as c:
        r = c.execute("SELECT name FROM seasons WHERE status!='booked' ORDER BY COALESCE(ended_ts, 0) DESC, id DESC LIMIT 1").fetchone()
    return r["name"] if r else ""


async def _poster(d: dict, skin_id: str) -> bytes | None:
    from .render import season_poster
    try:
        return await asyncio.to_thread(season_poster.render, d, skin_id)
    except Exception as e:  # noqa: BLE001 画不出来就发文字
        log.warning("季末海报画不出来（%s），改发文字：%s", d.get("role_name"), e)
        return None


async def send_one(role_id: int, send: Send, send_image=None, show_name: str = "", skin_id: str = "", tail: str = "") -> bool | None:
    """给一位玩家发。没有互动返回 None；否则返回是否送达。
    send_image(bot_id, group_openid, data) 给了就发海报：先一句文字、再单独一张图；图发不出去补发文字小结。"""
    d = stats(role_id)
    if not d:
        return None
    summary = build(role_id, d) + (("\n\n" + tail) if tail else "")      # tail：结束季度时「收尾前存档」的提醒，并进同一条，少发一条消息
    png = await _poster(dict(d, show_name=show_name), skin_id or skin()) if send_image else None
    if not png:
        try:
            ok, _ = await push_to_role(role_id, summary, send, True)
        except Exception:  # noqa: BLE001
            ok = False
        return ok
    intro = (f"🎬 「{show_name}」已落幕\n" if show_name else "🎬 本季已落幕\n") + f"这是你「{d['role_name']}」的本季互动报告：" + (("\n\n" + tail) if tail else "")
    try:
        ok, gid, bot_id, _ = await push_to_role_ex(role_id, intro, send, True)
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        return False
    try:
        await send_image(bot_id, gid, png)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("季末海报发不出去（%s），改发文字：%s", d["role_name"], e)
    try:
        await send(bot_id, gid, summary)
    except Exception:  # noqa: BLE001
        return False
    return True


async def send_all(send: Send, send_image=None, tail: str = "") -> tuple[int, int]:
    """给每个有互动的玩家发。返回 (发出几份, 发不出去几份)。"""
    with get_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0 ORDER BY id")]
    show, skin_id = last_season_name(), skin()
    if send_image:
        from .render import season_poster
        await asyncio.to_thread(season_poster.prefetch, skin_id)      # 第一次用这款皮肤要从主页下载；下载不了就画纯色纸
    ok = bad = 0
    for rid in ids:
        done = await send_one(rid, send, send_image, show, skin_id, tail)
        if done is None:
            continue
        ok, bad = ok + int(done), bad + int(not done)
        if send_image and PAUSE:
            await asyncio.sleep(PAUSE)
    return ok, bad


# ---------- 后台下载海报 ----------

def live_players() -> list[dict]:
    """现在库里（本季还没收尾）有互动的玩家：[{id, name, data}]。"""
    with get_conn() as c:
        rows = c.execute("SELECT id, name FROM roles WHERE is_npc=0 ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = stats(r["id"])
        if d:
            out.append({"id": r["id"], "name": r["name"], "data": d})
    return out


def poster_bytes(data: dict, show_name: str, skin_id: str = "") -> bytes:
    from .render import season_poster
    season_poster.prefetch(skin_id or skin())
    return season_poster.render(dict(data, show_name=show_name), skin_id or skin())


def zip_posters(items: list[tuple[str, dict]], show_name: str, skin_id: str = "") -> bytes:
    """items = [(文件名不带后缀, 海报数据)] → zip。画不出来的那张跳过，zip 里放一个说明。"""
    import io
    import zipfile
    buf, bad = io.BytesIO(), []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:          # JPEG 本来就压过，不再压
        for name, data in items:
            try:
                z.writestr(f"{name}.jpg", poster_bytes(data, show_name, skin_id))
            except Exception as e:  # noqa: BLE001
                log.warning("海报画不出来（%s）：%s", name, e)
                bad.append(name)
        if bad:
            z.writestr("没画出来的.txt", "\n".join(bad))
    return buf.getvalue()
