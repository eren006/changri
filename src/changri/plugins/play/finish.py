"""房间收尾：结束、强结、取消官约、到点提醒、清群、复盘、补录。"""

from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import announce, pool, qqmanager, qqpool, roles, rooms, season, wishes
from changri.core.buttons import Btn
from changri.core.db import get_config, get_conn, get_int
from changri.core.overview import slot_text as _slot
from changri.core.qqsend import reply_buttons, reply_long
from changri.core.textutil import hm_text, parse_id

from .common import NEED_ROLE, NOT_ADMIN, _cmd, _is_admin, _me, _push, _to_group


async def _finish_room(room_id: int, status: str, why: str, timeout: bool = False) -> None:
    """timeout=True：「到时间强制结群」到点了（文案不一样：结戏限时已到）。
    房间已经不在了或者已经结束（小手机和群里同时点结束、定时任务刚好到点）：什么也不做，不重复通知、不重复发奖励。"""
    room = rooms.get(room_id)
    if not room or room["status"] != "open":
        return
    mem = rooms.members(room_id)
    gi = rooms.group_info(room)
    rooms.end(room_id, status)
    verb = "已经结束" if status == "ended" else "已经取消"
    if status != "ended":                                                # 正常结束不再往个人群发通知；取消、超时这类参与者不一定在场，才通知
        for m in mem:
            await _push(m["role_id"], f"🔚 R{room_id} {room['kind']}{verb}{why}。" + ("请退出那个群，管理员清场后才能再给别人用。" if gi else ""))
    if status == "ended" and room["kind"] != "微信":                     # 微信不复盘（照老系统：微信群不走存档 / 字数那一套），不发复盘图也不发统计回执
        from changri.core import roomlog
        from changri.core.qqsend import IMG
        if get_config("room_auto_review", "0") == "1":                   # 自动复盘开着：把本场复盘画成图发给每个参与者
            text = roomlog.review_text(room_id)
            for m in (mem if text else []):
                await _push(m["role_id"], IMG + text)
        else:                                                            # 关着：发一条统计回执，带「复盘 R编号」的查看提示
            text = roomlog.receipt_text(room_id)
            for m in (mem if text else []):
                await _push(m["role_id"], text)
    if status == "ended":                                                # 结戏奖励（额外扩展，开着才发）
        from changri.core import extensions as ext_core
        if ext_core.is_enabled("rewards"):
            try:
                from changri.core import rewards
                for rid, items in rewards.settle(room_id).items():
                    await _push(rid, rewards.message(room_id, items))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[结戏奖励] R{room_id} 结算失败：{e}")
    pg = pool.get(room["pool_id"]) if room["pool_id"] else None
    await _leave_group(room, gi, pg, "结戏限时已到~请及时离开！" if timeout else f"{room['kind']}{verb}{why}，请退群！",
                       ("结戏限时已到~" if timeout else f"{room['kind']}{verb}{why}，") + "请退群！管理员确认人都退了，这个群才会给下一场用。")


async def _leave_group(room, gi, pg, qq_text: str, official_text: str) -> None:
    """房间不用这个群了：小号的群群名改回空闲名、在群里说一句请退群；官方机器人的群在群里说一句，再提醒后台群去清场。"""
    if gi and gi[1].startswith(pool.QQ_PREFIX) and pg:                   # 房间群：群名改回「长日备用」（可在「QQ 小号与群号组（双骰）」页改）
        await qqmanager.room_rename(gi[0], pg, qqpool.free_name(pg["set_name"]))
    if gi and gi[1].startswith(pool.QQ_PREFIX):                          # 小号的群：小号会自己查人
        await _to_group(gi[0], gi[1], qq_text)
    elif gi:                                                             # 官方机器人的群：查不了人，等管理员确认退光了再放回
        await _to_group(gi[0], gi[1], official_text)
        staff = announce.get_backstage()                                 # 房间结束不往公告群发任何东西（作者要求）；清场提醒只发后台群，没设就不发
        if staff:
            await _to_group(staff[0], staff[1], f"🧹 {gi[2]}（R{room['id']} 用过）在等人退群：到群里看一眼，人都退光了，"
                                                f"在那个群里发「放回备用群」（或者在这里发「放回备用群 群号」），也可以到后台「纯官方模式」点「人已退光，放回池」，它才会分给下一场。")


