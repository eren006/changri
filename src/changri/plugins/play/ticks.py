"""定时任务（过期结束、补群、提前排的官约到点开房……）和 QQ 小号旁听到的房间指令。"""

import re

import nonebot
from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent

from changri.core import announce, messaging, pool, qqmanager, roles, rooms, storycal
from changri.core.db import per_bot
from changri.core.overview import slot_text as _slot
from changri.core.qqsend import make_send

from .common import NEED_ROLE, _announce_in_room, _cmd, _me, _missed_line, _push, _push_group, _qq_hint, _to_group
from .finish import _ensure_clean, _finish_room, _room_arg, _sweep_cleaning, _warn_expiring
from .launch import _send_official_invites
from .timeline import _decline, _propose_in_room, _vote_result

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


# ================= 定时：过期结束、给还没分到群的房间补群 =================

@scheduler.scheduled_job("interval", seconds=30, id="rooms_tick", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    if not nonebot.get_bots():
        return
    # 每一步单独接住错误：一步出错（比如某个群查成员时连接断了）不能让后面补群、查到场整轮都跳过
    for step in (_tick_outbox, _warn_expiring, _tick_expired, _sweep_cleaning, _tick_deferred, _tick_assign, _check_presence, _tick_pool_alert):
        try:
            await step()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[房间] 定时任务 {step.__name__} 出错：{e}")


async def _tick_pool_alert() -> None:
    """有房间因为没有空群没开成：提醒后台群（最多 30 分钟一条）。没设后台群就不发，不退回公告群。"""
    staff = announce.get_backstage()
    if not staff:
        return
    hit = pool.take_exhausted()
    if not hit:
        return
    c = pool.counts()
    await _to_group(staff[0], staff[1],
                    f"⚠️ 备用群池用完了：最近有 {hit['n']} 次发起没开成（最后一次是{hit['kind']}），玩家已收到「没能开成」。\n"
                    f"现在：空闲 {c['free']}，占用中 {c['occupied']}，等人退群 {c['cleaning']}。"
                    "可以把退光人的群放回池（「放回备用群」或后台「群池」页），或者多准备几个备用群。"
                    + ("\n（显示有空闲却还是没分到：多半是管这些群的 QQ 小号掉线了。）" if c["free"] else ""))


async def _tick_outbox() -> None:
    from changri.core import specials
    for row in specials.take_outbox():                    # 窃听器 / 截信器 / 回音壁抄到的、捕鼠器的提醒
        await messaging.push_to_role(row["owner_id"], row["text"], make_send())


async def _tick_expired() -> None:
    for r in rooms.expired():
        await _finish_room(r["id"], "ended", "", timeout=True)


async def _tick_assign() -> None:
    """有空群了，给排队的房间补群。分到的群不干净又换不到：房间留着接着排队（不删、不通知）。"""
    for rid in rooms.assign_waiting():
        if not await _ensure_clean(rid, drop=False):
            continue
        room = rooms.get(rid)
        if not room or not room["pool_id"]:
            continue
        if rooms.is_official(room):                    # 官约 / 官电：玩家还没收到过群号，发完整的邀请
            await _report_official_missed(room, await _send_official_invites(rid))
            continue
        for m in rooms.members(rid):
            await _push(m["role_id"], f"🔔 R{rid} {room['kind']}有空群了。\n{_slot(room)}" + _qq_hint(m["role_id"], room))
            await _push_group(m["role_id"], room)
        await _announce_in_room(rid)


_defer_waiting_told: set[tuple[str, int]] = set()


async def _report_official_missed(room, missed: list) -> None:
    """到那天才开房的官约：管理员不在场，没发到邀请的人告诉后台群（没设后台群就不发）。"""
    staff = announce.get_backstage()
    if missed and staff:
        await _to_group(staff[0], staff[1], f"📮 R{room['id']} {'官电' if room['kind'] == '电话' else '官约'}（{_slot(room)}）已开房、发了邀请。\n{_missed_line(missed)}")


async def _tick_deferred() -> None:
    """提前排的官约到那天了：领群、发邀请。领不到群就等下一轮，后台群只提醒一次。"""
    from changri.core.db import current_bot
    for r in rooms.deferred_due():
        if not rooms.activate(r["id"]):
            key = (current_bot(), r["id"])
            staff = announce.get_backstage()
            if key not in _defer_waiting_told and staff:
                _defer_waiting_told.add(key)
                await _to_group(staff[0], staff[1], f"⚠️ R{r['id']} 官约（{_slot(r)}）今天该开房了，但备用群池没有空群，还在等。"
                                                    "放回或添加备用群后会自动分群、发邀请。")
            continue
        if not await _ensure_clean(r["id"], drop=False):                # 分到的小号群里有外人：退回等群，_tick_assign 分到后照样发完整邀请
            continue
        await _report_official_missed(r, await _send_official_invites(r["id"]))


async def _receipt_all_here(room) -> None:
    """人到齐了。房间群（只有群号）：由监听的 QQ 号在群里 @ 所有参与者，说清楚是什么、几点、在哪；
    发不出去（QQ 号掉线、没权限）才退回发到每个人自己的群里。"""
    from changri.core.db import current_bot
    gi = rooms.group_info(room)
    mem = rooms.members(room["id"])
    if gi and gi[1].startswith(pool.QQ_PREFIX):
        qqs = [q for m in mem for q in roles.qqs_of_role(m["role_id"])[:1]]
        names = "、".join(m["name"] for m in mem)
        text = (f"✅ 人都到齐了：R{room['id']} {room['kind']}\n时间地点：{_slot(room)}\n参与者：{names}\n"
                f"聊完了，直接在这个群里发「结束{room['kind']}」就行。")
        err = await qqmanager.room_say(current_bot(), gi[1][len(pool.QQ_PREFIX):], text, qqs)
        if not err:
            return
        logger.warning(f"[房间] R{room['id']} 群里发人齐提示失败，改发各人自己的群：{err}")
    elif gi:                                          # 官方机器人的房间群：由它在群里说（要群主开了「主动在群聊内发言」）
        names = "、".join(m["name"] for m in mem)
        text = (f"✅ 人都到齐了：R{room['id']} {room['kind']}\n时间地点：{_slot(room)}\n参与者：{names}\n"
                f"聊完了，直接在这个群里发「结束{room['kind']}」就行。")
        if await _to_group(gi[0], gi[1], text):
            return
        logger.warning(f"[房间] R{room['id']} 官方机器人在群里发人齐提示失败，改发各人自己的群")
    for m in mem:
        await _push(m["role_id"], f"✅ R{room['id']} {room['kind']}：人都到齐了。\n{_slot(room)}\n聊完了发「结束{room['kind']} R{room['id']}」。")


async def _check_presence(only_gid: str = "") -> None:
    """房间群（只有群号的群）：问 QQ 号群里现在有谁，认过 QQ 的参与者都在群里就算到齐，不用等发言。
    有人进群时马上查那一个群（only_gid）；定时任务每 30 秒再把所有房间扫一遍兜底。"""
    from changri.core.db import current_bot
    bot_id = current_bot()
    for room in rooms.open_rooms():
        gi = rooms.group_info(room)
        if not gi or not gi[1].startswith(pool.QQ_PREFIX) or all(m["entered_ts"] for m in rooms.members(room["id"])):
            continue
        if only_gid and gi[1] != pool.QQ_PREFIX + only_gid:
            continue
        present = await qqmanager.group_members(bot_id, gi[1][len(pool.QQ_PREFIX):])
        res = rooms.note_presence(room, present) if present else None
        if res and res["complete"]:
            await _receipt_all_here(rooms.get(room["id"]))


_ROOM_CMD = re.compile(r"[/.。]?(结束(?:约会|私约|电话|官约|微信|心愿|前置电话|踩点)|修改时间线|同意时间线|拒绝时间线)\s*(.*)")
_LEAD_AT = re.compile(r"^(?:@\S+\s*)+")          # QQ 里「回复」一条消息会自动在开头 @ 原发送人


def _cmd_text(text: str) -> str:
    return _LEAD_AT.sub("", (text or "").strip()).strip()


async def _listen_cmd(app_id, msg) -> bool:
    """房间群里没有卡拉：有监听的 QQ 号时，群里发「结束约会 / 修改时间线 / 同意时间线 / 拒绝时间线」由 QQ 号听到，
    按发言人绑定的 QQ 认出他是房间的哪位参与者，照常处理，结果由 QQ 号在群里回。返回是否是房间指令。"""
    m = _ROOM_CMD.fullmatch(_cmd_text(msg.text))
    if not m:
        return False
    room = rooms.room_of_group(app_id, pool.QQ_PREFIX + msg.group_id)
    if not room:
        return False
    cmd, arg = m.group(1), m.group(2).strip()
    rid = _room_arg(arg) if re.fullmatch(r"[Rr]\d+", arg) else None
    if rid and rid != room["id"]:
        return False
    say = lambda t: _to_group(app_id, pool.QQ_PREFIX + msg.group_id, t)       # noqa: E731
    mine = {r["id"] for r in roles.roles_by_qq(msg.user_id)}
    me = next((roles.get_role(x["role_id"]) for x in rooms.members(room["id"]) if x["role_id"] in mine), None)
    if not me:
        await say("认不出你是谁：请先在自己的群里对卡拉发「/绑定QQ 你的QQ号」，绑定后才能在这里用。")
        return True
    if cmd.startswith("结束"):
        await _finish_room(room["id"], "ended", "")
    elif cmd == "修改时间线":
        p = arg.split()
        day = room["day"]
        if p and re.fullmatch(r"[Dd]\d+", p[0]):
            day = int(p.pop(0)[1:])
        hm = rooms.parse_hm(p[0], day) if p else None
        if not hm:
            await say(f"用法：修改时间线 [D几] 时间\n例：修改时间线 D1 {storycal.example_time()}")
            return True
        ok, why = rooms.propose_change(room["id"], me["id"], day, *hm)
        others = [x for x in rooms.members(room["id"]) if x["role_id"] != me["id"]]
        if ok and not others:                                     # 只有自己（踩点）：直接生效
            await say(await _vote_result(room, me, True, True))
        else:
            await say(_propose_in_room(me, room, others, day, hm) if ok else f"❌ {why}")
    elif cmd == "同意时间线":
        await say(await _vote_result(room, me, True, True))
    else:
        await say(await _decline(room, me, True))
    return True


async def _on_listened(app_id, msg) -> None:
    """QQ 号听到房间群里有人说话 = 到场（群里没有卡拉，靠这个补上）；最后一个人到了，私下回执给每个参与者。"""
    try:
        from changri.core import roomlog
        roomlog.record(app_id, msg)                                  # 复盘：房间群里的发言存下来（补录只由卡拉做）
        if await _listen_cmd(app_id, msg):
            return
        res = rooms.note_qq_speech(app_id, msg.group_id, msg.user_id)
        if res and res["complete"]:
            await _receipt_all_here(rooms.get(res["room_id"]))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 记录到场出错：{e}")


async def _on_join(app_id, gid, user_id) -> None:
    """QQ 号看到有人进了房间群：马上查一次谁到了。"""
    try:
        from changri.core.db import use_bot
        with use_bot(app_id):
            await _check_presence(gid)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 进群后查到场出错：{e}")


if _on_join not in qqmanager._join_listeners:
    qqmanager.add_join_listener(_on_join)

if _on_listened not in qqmanager._listeners:
    qqmanager.add_listener(_on_listened)


my_stats = _cmd("本场统计", {"我的统计"})


@my_stats.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    from changri.core import mystats
    me = _me(bot, event)
    if not me:
        await my_stats.finish(NEED_ROLE)
    await my_stats.finish(mystats.build(me["id"]))
