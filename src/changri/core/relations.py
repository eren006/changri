"""关系线：一对角色一条，双方轮流追加「细节」；发起人有额度，单条/总字数/段数有上限；可确认完成、可撤回自己发的细节。
规则和上限对齐长日系统的默认值（都能在设置平台改）。
"""
import time

from .db import get_config, get_conn, get_int


def enabled() -> bool:
    return get_config("rel_enabled", "1") != "0"


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def get_rel(a: int, b: int):
    lo, hi = _pair(a, b)
    with get_conn() as conn:
        return conn.execute("SELECT * FROM relations WHERE role_lo=? AND role_hi=?", (lo, hi)).fetchone()


def details(rel_id: int) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT d.*, r.name AS from_name FROM rel_details d JOIN roles r ON r.id=d.from_role_id "
            "WHERE d.rel_id=? ORDER BY d.id", (rel_id,)
        ).fetchall()


def _count_all(conn, role_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM relations WHERE role_lo=? OR role_hi=?", (role_id, role_id)).fetchone()[0]


def add_detail(from_id: int, to_id: int, to_name: str, text: str) -> tuple[bool, str]:
    """拉线：没有关系线就建立一条，然后追加一段细节。"""
    if not enabled():
        return False, "❌ 系统已关闭"
    if from_id == to_id:
        return False, "⚠️ 你不能跟自己建立关系线哦。"
    max_chars = get_int("max_detail_chars", 500)
    if len(text) > max_chars:
        return False, f"⚠️ 内容过长（{len(text)} 字），单条拉线上限为 {max_chars} 字，请精简后再提交。"
    lo, hi = _pair(from_id, to_id)
    now = int(time.time())
    with get_conn() as conn:
        rel = conn.execute("SELECT * FROM relations WHERE role_lo=? AND role_hi=?", (lo, hi)).fetchone()
        new = rel is None
        if new:
            max_rel = get_int("max_relationships_per_user", 20)
            started = conn.execute("SELECT COUNT(*) FROM relations WHERE initiator_id=?", (from_id,)).fetchone()[0]
            if started >= max_rel:
                return False, f"⚠️ 你的发起额度已达上限 ({max_rel})"
            max_total = get_int("max_relationships_total", 0)
            if max_total > 0:
                if _count_all(conn, from_id) >= max_total:
                    return False, f"⚠️ 你的关系线总数已达上限 ({max_total})，发起和收到的都算在内"
                if _count_all(conn, to_id) >= max_total:
                    return False, f"⚠️「{to_name}」的关系线总数已满 ({max_total})，暂时不能再向 ta 发起"
            rel_id = conn.execute(
                "INSERT INTO relations(role_lo, role_hi, initiator_id, created_ts) VALUES(?,?,?,?)", (lo, hi, from_id, now)
            ).lastrowid
            used_chars, used_count = 0, 0
        else:
            rel_id = rel["id"]
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(text)),0) FROM rel_details WHERE rel_id=?", (rel_id,)).fetchone()
            used_count, used_chars = row[0], row[1]
        max_count = get_int("max_detail_count", 20)
        if used_count >= max_count:
            return False, f"⚠️ 你们的关系线已达段数上限（{max_count} 段），无法继续添加。"
        max_total_chars = get_int("max_rel_total_chars", 3000)
        if used_chars + len(text) > max_total_chars:
            return False, f"⚠️ 添加后将超过总字数上限（{max_total_chars} 字，当前已有 {used_chars} 字），请精简内容。"
        conn.execute("INSERT INTO rel_details(rel_id, from_role_id, text, created_ts) VALUES(?,?,?,?)", (rel_id, from_id, text, now))
    hint = f"（本条 {len(text)} 字 · 累计 {used_chars + len(text)}/{max_total_chars} 字 · {used_count + 1}/{max_count} 段）"
    return True, ("✨ 关系线已建立。\n" if new else "") + hint


def confirm(a: int, b: int) -> bool:
    rel = get_rel(a, b)
    if not rel:
        return False
    with get_conn() as conn:
        conn.execute("UPDATE relations SET confirmed=1 WHERE id=?", (rel["id"],))
    return True


