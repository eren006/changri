"""季末报告：结束季度时，给每个玩家的个人群发一份本季互动小结（设置在「基础」里，默认关）。
内容：约会场次、短信/礼物/心动信的发出和收到，以及互动最多的 3 位对象。心动信是匿名的，所以只报数量，不说是谁写的。"""
from .db import get_config, get_conn
from .messaging import Send, push_to_role


def enabled() -> bool:
    return get_config("season_report", "0") == "1"


def build(role_id: int) -> str:
    with get_conn() as c:
        me = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not me:
            return ""
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        rooms_n = q("SELECT COUNT(*) FROM room_members m JOIN rooms r ON r.id=m.room_id WHERE m.role_id=? AND r.status='ended'", role_id)
        sms_out = q("SELECT COUNT(*) FROM messages WHERE kind='sms' AND from_role_id=? AND status!='failed'", role_id)
        sms_in = q("SELECT COUNT(*) FROM messages WHERE kind='sms' AND to_role_id=? AND status='sent'", role_id)
        gift_out = q("SELECT COUNT(*) FROM messages WHERE kind='gift' AND from_role_id=? AND status!='failed'", role_id)
        gift_in = q("SELECT COUNT(*) FROM messages WHERE kind='gift' AND to_role_id=? AND status='sent'", role_id)
        mail_out = q("SELECT COUNT(*) FROM lovemails WHERE from_role_id=?", role_id)
        mail_in = q("SELECT COUNT(*) FROM lovemails WHERE to_role_id=? AND status='sent'", role_id)
        top = c.execute(
            "SELECT r.name AS name, SUM(n) AS n FROM ("
            " SELECT to_role_id AS other, COUNT(*) AS n FROM messages WHERE from_role_id=?1 AND status!='failed' GROUP BY to_role_id"
            " UNION ALL SELECT from_role_id, COUNT(*) FROM messages WHERE to_role_id=?1 AND status='sent' GROUP BY from_role_id"
            " UNION ALL SELECT m2.role_id, COUNT(*) FROM room_members m1 JOIN room_members m2 ON m1.room_id=m2.room_id AND m2.role_id!=m1.role_id "
            "  JOIN rooms rm ON rm.id=m1.room_id AND rm.status='ended' WHERE m1.role_id=?1 GROUP BY m2.role_id"
            ") x JOIN roles r ON r.id=x.other GROUP BY x.other ORDER BY n DESC, r.id LIMIT 3", (role_id,)).fetchall()
    if not (rooms_n or sms_out or sms_in or gift_out or gift_in or mail_out or mail_in):
        return ""
    lines = [f"📖 {me['name']} 的本季小结", f"约会：{rooms_n} 场", f"短信：发出 {sms_out} 条，收到 {sms_in} 条",
             f"礼物：送出 {gift_out} 份，收到 {gift_in} 份", f"心动信：写了 {mail_out} 封，收到 {mail_in} 封"]
    if top:
        lines.append("互动最多的人：" + "、".join(f"{t['name']}（{t['n']}）" for t in top))
    return "\n".join(lines) + "\n谢谢你这一季的陪伴。"


async def send_all(send: Send) -> tuple[int, int]:
    """给每个有互动的玩家发。返回 (发出几份, 发不出去几份)。"""
    with get_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0 ORDER BY id")]
    ok = bad = 0
    for rid in ids:
        text = build(rid)
        if not text:
            continue
        try:
            done, _ = await push_to_role(rid, text, send, True)
        except Exception:  # noqa: BLE001
            done = False
        ok, bad = ok + int(done), bad + int(not done)
    return ok, bad
