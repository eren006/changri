"""一个群同一时间只归一方：QQ 小号的群号组，或者官方机器人的备用群。

- pool_groups 里同一个群号可以有两行：小号那行（group_openid = qq:群号）和官方机器人那行（官方群编号，real_gid = 群号）。
- 两行都留着，moved = 1 的那行表示「现在归另一边」：不分给房间、小号不听，只记着组名 / 群编号，方便以后一键转回来。
- 转给官方机器人：管理员在群里发「设为备用群 群号」（第一次，官方机器人要靠这条消息认得这个群），以后在后台点「转到官方备用群」。
- 转回小号：后台点「转回小号群」（先查小号还在群里）。
官方机器人改不了群名，所以转过去之前群名必须是空闲名（「长日备用」这类），不然下一场的人会看到上一场的房间名。"""
import re

from . import pool, qqpool
from .db import get_conn

GID_RE = re.compile(r"\d{5,12}")
BUSY = "这个群{who}还在用（R{rid}），房间结束、人退光以后再转"


def alt_row(bot_id: str, gid: str):
    return pool.is_pool_group(bot_id, pool.QQ_PREFIX + gid)


def official_row(bot_id: str, gid: str):
    """这个群号登记成官方机器人备用群的那一行（没有返回 None）。"""
    with get_conn() as c:
        return c.execute("SELECT * FROM pool_groups WHERE bot_id=? AND real_gid=? AND group_openid NOT LIKE 'qq:%' "
                         "ORDER BY moved, id LIMIT 1", (bot_id, gid)).fetchone()


def owner(bot_id: str, gid: str) -> str:
    """'official' / 'qq' / ''（两边都没登记）。"""
    o = official_row(bot_id, gid)
    if o and not o["moved"] and o["in_pool"]:
        return "official"
    a = alt_row(bot_id, gid)
    if a and not a["moved"]:
        return "qq"
    return ""


def _busy(g, who: str) -> str:
    return BUSY.format(who=who, rid=g["room_id"] or "?") if g and g["state"] != "free" else ""


def _set_moved(pool_id: int, moved: bool) -> None:
    with get_conn() as c:
        c.execute("UPDATE pool_groups SET moved=? WHERE id=?", (int(moved), pool_id))


def check_to_official(bot_id: str, group_openid: str, gid: str) -> str:
    """转给官方机器人之前查一遍，返回空串表示可以转，否则是原因。"""
    if not GID_RE.fullmatch(gid or ""):
        return "群号是 5 到 12 位数字。用法：在这个群里发「设为备用群 本群群号」"
    other = qqpool.held_by_other_bots().get(gid)
    if other:
        return f"群 {gid} 已经绑在机器人「{other}」上了，先到「{other}」的后台把它移出"
    a = alt_row(bot_id, gid)
    if a and not a["moved"]:
        why = _busy(a, "在小号的群号组里")
        if why:
            return why
    o = pool.is_pool_group(bot_id, group_openid)
    if o:
        why = _busy(o, "")
        if why:
            return why
        if o["real_gid"] and o["real_gid"] != gid and o["in_pool"] and not o["moved"]:
            return f"这个群之前登记的群号是 {o['real_gid']}，跟 {gid} 对不上。群号填错了的话，先到「群池」页改好"
    return ""


def to_official(bot_id: str, group_openid: str, gid: str, set_name: str = "") -> tuple[bool, str]:
    """把群交给官方机器人：官方那行放进池，小号那行（有的话）标成 moved。返回 (成功, 原因)。
    调用前群名要已经改成空闲名（小号在线时由调用方先让小号改）。"""
    why = check_to_official(bot_id, group_openid, gid)
    if why:
        return False, why
    a = alt_row(bot_id, gid)
    if not set_name and a:
        set_name = a["set_name"]
    pid = pool.register(bot_id, group_openid, real_gid=gid, set_name=set_name)
    _set_moved(pid, False)
    ok, why = pool.set_in_pool(pid, True)
    if not ok:                                   # 个人群 / 公告群这类：register 已经顺手放进池了，撤回来
        with get_conn() as c:
            c.execute("UPDATE pool_groups SET in_pool=0 WHERE id=?", (pid,))
        return False, why
    if a and not a["moved"]:
        _set_moved(a["id"], True)
    return True, ""


