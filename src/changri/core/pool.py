"""群池：机器人被拉进的群。约会房间从池里领空群，结束后先「待清场」，管理员确认群里的人都清走了再放回池。
官方机器人读不到真实群号、踢不了人，所以群号要管理员在设置平台填，清场也要人工确认。"""
import time

from .db import get_conn, get_config


QQ_PREFIX = "qq:"          # 只有群号、卡拉不在里面的群（QQ 号监听那条线管着）：group_openid 用 qq:群号 占位


def is_qq_only(g) -> bool:
    return str(g["group_openid"]).startswith(QQ_PREFIX)


def register_qq(bot_id: str, gid: str, set_name: str = "", label: str = "") -> int:
    """QQ 号那条线上的群直接放进房间群池：不需要卡拉进群，机器人在里面一言不发，只靠 QQ 号改群名。"""
    return register(bot_id, QQ_PREFIX + gid, real_gid=gid, set_name=set_name, label=label)


def note_group(bot_id: str, group_openid: str) -> bool:
    """机器人被拉进群时登记一下（已有则不动）。返回是否新登记。"""
    with get_conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO pool_groups(bot_id, group_openid, added_ts) VALUES(?,?,?)",
                        (bot_id, group_openid, int(time.time())))
        return cur.rowcount > 0


def register(bot_id: str, group_openid: str, real_gid: str = "", set_name: str = "", label: str = "") -> int:
    """直接放进池（管理员在群里发「设为群池」）。"""
    note_group(bot_id, group_openid)
    with get_conn() as c:
        c.execute("UPDATE pool_groups SET in_pool=1, real_gid=CASE WHEN ?!='' THEN ? ELSE real_gid END, "
                  "set_name=CASE WHEN ?!='' THEN ? ELSE set_name END, label=CASE WHEN ?!='' THEN ? ELSE label END "
                  "WHERE bot_id=? AND group_openid=?", (real_gid, real_gid, set_name, set_name, label, label, bot_id, group_openid))
        return c.execute("SELECT id FROM pool_groups WHERE bot_id=? AND group_openid=?", (bot_id, group_openid)).fetchone()["id"]


def is_pool_group(bot_id: str, group_openid: str):
    with get_conn() as c:
        return c.execute("SELECT * FROM pool_groups WHERE bot_id=? AND group_openid=?", (bot_id, group_openid)).fetchone()


