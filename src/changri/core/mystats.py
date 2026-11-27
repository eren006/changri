"""本场统计（玩家自己发「本场统计」）：本季累计回复段数、字数、平均每段字数、平均回复耗时、最快回复，
以及短信、礼物、约会里互动最多的 3 位对象（收到的和发出的各一份）。"""
from . import arclen
from .db import get_conn


def _top(c, sql: str, args: tuple) -> str:
    rows = c.execute(sql, args).fetchall()
    return "\n".join(f"　{i}. {r['name']}（{r['n']}）" for i, r in enumerate(rows, 1))


def build(role_id: int) -> str:
    with get_conn() as c:
        me = c.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not me:
            return ""
        n_rp, chars = c.execute(                     # 微信不算字数（作者 2026-11-27）
            "SELECT COUNT(*), COALESCE(SUM(l.chars),0) FROM room_lines l JOIN rooms r ON r.id=l.room_id "
            "WHERE l.role_id=? AND l.is_rp=1 AND r.kind!='微信'", (role_id,)).fetchone()
        sec = []
        for kind, label, out_t, in_t in (("sms", "短信", "📤 你最喜欢发短信给：", "📨 最喜欢给你发短信："), ("gift", "礼物", "🎁 你最喜欢送礼给：", "🎀 最喜欢送你礼物：")):
            to = _top(c, "SELECT r.name, COUNT(*) AS n FROM messages m JOIN roles r ON r.id=m.to_role_id WHERE m.kind=? AND m.from_role_id=? AND m.status!='failed' GROUP BY m.to_role_id ORDER BY n DESC, r.id LIMIT 3", (kind, role_id))
            # 收到的按落款算：匿名、自定义署名、化名的写署名，不写真名（不然一看排行就知道是谁）
            fr = _top(c, "SELECT COALESCE(NULLIF(m.sign,''), r.name) AS name, COUNT(*) AS n FROM messages m JOIN roles r ON r.id=m.from_role_id "
                         "WHERE m.kind=? AND m.to_role_id=? AND m.status='sent' GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3", (kind, role_id))
            if to or fr:
                sec.append(f"【{label}】" + (f"\n{in_t}\n{fr}" if fr else "") + (f"\n{out_t}\n{to}" if to else ""))
        mine = _top(c, "SELECT r.name, COUNT(*) AS n FROM rooms x JOIN room_members m ON m.room_id=x.id JOIN roles r ON r.id=m.role_id "
                       "WHERE x.creator_id=? AND m.role_id!=? AND x.kind IN ('约会','电话') AND x.status!='cancelled' GROUP BY m.role_id ORDER BY n DESC, r.id LIMIT 3", (role_id, role_id))
        theirs = _top(c, "SELECT r.name, COUNT(*) AS n FROM rooms x JOIN room_members m ON m.room_id=x.id JOIN roles r ON r.id=x.creator_id "
                         "WHERE m.role_id=? AND x.creator_id!=? AND x.kind IN ('约会','电话') AND x.status!='cancelled' GROUP BY x.creator_id ORDER BY n DESC, r.id LIMIT 3", (role_id, role_id))
        # 心愿：发起人 = wishes.poster_id，摘走的人 = wishes.taker_id（只算已经被摘走的）
        took_mine = _top(c, "SELECT r.name, COUNT(*) AS n FROM wishes w JOIN roles r ON r.id=w.taker_id WHERE w.poster_id=? AND w.status='taken' GROUP BY w.taker_id ORDER BY n DESC, r.id LIMIT 3", (role_id,))
        i_took = _top(c, "SELECT r.name, COUNT(*) AS n FROM wishes w JOIN roles r ON r.id=w.poster_id WHERE w.taker_id=? AND w.status='taken' GROUP BY w.poster_id ORDER BY n DESC, r.id LIMIT 3", (role_id,))
        if mine or theirs:
            sec.append("【约会】" + (f"\n📅 最喜欢约你（约会/电话）：\n{theirs}" if theirs else "") + (f"\n💌 你最喜欢约（约会/电话）：\n{mine}" if mine else ""))
    if took_mine or i_took:
        sec.append("【心愿】" + (f"\n🌠 最喜欢摘你心愿：\n{took_mine}" if took_mine else "") + (f"\n✨ 你最喜欢摘谁的心愿：\n{i_took}" if i_took else ""))
    arc = next((p for p in arclen.stats()["people"] if p["id"] == role_id), None)
    if not (n_rp or arc or sec):
        return f"📊 【{me['name']}】暂无统计数据，快去参与邀约吧！"
    head = [f"📊 【{me['name']}】统计报告", "【本季累计】", f"🔹 累计回复：{n_rp} 段", f"🔹 累计字数：{chars} 字"]
    if n_rp:
        head.append(f"🔹 平均每段：{round(chars / n_rp)} 字")
    if arc:
        head += [f"🔹 平均耗时：{round(arc['avg'] * 60)} 分钟", f"🔹 极限速度：{max(1, round(arc['fastest'] * 60))} 分钟（最快）"]
    return "\n".join(head + sec + ["— 以上 —"])