def check_to_qq(bot_id: str, gid: str) -> str:
    a = alt_row(bot_id, gid)
    if not a:
        return "这个群没进过小号的群号组，不能转回小号。要给小号用，到「QQ 小号与群号组（双骰）」页把它加进群号组"
    if not a["moved"]:
        return "这个群现在就归小号管"
    o = official_row(bot_id, gid)
    if o and not o["moved"]:
        why = _busy(o, "官方机器人")
        if why:
            return why
    return ""


def to_qq(bot_id: str, gid: str) -> tuple[bool, str]:
    """把群交回小号：官方那行标成 moved（留着群编号），小号那行恢复。调用方要先确认小号在群里。"""
    why = check_to_qq(bot_id, gid)
    if why:
        return False, why
    o = official_row(bot_id, gid)
    if o and not o["moved"]:
        _set_moved(o["id"], True)
    _set_moved(alt_row(bot_id, gid)["id"], False)
    return True, ""


RENAME_ASK = "请群主马上把群名改成「{name}」这类不带房间信息的名字：以后由官方机器人管这个群，它改不了群名，不改的话下一场的人会看到之前的房间名。"
BACK_NOTICE = "这个群要交回 QQ 小号管了：请群主确认小号还在群里、并且是管理员（不然改不了群名、听不到大家说话）。"


# ---------- 一开始就是官方备用群、想交给小号：「转为小号群」 ----------

async def alt_link_in_group(bot_id: str, gid: str) -> tuple[int, str]:
    """哪个连着的小号在这个群里：返回 (QQ 号连接编号, "")；都不在返回 (0, 原因)。取群列表不走缓存，确认小号此刻真在群里。"""
    from . import qqmanager
    from .db import use_bot
    with use_bot(bot_id):
        links = [ln for ln in qqpool.links() if ln["enabled"]]
    if not links:
        return 0, "还没有连 QQ 小号：先在后台「QQ 小号与群号组（双骰）」页连一个小号"
    errs = []
    for ln in links:
        if not qqmanager.link_status(bot_id, ln["id"])["connected"]:
            continue
        try:
            groups = await qqmanager.group_list(bot_id, ln["id"], max_age=0)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e) or "取群列表失败")
            continue
        if any(str(g.get("group_id")) == gid for g in groups):
            return ln["id"], ""
    if errs:
        return 0, "取不到小号的群列表（" + "；".join(errs) + "）"
    return 0, f"小号不在群 {gid} 里（或者小号现在没连上）：先把小号拉进群、设为管理员，再发一次"


def check_new_qq(bot_id: str, group_openid: str) -> tuple[str, str]:
    """「转为小号群」之前查一遍。返回 (群号, 原因)；原因为空表示可以转。"""
    g = pool.is_pool_group(bot_id, group_openid)
    if not g or pool.is_qq_only(g) or not g["in_pool"] or g["moved"]:
        return "", "这个群不是官方机器人的备用群（先发「设为备用群 本群群号」）"
    gid = g["real_gid"]
    if not GID_RE.fullmatch(gid or ""):
        return "", "这个群还没登记群号：先发「设为备用群 本群群号」，或者到「群池」页填上群号"
    why = _busy(g, "官方机器人")
    if why:
        return gid, why
    other = qqpool.held_by_other_bots().get(gid)
    if other:
        return gid, f"群 {gid} 已经绑在机器人「{other}」上了，先到「{other}」的后台把它移出"
    return gid, ""


def to_new_qq(bot_id: str, group_openid: str, set_name: str, link_id: int) -> tuple[bool, str, str]:
    """把一开始就是官方备用群的群交给小号：官方那行标 moved（留着群编号，方便以后一键转回来），群加进小号的群号组。
    以前在小号群号组待过的，直接恢复那一行。返回 (成功, 原因, 用的组名)。"""
    gid, why = check_new_qq(bot_id, group_openid)
    if why:
        return False, why, ""
    g = pool.is_pool_group(bot_id, group_openid)
    a = alt_row(bot_id, gid)
    set_name = (set_name or (a["set_name"] if a else "") or g["set_name"] or "小号组").strip()[:20]
    _set_moved(g["id"], True)
    err = qqpool.join_set(bot_id, gid, set_name, link_id)
    if err:
        _set_moved(g["id"], False)                   # 没加成：还归官方机器人
        return False, err, set_name
    return True, "", set_name
