"""每个机器人可以连多个 QQ 号（每个号一条连接，core/qqlink.py），它们的群放在同一个群号池里。只处理群号池里的群；池外的消息直接丢掉，池外的群不允许改名。
进程里一个事件循环：start_all 在启动时调用，网页要改群名时通过 loopbridge 回到主循环。"""
import asyncio
import logging
import re
import time

from . import qqpool
from .db import list_bot_ids, use_bot
from .qqlink import GroupMsg, QQLink, QQLinkError

log = logging.getLogger("qqmanager")

_links: dict[tuple[str, int], QQLink] = {}          # (机器人, QQ 号连接编号)
_tasks: dict[tuple[str, int], asyncio.Task] = {}
_stats: dict[tuple[str, int], dict] = {}
_listeners: list = []          # 上层挂钩：fn(app_id, GroupMsg)，只会收到池里的群的消息（下一步：进 log）


def add_listener(fn) -> None:
    _listeners.append(fn)


_notifiers: list = []          # 上层挂钩：fn(app_id, 文字)，指令执行结果往公告群发一条（QQ 号自己在群里一言不发）
_JOIN = re.compile(r"^[/.。]?\s*加入群号组\s+(\S{1,20})\s*$")
_LEAVE = re.compile(r"^[/.。]?\s*退出群号组\s*$")


def add_notifier(fn) -> None:
    _notifiers.append(fn)


def parse_command(text: str) -> tuple[str, str] | None:
    t = text.strip()
    m = _JOIN.match(t)
    if m:
        return "join", m.group(1)
    if _LEAVE.match(t):
        return "leave", ""
    return None


async def _run_command(app_id: str, m: GroupMsg, cmd: tuple[str, str], link_id: int = 0) -> None:
    """授权的 QQ 号在任何一个群里发「加入群号组 名字」/「退出群号组」。群里不回话，结果发到公告群（有的话）。"""
    kind, name = cmd
    err = qqpool.join_set(app_id, m.group_id, name, link_id) if kind == "join" else qqpool.leave_set(app_id, m.group_id)
    if kind == "join":
        text = f"✅ 群 {m.group_id} 已加入群号组「{name}」" if not err else f"❌ 群 {m.group_id} 没能加入「{name}」：{err}"
    else:
        text = f"✅ 群 {m.group_id} 已退出群号组" if not err else f"❌ 群 {m.group_id} 没能退出：{err}"
    log.info(f"[群号组] {text}")
    for fn in list(_notifiers):
        try:
            res = fn(app_id, text)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            log.exception("群号组通知出错")


def _make_handler(app_id: str, link_id: int = 0):
    async def on_msg(m: GroupMsg):
        with use_bot(app_id):
            owner = qqpool.owner_of(m.group_id)
            if owner and link_id and owner != link_id:        # 这个群归另一个 QQ 号管，两个号都在群里时只认归属的那个，不重复处理
                return
            cmd = parse_command(m.text)
            if cmd and m.user_id in qqpool.auth_qqs():          # 指令在哪个群发都行（群还不在池里也行），但只认授权的 QQ 号
                await _run_command(app_id, m, cmd, link_id)
                return
            if not qqpool.in_pool(m.group_id):
                return
            if link_id and not owner:
                qqpool.claim_owner(m.group_id, link_id)
            st = _stats.setdefault((app_id, link_id), {"count": 0, "last": 0})
            st["count"] += 1
            st["last"] = m.time
            for fn in list(_listeners):
                try:
                    res = fn(app_id, m)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    log.exception("监听挂钩出错")
    return on_msg


_join_listeners: list = []     # 上层挂钩：fn(app_id, 群号, QQ号)，池里的群有人进群时调用


def add_join_listener(fn) -> None:
    _join_listeners.append(fn)


def _make_join_handler(app_id: str, link_id: int = 0):
    async def on_join(gid: str, user_id: str):
        with use_bot(app_id):
            if not qqpool.in_pool(gid):
                return
            owner = qqpool.owner_of(gid)
        if owner and link_id and owner != link_id:
            return
        for fn in list(_join_listeners):
            try:
                res = fn(app_id, gid, user_id)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                log.exception("进群挂钩出错")
    return on_join


def _make_recall_handler(app_id: str):
    async def on_recall(gid: str, message_id: str):
        with use_bot(app_id):
            from . import roomlog
            roomlog.recall(message_id, gid)
    return on_recall


def _keys(app_id: str) -> list[tuple[str, int]]:
    return [k for k in _links if k[0] == app_id]