async def remove_room(role_id: int, room_id: int) -> tuple[bool, str]:
    """管理员「删除时间线」：整场删掉。还在进行的，告诉参与者、在房间群里请大家退群、小号的群改回空闲名（以前只是悄悄把群标成等人退群）。
    QQ 指令和后台「上帝模式」页共用。"""
    from changri.core import godmode
    room = rooms.get(room_id)
    live = bool(room and room["status"] == "open")
    mem = rooms.members(room_id) if live else []
    gi = rooms.group_info(room) if live else None
    pg = pool.get(room["pool_id"]) if live and room["pool_id"] else None
    ok, msg = godmode.delete_slot(role_id, room_id)
    if ok and live:
        for m in mem:
            await _push(m["role_id"], f"🗑 R{room_id} {room['kind']}（{_slot(room)}）已被管理员删除，时间线上也拿掉了。" + ("请退出那个群。" if gi else ""))
        await _leave_group(room, gi, pg, f"{room['kind']}已被管理员删除，请退群！",
                           f"{room['kind']}已被管理员删除，请退群！管理员确认人都退了，这个群才会给下一场用。")
    return ok, msg


async def _warn_expiring() -> None:
    """「到时间强制结群」开着时：离结戏限时还剩 N 小时，在房间群里 @ 所有参与者提醒收尾（每个房间只提醒一次）。
    房间群发不出去（比如 QQ 号掉线），就改发到每个人的个人群。"""
    left = get_int("room_expire_warn_hours", 4)
    for r in rooms.expiring_soon():
        rooms.mark_expire_warned(r["id"])
        mem = rooms.members(r["id"])
        text = f"⏰ R{r['id']} {r['kind']}离结戏限时还有 {left} 小时，可以开始收尾啦~"
        gi = rooms.group_info(r)
        sent = False
        if gi and gi[1].startswith(pool.QQ_PREFIX):                      # 监听 QQ 号管的群：按 QQ 号 @
            qqs = [q for m in mem for q in roles.qqs_of_role(m["role_id"])[:1]]
            sent = not await qqmanager.room_say(gi[0], gi[1][len(pool.QQ_PREFIX):], text, qqs)
        elif gi:                                                         # 官方机器人的群：写上名字
            await _to_group(gi[0], gi[1], " ".join(f"@{m['name']}" for m in mem) + "\n" + text)
            sent = True
        if not sent:
            for m in mem:
                await _push(m["role_id"], text)


NO_GROUP_TEXT = "备用群池暂时没有空闲的群，这次{kind}没能开成，请稍后再试，或联系管理员多准备几个备用群。"


async def _ensure_clean(room_id: int, drop: bool = True) -> bool:
    """分到的群如果是 QQ 号监听那条线的群，就查一遍群成员：除了群主、管理员、我们的 QQ 号和这场的参与者，
    还有别人在里面（绑没绑 QQ 都算），这个群先放一边（等人退群），给房间换一个。问不到群成员就当没人。
    换来的每个群都照样查，直到分到干净的或者没群可换（每换一次就有一个空群变成「等人退群」，一定会停）。
    换不到干净的群：drop=True（刚发起的房间）当没有空群，房间删掉（不排队）；
    drop=False（已经在排队的房间）房间留着、退回未分群，下一轮接着排。两种都返回 False；其余情况返回 True。"""
    while True:
        room = rooms.get(room_id)
        if not room or room["status"] != "open" or not room["pool_id"]:
            return True
        g = pool.get(room["pool_id"])
        if not g or not pool.is_qq_only(g):
            return True
        others = await qqmanager.group_others(g["bot_id"], g["real_gid"])
        if others is None or not others - pool.room_qqs(room_id):
            return True
        pool.reject(g["id"], room_id)
        if not pool.allocate(room_id):
            if drop:
                with get_conn() as c:
                    c.execute("DELETE FROM rooms WHERE id=?", (room_id,))
                pool.note_exhausted(room["kind"])                                # 定时任务会提醒后台群
            return False