def withdraw(from_id: int, to_id: int, text: str):
    """撤回自己发的、内容完全一致的一段细节。返回被撤回的内容，找不到返回 None（关系线不存在返回 ''）。"""
    rel = get_rel(from_id, to_id)
    if not rel:
        return ""
    with get_conn() as conn:
        row = conn.execute("SELECT id, text FROM rel_details WHERE rel_id=? AND from_role_id=? AND text=? ORDER BY id LIMIT 1",
                           (rel["id"], from_id, text)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM rel_details WHERE id=?", (row["id"],))
    return row["text"]


def list_for(role_id: int) -> list[dict]:
    """某角色的所有关系线，带对方名字、段数、字数、是否轮到自己回。"""
    with get_conn() as conn:
        rels = conn.execute(
            "SELECT r.*, o.id AS other_id, o.name AS other_name FROM relations r "
            "JOIN roles o ON o.id = CASE WHEN r.role_lo=? THEN r.role_hi ELSE r.role_lo END "
            "WHERE r.role_lo=? OR r.role_hi=? ORDER BY r.id", (role_id, role_id, role_id)).fetchall()
        out = []
        for r in rels:
            n, chars = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(text)),0) FROM rel_details WHERE rel_id=?", (r["id"],)).fetchone()
            last = conn.execute("SELECT from_role_id FROM rel_details WHERE rel_id=? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            out.append({
                "rel_id": r["id"], "other_id": r["other_id"], "other_name": r["other_name"], "confirmed": bool(r["confirmed"]),
                "mine": r["initiator_id"] == role_id, "count": n, "chars": chars,
                "pending": (not r["confirmed"]) and n > 0 and last and last["from_role_id"] != role_id,
            })
        return out