def start(app_id: str) -> str:
    """按这个机器人当前的设置启动所有已启用的 QQ 号连接（已在跑的先停掉重来）。返回空串表示成功。"""
    stop(app_id)
    with use_bot(app_id):
        qqpool.ensure_links()
        rows = [r for r in qqpool.links() if r["enabled"]]
    for r in rows:
        link = QQLink(r["url"], r["token"], on_group_message=_make_handler(app_id, r["id"]), on_group_join=_make_join_handler(app_id, r["id"]), on_group_recall=_make_recall_handler(app_id))
        _links[(app_id, r["id"])] = link
        _tasks[(app_id, r["id"])] = asyncio.get_running_loop().create_task(link.run())
    return ""


def stop(app_id: str) -> None:
    for k in _keys(app_id):
        stop_link(*k)


def stop_link(app_id: str, link_id: int) -> None:
    link = _links.pop((app_id, link_id), None)
    task = _tasks.pop((app_id, link_id), None)
    if link:
        link.stop()
    if task:
        task.cancel()
    _group_cache.pop((app_id, link_id), None)


async def restart(app_id: str) -> str:
    return start(app_id)


async def start_all() -> None:
    for app_id in list_bot_ids():
        try:
            start(app_id)
        except Exception:
            log.exception("启动 QQ 号连接失败")


def link_status(app_id: str, link_id: int) -> dict:
    link = _links.get((app_id, link_id))
    st = _stats.get((app_id, link_id), {})
    return {"running": link is not None, "connected": bool(link and link.connected), "online": link.online if link else None, "self_id": link.self_id if link else "",
            "error": link.last_error if link else "", "count": st.get("count", 0), "last": st.get("last", 0)}


def status(app_id: str) -> dict:
    """这个机器人所有 QQ 号连接合起来的状态：有一个连上就算连上。"""
    sts = [link_status(*k) for k in _keys(app_id)]
    if not sts:
        return link_status(app_id, -1)
    up = [x for x in sts if x["connected"]]
    first = (up or sts)[0]
    return {**first, "running": True, "connected": bool(up), "count": sum(x["count"] for x in sts), "last": max(x["last"] for x in sts)}


async def rename(app_id: str, gid: str, name: str) -> str:
    """改群名。只能改池里的群。先让群归属的那个 QQ 号改，它连不上再试别的号。返回空串表示成功，否则是原因。"""
    with use_bot(app_id):
        if not qqpool.in_pool(gid):
            return "这个群不在群号池里"
        owner = qqpool.owner_of(gid)
    order = sorted((k for k in _keys(app_id) if _links[k].connected), key=lambda k: k[1] != owner)
    if not order:
        return "QQ 号现在没有连上"
    err = ""
    for k in order:
        try:
            await _links[k].set_group_name(gid, name)
            return ""
        except QQLinkError as e:
            err = f"没改成：{e}（这个号要是群主或管理员才能改群名）"
    return err


async def room_rename(bot_id: str, group_row, name: str) -> None:
    """房间群改名（群号池里 qq: 开头的群）。改不成只记日志，不影响房间本身。"""
    from . import pool
    if not pool.is_qq_only(group_row):
        return
    err = await rename(bot_id, group_row["real_gid"], name)
    if err:
        log.warning(f"[房间群] 改名失败 {group_row['real_gid']} → {name}：{err}")


# ---------- 断线提醒 ----------
_down: dict[tuple[str, int], dict] = {}        # (app_id, link_id) -> {"since": 时间, "alerted": 是否已提醒过}


def down_reason(app_id: str, link_id: int = 0) -> str:
    """空串 = 正常（或没启用）；否则是掉线原因。"""
    st = link_status(app_id, link_id)
    if not st["running"]:
        return ""
    if not st["connected"]:
        return f"连不上这个号的 QQ 客户端（LLBot / NapCat / SnowLuma 等）：{st['error'] or '等待重连'}"
    if st["online"] is False:
        return "QQ 客户端还连着，但 QQ 号已经掉线，要重新扫码登录"
    return ""


def alerted(app_id: str, link_id: int = 0) -> bool:
    """这个号掉线、而且已经提醒过了（掉线超过提醒时间，还没恢复）。"""
    d = _down.get((app_id, link_id))
    return bool(d and d["alerted"])


