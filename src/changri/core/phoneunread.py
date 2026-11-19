"""小手机未读（照老系统 _phone_unread_updates）：会话列表顶上的「未读消息」卡片、每个会话的未读数、底栏角标、新消息弹窗都用这一份。
打开会话才算读过（只看会话列表不算）；按 core/phoneview 的会话归属算，匿名短信不会算到真名会话头上。"""
import time

from .db import get_conn


def mark_read(role_id: int, key: str, ts: int | None = None) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO phone_reads(role_id, key, read_ts) VALUES(?,?,?) ON CONFLICT(role_id, key) DO UPDATE SET read_ts=excluded.read_ts",
                  (role_id, key[:200], int(ts or time.time())))


def _reads(role_id: int) -> dict[str, int]:
    with get_conn() as c:
        return {r["key"]: r["read_ts"] for r in c.execute("SELECT key, read_ts FROM phone_reads WHERE role_id=?", (role_id,))}


def updates(role, song_on: bool = False, steal_on: bool = False, lovemail_on: bool = False, rel_on: bool = False) -> list[dict]:
    """[{key, name, label, count, ts, kind, target}]，新的在前。kind/target 给页面拼链接：
    r→角色id、s→署名、alias→匿名对话id、song、steal、lovemail、rel→对方名字。"""
    from . import aliases, gifts, phoneview
    rid = role["id"]
    reads = _reads(rid)
    by_id, by_name = phoneview._names()
    groups: dict[str, dict] = {}
    with get_conn() as c:
        from .messaging import NOT_RECEIVED_SQL
        rows = c.execute(f"SELECT * FROM messages WHERE to_role_id=? AND alias_id IS NULL AND error!='已撤回' AND {NOT_RECEIVED_SQL} ORDER BY id", (rid,)).fetchall()
    for m in rows:
        k = phoneview.key_of(m, rid, by_id, by_name)
        if not k:
            continue
        key = f"{k[0]}:{k[1]}"
        if m["created_ts"] <= reads.get(key, 0):
            continue
        g = groups.setdefault(key, {"key": key, "name": by_id.get(k[1], "?") if k[0] == "r" else k[1], "kinds": set(), "count": 0, "ts": 0,
                                    "kind": k[0], "target": k[1]})
        g["count"] += 1
        g["ts"] = max(g["ts"], m["created_ts"])
        g["kinds"].add(m["kind"])
    out = []
    for g in groups.values():
        kinds = g.pop("kinds")
        g["label"] = " / ".join(n for k, n in (("sms", "短信"), ("gift", "礼物")) if k in kinds)
        out.append(g)
    for t in aliases.threads(rid):
        key = f"a:{t['alias_id']}"
        with get_conn() as c:
            r = c.execute("SELECT COUNT(*) n, MAX(created_ts) ts FROM messages WHERE alias_id=? AND to_role_id=? AND created_ts>? AND error!='已撤回'",
                          (t["alias_id"], rid, reads.get(key, 0))).fetchone()
        if r["n"]:
            out.append({"key": key, "name": t["title"], "label": "匿名对话", "count": r["n"], "ts": r["ts"], "kind": "alias", "target": t["alias_id"]})
    if song_on:
        new = [x for x in phoneview.song_items(role) if not x["mine"] and x["ts"] > reads.get("song", 0)]
        if new:
            out.append({"key": "song", "name": phoneview.SONG_THREAD, "label": "点歌", "count": len(new), "ts": new[-1]["ts"], "kind": "song", "target": ""})
    torn = [x for x in phoneview.torn_items(rid) if x["ts"] > reads.get("torn", 0)]
    if torn:
        out.append({"key": "torn", "name": phoneview.TORN_THREAD, "label": "撕坏的短信", "count": len(torn), "ts": torn[-1]["ts"], "kind": "torn", "target": ""})
    if steal_on:
        with get_conn() as c:
            r = c.execute("SELECT COUNT(*) n, MAX(created_ts) ts FROM gift_steals WHERE victim_id=? AND created_ts>?", (rid, reads.get("steal", 0))).fetchone()
        if r["n"]:
            out.append({"key": "steal", "name": phoneview.STEAL_THREAD, "label": "有人动了你的图鉴", "count": r["n"], "ts": r["ts"], "kind": "steal", "target": ""})
    if lovemail_on:
        with get_conn() as c:
            r = c.execute("SELECT COUNT(*) n, MAX(sent_ts) ts FROM lovemails WHERE to_role_id=? AND status='sent' AND COALESCE(sent_ts,0)>?",
                          (rid, reads.get("lovemail", 0))).fetchone()
        if r["n"]:
            out.append({"key": "lovemail", "name": "匿名来信", "label": "心动信", "count": r["n"], "ts": r["ts"], "kind": "lovemail", "target": ""})
    if rel_on:
        from . import relations
        un = relations.unread(rid)
        if un:
            with get_conn() as c:
                for rel_id, v in un.items():
                    x = c.execute("SELECT r.*, o.name AS other FROM relations r JOIN roles o ON o.id = CASE WHEN r.role_lo=? THEN r.role_hi ELSE r.role_lo END "
                                  "WHERE r.id=?", (rid, rel_id)).fetchone()
                    last = c.execute("SELECT MAX(created_ts) FROM rel_items WHERE rel_id=?", (rel_id,)).fetchone()[0] or 0
                    last = max(last, c.execute("SELECT COALESCE(MAX(created_ts),0) FROM rel_details WHERE rel_id=?", (rel_id,)).fetchone()[0])
                    label = "、".join(n for n, k in (("关系线留言", "chat"), ("关系线补充", "detail")) if v[k])
                    out.append({"key": f"rel:{rel_id}", "name": x["other"], "label": label, "count": v["chat"] + v["detail"], "ts": last,
                                "kind": "rel", "target": x["other"]})
    from . import precall
    if precall.is_on():
        for x in precall.sessions_for(rid):
            n = x["unread"] or (1 if x["fresh"] else 0)
            if n:
                out.append({"key": f"pc:{x['id']}", "name": x["name"], "label": "前置电话" + ("" if x["unread"] else "（刚接通）"), "count": n,
                            "ts": x["ts"], "kind": "precall", "target": x["id"]})
    out.sort(key=lambda g: -g["ts"])
    for g in out:
        g["time"] = time.strftime("%m-%d %H:%M", time.localtime(g["ts"])) if g["ts"] else ""
    return out