async def settle_wish_room(wish_id: int, room_id: int) -> bool:
    """摘心愿开的房间也要查分到的群干不干净（跟约会、官约一样）。换不到干净的群：房间已撤掉，心愿退回没被摘的状态，返回 False。"""
    if await _ensure_clean(room_id):
        return True
    wishes.untake(wish_id)
    return False


async def _sweep_cleaning() -> None:
    """等人退群的群：除了群主、管理员和我们的 QQ 号，群里没别人了（绑没绑 QQ 都算）就自动放回池。"""
    for g in pool.all_groups():
        if g["state"] != "cleaning" or not g["in_pool"] or not pool.is_qq_only(g):
            continue
        others = await qqmanager.group_others(g["bot_id"], g["real_gid"])
        if others is not None and not others:
            pool.release(g["id"], "free")


review_cmd = _cmd("复盘")
REVIEW_LIST = 15


def _review_line(r, my_id: int) -> str:
    """复盘名单的一行：R12 约会 · D2 14:00-15:00 · 咖啡厅 · 和 小满、阿川"""
    others = "、".join(m["name"] for m in rooms.members(r["id"]) if m["role_id"] != my_id) or "自己"
    return f"R{r['id']} {r['kind']} · D{r['day']} {hm_text(r['start_hm'], r['end_hm'])}" + (f" · {r['place']}" if r["place"] else "") + f" · 和 {others}"


@review_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """发「复盘 R12」：把那一场房间的戏文画成图发出来（只有参与者和管理员能看）。不带编号先列出我结束了的场次（房间号、和谁、几点、在哪），点按钮看。"""
    from changri.core import roomlog
    from changri.core.qqsend import IMG
    me = _me(bot, event)
    if not me and not _is_admin(bot, event):
        await review_cmd.finish(NEED_ROLE)
    rid = _room_arg(args.extract_plain_text())
    room = rooms.get(rid) if rid else None
    if not room and not rid and me:
        mine = [r for r in rooms.timeline(me["id"]) if r["status"] == "ended"][-REVIEW_LIST:][::-1]     # 最近的在上面
        if not mine:
            await review_cmd.finish("你还没有结束了的场次。约会、电话结束以后才能复盘。")
        lines = [_review_line(r, me["id"]) for r in mine]
        await reply_buttons(review_cmd, "📖 你可以复盘的场次（最近的在上面）\n" + "\n".join(lines) + "\n\n发「复盘 房间号」看那一场，例：复盘 R" + str(mine[0]["id"]),
                            [Btn(f"复盘 R{r['id']}", f"复盘 R{r['id']}") for r in mine[:6]], per_row=3)
        await review_cmd.finish()
    if not room:
        await review_cmd.finish("用法：复盘 R12（房间号在邀请和回执里）。不带编号看你最近结束的一场。")
    if me and not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])) and not _is_admin(bot, event):
        await review_cmd.finish("你不是这个房间的参与者。")
    text = roomlog.review_text(room["id"])
    if not text:
        logger.info(f"[复盘] R{room['id']} 没有戏文：房间群里要有官方机器人（开着「接收全部消息」），或者有监听的 QQ 号（说话的人要绑定过 QQ）")
        await review_cmd.finish("这场没有记到戏文。戏文要按「角色名：正文」的格式写；格式没问题还是没有的话，可以找管理员看看。")
    await reply_long(review_cmd, IMG + text)


end_room = _cmd("结束约会", {"结束私约", "结束电话", "结束官约", "结束微信", "结束心愿", "结束前置电话", "结束踩点"})


@end_room.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    arg = _room_arg(args.extract_plain_text())
    if not room and arg:                         # 房间群里没有卡拉：在自己的群里发「结束约会 R12」
        room = rooms.get(arg)
        if room and room["status"] != "open":
            room = None
    if not room:
        await end_room.finish("这里没有进行中的约会房间。在自己的群里结束，要带房间号：结束约会 R12。")
    me = _me(bot, event)
    if not me and not _is_admin(bot, event):
        await end_room.finish(NEED_ROLE)
    if me and not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])) and not _is_admin(bot, event):
        await end_room.finish("你不是这个房间的参与者。")
    await _finish_room(room["id"], "ended", "")