def watch(app_id: str, now: float, alert_after: int = 120, link_id: int = 0) -> tuple[str, str] | None:
    """每隔几十秒调用一次（每个 QQ 号连接各自调）。掉线超过 alert_after 秒返回 ("alert", 原因)（只提醒一次）；恢复后返回 ("recover", "")。其余返回 None。"""
    key = (app_id, link_id)
    reason = down_reason(app_id, link_id)
    d = _down.get(key)
    if reason:
        if not d:
            d = _down[key] = {"since": now, "alerted": False}
        if not d["alerted"] and now - d["since"] >= alert_after:
            d["alerted"] = True
            return "alert", reason
        return None
    _down.pop(key, None)
    return ("recover", "") if d and d["alerted"] else None


_group_cache: dict[tuple[str, int], tuple[float, list]] = {}


async def group_list(app_id: str, link_id: int, max_age: int = 60) -> list[dict]:
    """这个 QQ 号现在所在的全部群（缓存 60 秒）。没连上抛 QQLinkError。"""
    key = (app_id, link_id)
    hit = _group_cache.get(key)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    link = _links.get(key)
    if not link or not link.connected:
        raise QQLinkError("QQ 号现在没有连上")
    groups = sorted(await link.group_list(), key=lambda g: str(g.get("group_name", "")))
    _group_cache[key] = (time.time(), groups)
    return groups


async def _member_rows(app_id: str, gid: str) -> list[dict] | None:
    """群成员名单（qqlink.member_rows 的格式）。群归属的号先问，问不到换别的号；都问不到返回 None。
    名单是空的也算问不到：QQ 号自己在群里，正常名单至少有它；空名单多半是号被踢了或接口出错，
    当成「群里没人」会把还有玩家的群放回池、分给下一场。"""
    with use_bot(app_id):
        owner = qqpool.owner_of(gid)
    for k in sorted((k for k in _keys(app_id) if _links[k].connected), key=lambda k: k[1] != owner):
        try:
            rows = await _links[k].member_rows(gid)
        except QQLinkError:
            continue
        except Exception as e:  # noqa: BLE001 连接刚断时 websockets 抛的不是 QQLinkError
            log.warning("查群 %s 成员出错：%s", gid, e)
            continue
        if rows:
            return rows
    return None


async def group_members(app_id: str, gid: str) -> set[str] | None:
    """这个群现在有哪些人（QQ 号）。问不到返回 None。"""
    rows = await _member_rows(app_id, gid)
    return None if rows is None else {r["qq"] for r in rows}


async def group_others(app_id: str, gid: str) -> set[str] | None:
    """房间群里「不该留着的人」：去掉群主、管理员（工作人员）和我们自己连着的 QQ 号，剩下的都算。
    不管有没有绑定过 QQ——没绑定的玩家也是人，群里还有他就不能分给下一场。
    只扮 NPC 的号（绑的 QQ 名下全是 NPC）不算，照老系统：NPC 留在群里不耽误下一场。问不到返回 None。"""
    rows = await _member_rows(app_id, gid)
    if rows is None:
        return None
    mine = {l.self_id for l in _links.values() if l.self_id}
    with use_bot(app_id):
        from . import roles
        npc_only = roles.npc_only_qqs()
    return {r["qq"] for r in rows if r["role"] not in ("owner", "admin") and r["qq"] not in mine and r["qq"] not in npc_only}


async def read_forward(app_id: str, gid: str, reply_id: str = "", forward_id: str = "") -> tuple[list[dict] | None, str]:
    """读合并转发（补录用）：群归属的号先试。返回 (消息列表, 出错原因)。"""
    with use_bot(app_id):
        owner = qqpool.owner_of(gid)
    err = "QQ 号现在没有连上"
    for k in sorted((k for k in _keys(app_id) if _links[k].connected), key=lambda k: k[1] != owner):
        try:
            return await _links[k].forward_of(reply_id, forward_id), ""
        except QQLinkError as e:
            err = str(e)
    return None, err


async def room_say(app_id: str, gid: str, text: str, at_qqs: list[str]) -> str:
    """QQ 号在房间群里发一条话并 @ 指定的人（只用于「人到齐了」这类房间提示）。池外的群不发。返回空串表示成功，否则是原因。"""
    with use_bot(app_id):
        if not qqpool.in_pool(gid):
            return "这个群不在群号池里"
        owner = qqpool.owner_of(gid)
    err = "QQ 号现在没有连上"
    for k in sorted((k for k in _keys(app_id) if _links[k].connected), key=lambda k: k[1] != owner):
        try:
            await _links[k].send_group_text(gid, text, at_qqs)
            return ""
        except QQLinkError as e:
            err = f"没发出去：{e}"
    return err