def delete_rel(rel_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM relations WHERE id=?", (rel_id,))


def stats() -> list:
    """管理员视角：每个角色的关系线数量（多的在前）。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT r.name, COUNT(*) AS n FROM roles r JOIN relations x ON x.role_lo=r.id OR x.role_hi=r.id GROUP BY r.id ORDER BY n DESC, r.name"
        ).fetchall()


def alert_hours() -> float:
    """超过多少小时没回复就提醒；0 = 不提醒。"""
    try:
        return max(0.0, float(get_config("rel_alert_hours", "24") or 0))
    except ValueError:
        return 0.0


def waiting_lines(now: float | None = None) -> list[dict]:
    """所有进行中（没确认）、至少有一段细节的关系线：最后一段是谁发的、在等谁回、已经等了多久，
    以及这条线上「对方回复」平均隔多久。等得最久的在前。"""
    now = now or time.time()
    out = []
    with get_conn() as conn:
        for r in conn.execute("SELECT x.*, a.name AS a_name, b.name AS b_name FROM relations x "
                              "JOIN roles a ON a.id=x.role_lo JOIN roles b ON b.id=x.role_hi WHERE x.confirmed=0"):
            ds = conn.execute("SELECT from_role_id, created_ts FROM rel_details WHERE rel_id=? ORDER BY id", (r["id"],)).fetchall()
            if not ds:
                continue
            last = ds[-1]
            names = {r["role_lo"]: r["a_name"], r["role_hi"]: r["b_name"]}
            waiting_id = r["role_hi"] if last["from_role_id"] == r["role_lo"] else r["role_lo"]
            gaps = [ds[i]["created_ts"] - ds[i - 1]["created_ts"] for i in range(1, len(ds)) if ds[i]["from_role_id"] != ds[i - 1]["from_role_id"]]
            out.append({"rel_id": r["id"], "a_name": r["a_name"], "b_name": r["b_name"], "last_from": names[last["from_role_id"]],
                        "waiting_on": names[waiting_id], "last_ts": last["created_ts"], "hours": max(0.0, (now - last["created_ts"]) / 3600),
                        "avg_reply_hours": (sum(gaps) / len(gaps) / 3600) if gaps else None, "count": len(ds)})
    out.sort(key=lambda x: -x["hours"])
    return out


def overdue(now: float | None = None) -> list[dict]:
    """超过提醒时限还没人回的关系线。"""
    limit = alert_hours()
    return [x for x in waiting_lines(now) if limit > 0 and x["hours"] >= limit]


# ---------- 小手机用：规则、详情编辑、闲谈、时间点 ----------

CHAT_MAX = 200
TIME_LABEL_MAX = 20
TIME_TEXT_MAX = 300


def rule() -> dict:
    return {"max_rel": get_int("max_relationships_per_user", 20), "max_total_rel": get_int("max_relationships_total", 0),
            "max_count": get_int("max_detail_count", 20), "max_total": get_int("max_rel_total_chars", 3000), "max_chars": get_int("max_detail_chars", 500)}


def _own_rel(conn, rel_id: int, role_id: int):
    return conn.execute("SELECT * FROM relations WHERE id=? AND (role_lo=? OR role_hi=?)", (rel_id, role_id, role_id)).fetchone()


def other_of(rel, role_id: int) -> int:
    return rel["role_hi"] if rel["role_lo"] == role_id else rel["role_lo"]


def start(from_id: int, to_id: int, to_name: str):
    """没有关系线就建一条（先闲聊也算发起，占发起额度）。返回 (关系线行 or None, 提示)。"""
    if not enabled():
        return None, "关系线现在没有开放。"
    if from_id == to_id:
        return None, "不能跟自己建立关系线。"
    rel = get_rel(from_id, to_id)
    if rel:
        return rel, ""
    r = rule()
    lo, hi = _pair(from_id, to_id)
    with get_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM relations WHERE initiator_id=?", (from_id,)).fetchone()[0] >= r["max_rel"]:
            return None, f"你的发起额度已达上限（{r['max_rel']}）"
        if r["max_total_rel"] > 0:
            if _count_all(conn, from_id) >= r["max_total_rel"]:
                return None, f"你的关系线总数已达上限（{r['max_total_rel']}）"
            if _count_all(conn, to_id) >= r["max_total_rel"]:
                return None, f"「{to_name}」的关系线总数已满，暂时不能再向 ta 发起"
        conn.execute("INSERT INTO relations(role_lo, role_hi, initiator_id, created_ts) VALUES(?,?,?,?)", (lo, hi, from_id, int(time.time())))
    return get_rel(from_id, to_id), ""


def edit_detail(detail_id: int, role_id: int, text: str) -> tuple[bool, str]:
    text = text.strip()
    mx = get_int("max_detail_chars", 500)
    if not text or len(text) > mx:
        return False, f"内容要写，且不超过 {mx} 字。"
    with get_conn() as conn:
        d = conn.execute("SELECT * FROM rel_details WHERE id=? AND from_role_id=?", (detail_id, role_id)).fetchone()
        if not d:
            return False, "找不到这一段，或者不是你写的。"
        conn.execute("UPDATE rel_details SET text=? WHERE id=?", (text, detail_id))
    return True, "已修改"


def delete_detail(detail_id: int, role_id: int) -> tuple[bool, str, str, int]:
    """返回 (成功, 提示, 被删的内容, 对方 id)。"""
    with get_conn() as conn:
        d = conn.execute("SELECT d.*, r.role_lo, r.role_hi FROM rel_details d JOIN relations r ON r.id=d.rel_id WHERE d.id=? AND d.from_role_id=?", (detail_id, role_id)).fetchone()
        if not d:
            return False, "找不到这一段，或者不是你写的。", "", 0
        conn.execute("DELETE FROM rel_details WHERE id=?", (detail_id,))
    return True, "已删除", d["text"], d["role_hi"] if d["role_lo"] == role_id else d["role_lo"]


def items(rel_id: int, kind: str) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM rel_items WHERE rel_id=? AND kind=? ORDER BY id", (rel_id, kind))]


def add_item(from_id: int, to_id: int, to_name: str, kind: str, text: str, label: str = "") -> tuple[bool, str]:
    text, label = text.strip(), label.strip()
    mx = CHAT_MAX if kind == "chat" else TIME_TEXT_MAX
    if not text or len(text) > mx:
        return False, f"内容要写，且不超过 {mx} 字。"
    if kind == "time" and (not label or len(label) > TIME_LABEL_MAX):
        return False, f"时间要写，且不超过 {TIME_LABEL_MAX} 字。"
    rel, err = start(from_id, to_id, to_name)
    if not rel:
        return False, err
    with get_conn() as conn:
        conn.execute("INSERT INTO rel_items(rel_id, from_role_id, kind, label, text, created_ts) VALUES(?,?,?,?,?,?)", (rel["id"], from_id, kind, label if kind == "time" else "", text, int(time.time())))
    return True, "已发送" if kind == "chat" else "已记下"


def edit_item(item_id: int, role_id: int, text: str, label: str = "") -> tuple[bool, str, int]:
    """返回 (成功, 提示, 对方 id)。"""
    with get_conn() as conn:
        it = conn.execute("SELECT i.*, r.role_lo, r.role_hi FROM rel_items i JOIN relations r ON r.id=i.rel_id WHERE i.id=? AND i.from_role_id=?", (item_id, role_id)).fetchone()
        if not it:
            return False, "找不到这一条，或者不是你写的。", 0
        text, label = text.strip(), label.strip()
        mx = CHAT_MAX if it["kind"] == "chat" else TIME_TEXT_MAX
        if not text or len(text) > mx or (it["kind"] == "time" and (not label or len(label) > TIME_LABEL_MAX)):
            return False, "内容或时间的长度不对。", 0
        conn.execute("UPDATE rel_items SET text=?, label=?, edited_ts=? WHERE id=?", (text, label if it["kind"] == "time" else "", int(time.time()), item_id))
    return True, "已修改", it["role_hi"] if it["role_lo"] == role_id else it["role_lo"]


def delete_item(item_id: int, role_id: int) -> tuple[bool, str, int]:
    with get_conn() as conn:
        it = conn.execute("SELECT i.*, r.role_lo, r.role_hi FROM rel_items i JOIN relations r ON r.id=i.rel_id WHERE i.id=? AND i.from_role_id=?", (item_id, role_id)).fetchone()
        if not it:
            return False, "找不到这一条，或者不是你写的。", 0
        conn.execute("DELETE FROM rel_items WHERE id=?", (item_id,))
    return True, "已删除", it["role_hi"] if it["role_lo"] == role_id else it["role_lo"]


def counts(rel_id: int) -> dict:
    with get_conn() as conn:
        n, chars = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(text)),0) FROM rel_details WHERE rel_id=?", (rel_id,)).fetchone()
        chat = conn.execute("SELECT COUNT(*) FROM rel_items WHERE rel_id=? AND kind='chat'", (rel_id,)).fetchone()[0]
        tm = conn.execute("SELECT COUNT(*) FROM rel_items WHERE rel_id=? AND kind='time'", (rel_id,)).fetchone()[0]
        last = conn.execute("SELECT text, from_role_id FROM rel_items WHERE rel_id=? AND kind='chat' ORDER BY id DESC LIMIT 1", (rel_id,)).fetchone()
    return {"n_detail": n, "n_chars": chars, "n_chat": chat, "n_time": tm, "last_chat": last["text"][:30] if last else "", "last_chat_from": last["from_role_id"] if last else 0}


def unread(role_id: int) -> dict[int, dict]:
    """{关系线 id: {"chat": 对方新闲谈数, "detail": 对方新细节数}}，只列有未读的。"""
    out: dict[int, dict] = {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT r.id AS rid, "
            "(SELECT COUNT(*) FROM rel_items i WHERE i.rel_id=r.id AND i.kind='chat' AND i.from_role_id!=? AND i.id>COALESCE(s.chat_seen,0)) AS c, "
            "(SELECT COUNT(*) FROM rel_details d WHERE d.rel_id=r.id AND d.from_role_id!=? AND d.id>COALESCE(s.detail_seen,0)) AS d "
            "FROM relations r LEFT JOIN rel_seen s ON s.rel_id=r.id AND s.role_id=? WHERE r.role_lo=? OR r.role_hi=?",
            (role_id, role_id, role_id, role_id, role_id)).fetchall()
    for r in rows:
        if r["c"] or r["d"]:
            out[r["rid"]] = {"chat": r["c"], "detail": r["d"]}
    return out


def unread_total(role_id: int) -> int:
    return sum(v["chat"] + v["detail"] for v in unread(role_id).values())


def mark_seen(rel_id: int, role_id: int, kind: str) -> None:
    """kind: chat / detail，把这条线里该类内容标成已读到最新。"""
    if kind not in ("chat", "detail"):
        return
    with get_conn() as conn:
        if kind == "chat":
            top = conn.execute("SELECT COALESCE(MAX(id),0) FROM rel_items WHERE rel_id=? AND kind='chat'", (rel_id,)).fetchone()[0]
        else:
            top = conn.execute("SELECT COALESCE(MAX(id),0) FROM rel_details WHERE rel_id=?", (rel_id,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO rel_seen(rel_id, role_id) VALUES(?,?)", (rel_id, role_id))
        conn.execute(f"UPDATE rel_seen SET {kind}_seen=? WHERE rel_id=? AND role_id=?", (top, rel_id, role_id))