backfill_cmd = _cmd("补录")


@backfill_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """补录只由卡拉做：回复一条合并转发的聊天记录，发「补录 R号」（R 号是这次约会/电话/官约的编号，季度内不重复），
    只补进进行中的季度。在自己的个人群里发就行，
    房间群是不是卡拉的群都可以（小号的群卡拉不用进）；在卡拉的房间群里也可以不带编号。
    QQ 会把被引用的记录作为文字带过来（只有发送者和内容，没有时间），已有的跳过，缺的按顺序插进复盘。"""
    from changri.core import roomlog
    if get_config("qq_backfill_enabled", "1") != "1":
        logger.info("[补录] 没打开：设置平台「QQ 小号与群号组（双骰）」页 qq_backfill_enabled")
        await backfill_cmd.finish("补录现在没有打开，想用的话找管理员。")
    if not season.running():
        await backfill_cmd.finish("现在没有进行中的季度，补录只能补进进行中的季度。")
    arg = _room_arg(args.extract_plain_text())
    room = rooms.get(arg) if arg else rooms.room_of_group(bot.self_id, event.group_openid)
    if not room or room["status"] == "cancelled":
        logger.info(f"[补录·卡拉] 拒绝：房间 {arg} 不存在（{event.group_openid}）")
        await backfill_cmd.finish("用法：回复那条合并转发的聊天记录，发「补录 R房间号」（在自己的群里发就行）。")
    me = _me(bot, event)
    if not _is_admin(bot, event) and not (me and any(m["role_id"] == me["id"] for m in rooms.members(room["id"]))):
        await backfill_cmd.finish(f"只有 R{room['id']} 的参与者或管理员能补录。")
    quoted = "\n".join(el.content or "" for el in (getattr(event, "msg_elements", None) or []))
    items = roomlog.parse_quoted_forward(quoted)
    logger.info(f"[补录·卡拉] R{room['id']}：引用文字 {len(quoted)} 字，读出 {len(items)} 条")
    if not items:
        await backfill_cmd.finish("没读到聊天记录：请长按那条合并转发，点「回复」，再发「补录 R房间号」。")
    res = roomlog.backfill_quoted(room["id"], items)
    logger.info(f"[补录·卡拉] R{room['id']}：{res}")
    await backfill_cmd.finish(f"📥 R{room['id']} 补录好了：补进 {res['added']} 条，已经有的 {res['skipped']} 条跳过。"
                              "拿不到原来的时间，缺的按顺序插在已有发言之间；同一个群用过好几场的话，转发时只选这一场的消息。")


def _room_arg(text: str) -> int | None:
    """房间号：R12、12、#12、[R12]、【12】都认（textutil.parse_id，全部编号共用一套写法）。"""
    return parse_id(text, "R")


force_end = _cmd("强结约会", {"强结", "强结私约"})


@force_end.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await force_end.finish(NOT_ADMIN)
    rid = _room_arg(args.extract_plain_text())
    room = rooms.get(rid) if rid else None
    if not room or room["status"] != "open":
        await force_end.finish("用法：强结约会 R房间号，例：强结约会 R12（只能结束进行中的）。")
    await _finish_room(rid, "ended", "（管理员强制结束）")
    await force_end.finish(f"✅ R{rid} 已强制结束。")


cancel_official = _cmd("取消官约", {"取消房间"})


@cancel_official.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await cancel_official.finish(NOT_ADMIN)
    rid = _room_arg(args.extract_plain_text())
    room = rooms.get(rid) if rid else None
    if not room or room["status"] != "open":
        await cancel_official.finish("用法：取消官约 R房间号，例：取消官约 R12（取消后不算发生过，从时间线上拿掉）。")
    await _finish_room(rid, "cancelled", "（管理员取消）")
    await cancel_official.finish(f"✅ R{rid} 已取消。")