def get(pool_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM pool_groups WHERE id=?", (pool_id,)).fetchone()


def all_groups() -> list:
    """带上「已绑成个人群/戏群/公告群」的标记，避免把玩家的个人群当成房间用。"""
    with get_conn() as c:
        rows = c.execute("SELECT * FROM pool_groups ORDER BY set_name, id").fetchall()
        bound = {(r["bot_id"], r["group_openid"]): r["name"] for r in c.execute(
            "SELECT g.bot_id, g.group_openid, r.name FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.group_openid IS NOT NULL")}
    special = {get_config(k, ""): label for k, label in (
        ("water_group", "水群"), ("backstage_group", "后台群"), ("announce_group", "公告群"), ("stage_group", "戏群"))}
    special.pop("", None)
    out = []
    for r in rows:
        d = dict(r)
        key = f"{r['bot_id']}|{r['group_openid']}"
        d["owner"] = bound.get((r["bot_id"], r["group_openid"]), "")
        d["conflict"] = "个人群" if (r["bot_id"], r["group_openid"]) in bound else special.get(key, "")
        out.append(d)
    return out


def update(pool_id: int, real_gid: str, label: str, set_name: str) -> None:
    with get_conn() as c:
        c.execute("UPDATE pool_groups SET real_gid=?, label=?, set_name=? WHERE id=?", (real_gid.strip(), label.strip(), set_name.strip(), pool_id))


def set_in_pool(pool_id: int, on: bool) -> tuple[bool, str]:
    g = get(pool_id)
    if not g:
        return False, "没有这个群"
    if on:
        d = [x for x in all_groups() if x["id"] == pool_id][0]
        if d["conflict"]:
            return False, f"这个群已经是{d['conflict']}，不能放进群池"
    elif g["state"] != "free":
        return False, "这个群正在使用或等待清场，先处理完再移出"
    with get_conn() as c:
        c.execute("UPDATE pool_groups SET in_pool=? WHERE id=?", (int(on), pool_id))
    return True, ""


def set_set(set_name: str, on: bool) -> tuple[int, int]:
    """开启/关闭一整个群号组。返回 (成功数, 跳过数)。"""
    ok = skip = 0
    with get_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM pool_groups WHERE set_name=?", (set_name,))]
    for i in ids:
        done, _ = set_in_pool(i, on)
        ok, skip = ok + done, skip + (not done)
    return ok, skip


def delete(pool_id: int) -> tuple[bool, str]:
    g = get(pool_id)
    if g and g["state"] != "free":
        return False, "这个群正在使用或等待清场"
    with get_conn() as c:
        c.execute("DELETE FROM pool_groups WHERE id=?", (pool_id,))
    return True, ""


def link_ready(g) -> bool:
    """QQ 号监听那条线上的群，只有它归属的那个 QQ 号现在连着、登录着才能分给房间。
    群没有归属的号（还没人认领）时，这个机器人有任何一个号连着就行。官方群（卡拉在里面的）不看这个。"""
    if not is_qq_only(g):
        return True
    from . import qqmanager, qqpool
    from .db import use_bot
    bot = g["bot_id"]
    with use_bot(bot):
        owner = qqpool.owner_of(g["real_gid"])
        links = [x["id"] for x in qqpool.links()]
    if not links:
        return True                                  # 一个 QQ 号连接都没登记过（老用法）：不限制
    ids = [owner] if owner and owner in links else ([] if owner else links)
    for lid in ids:
        st = qqmanager.link_status(bot, lid)
        if st["connected"] and st["online"] is not False:
            return True
    return False


def allocate(room_id: int):
    """领一个空群给房间。池里没有空群返回 None（调用方会撤掉这个房间，不排队）。
    QQ 号没登录（没连上）的群不分：登记在哪个 QQ 号名下，就跟着那个号走。"""
    with get_conn() as c:
        g = next((x for x in c.execute("SELECT * FROM pool_groups WHERE in_pool=1 AND state='free' ORDER BY RANDOM()") if link_ready(x)), None)
        if not g:
            return None
        c.execute("UPDATE pool_groups SET state='occupied', room_id=? WHERE id=?", (room_id, g["id"]))
        c.execute("UPDATE rooms SET pool_id=? WHERE id=?", (g["id"], room_id))
        return g


def release(pool_id: int, to: str = "cleaning") -> None:
    with get_conn() as c:
        c.execute("UPDATE pool_groups SET state=?, room_id=NULL WHERE id=?", (to, pool_id))


def counts() -> dict:
    with get_conn() as c:
        r = {k: 0 for k in ("free", "occupied", "cleaning")}
        for row in c.execute("SELECT state, COUNT(*) n FROM pool_groups WHERE in_pool=1 GROUP BY state"):
            r[row["state"]] = row["n"]
        r["waiting"] = c.execute("SELECT COUNT(*) FROM rooms WHERE status='open' AND pool_id IS NULL").fetchone()[0]
        return r


def player_qqs() -> set[str]:
    """所有登记过的玩家 QQ 号（恋综绑定 + 跑团身份）。靠它判断一个群里有没有玩家。"""
    with get_conn() as c:
        out = {r["qq"] for r in c.execute("SELECT qq FROM person_qq")}
        out |= {r["member_openid"][3:] for r in c.execute("SELECT member_openid FROM trpg_identities WHERE member_openid LIKE 'qq:%'")}
    return out


def room_qqs(room_id: int) -> set[str]:
    """这个房间自己的参与者的 QQ 号（他们进群是应该的）。"""
    with get_conn() as c:
        return {r["qq"] for r in c.execute(
            "SELECT q.qq FROM room_members m JOIN roles r ON r.id=m.role_id JOIN person_qq q ON q.member_openid=r.member_openid WHERE m.room_id=?", (room_id,))}


def reject(pool_id: int, room_id: int, to: str = "cleaning") -> None:
    """分到的群里还有别的玩家：群先放一边（等人退群），房间退回未分群的状态，再领一个；领不到由调用方撤掉房间。"""
    with get_conn() as c:
        c.execute("UPDATE pool_groups SET state=?, room_id=NULL WHERE id=?", (to, pool_id))
        c.execute("UPDATE rooms SET pool_id=NULL WHERE id=?", (room_id,))
