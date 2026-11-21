"""时间线：查看、删除、修改 / 同意 / 拒绝时间线，我的待回、我的数量、弧长。"""

import re

from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import pool, qqmanager, qqpool, roles, rooms, season, storycal, wishes
from changri.core.buttons import Btn
from changri.core.db import get_int
from changri.core.qqsend import reply_buttons, reply_long
from changri.core.textutil import hm_text

from .common import NEED_ROLE, _cmd, _is_admin, _me, _push
from .finish import _finish_room, _room_arg, remove_room


# ================= 时间线 / 待回 =================

tl = _cmd("时间线")


def _timeline_text(role) -> str:
    rs = rooms.timeline(role["id"])
    if not rs:
        return ""
    lines, day = [f"📅 {role['name']} 的时间线"], None
    for r in rs:
        if r["day"] != day:
            day = r["day"]; lines.append(f"\nD{day}")
        others = "、".join(m["name"] for m in rooms.members(r["id"]) if m["role_id"] != role["id"])
        st = {"open": "进行中", "ended": "已结束"}.get(r["status"], r["status"])
        lines.append(f"  {hm_text(r['start_hm'], r['end_hm'])} {r['kind']}{' @' + r['place'] if r['place'] else ''} 与 {others}（R{r['id']} {st}）")
    return "\n".join(lines)


@tl.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    target = args.extract_plain_text().strip()
    if target:                                           # 管理员：时间线 角色名，看别人的
        if not _is_admin(bot, event):
            await tl.finish("❌ 只有管理员能看别人的时间线。")
        who = roles.get_role_by_name(target)
        if not who:
            await tl.finish(f"❌ 找不到角色「{target}」。")
        await reply_long(tl, _timeline_text(who) or f"{target} 的时间线还是空的。")
        return
    me = _me(bot, event)
    if not me:
        await tl.finish(NEED_ROLE)
    await reply_long(tl, _timeline_text(me) or "你的时间线还是空的。")


del_slot = _cmd("删除时间线")


@del_slot.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await del_slot.finish("❌ 这条指令只有管理员能用。")
    p = args.extract_plain_text().split()
    if len(p) != 2 or not _room_arg(p[1]):
        await del_slot.finish("用法：删除时间线 角色名 R房间号（房间号在「时间线 角色名」里看）\n会把这个房间连同所有人的时间线和复盘记录一起删掉。")
    who = roles.get_role_by_name(p[0])
    if not who:
        await del_slot.finish(f"❌ 找不到角色「{p[0]}」。")
    ok, msg = await remove_room(who["id"], _room_arg(p[1]))
    await del_slot.finish(("✅ " if ok else "❌ ") + msg)


todo = _cmd("我的待回")


@todo.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await todo.finish(NEED_ROLE)
    from changri.core.overview import lingering, todo_items
    items = todo_items(me, await lingering(me))
    await reply_long(todo, "📋 你的待回\n" + ("\n".join(f"· {x}" for x in items) if items else "没有要回的事。"))


chg = _cmd("修改时间线")


def _when(room) -> str:
    return f"D{room['day']} {hm_text(room['start_hm'], room['end_hm'])}"


CHG_USAGE = "用法：修改时间线 [R房间号] [D几] 时间（在房间群里发可以不写房间号；在个人群发要带上）\n例：修改时间线 R12 D1 {t}"


@chg.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """在房间群里发：改这个房间。在别的群（个人群）发：带上 R房间号。申请会推到其他参与者的个人群，下面挂同意/拒绝。"""
    p = args.extract_plain_text().split()
    rid = next((int(x[1:]) for x in p if re.fullmatch(r"[Rr]\d+", x)), None)
    p = [x for x in p if not re.fullmatch(r"[Rr]\d+", x)]
    here = rooms.room_of_group(bot.self_id, event.group_openid)
    room = rooms.get(rid) if rid else here
    if not room:
        await chg.finish(("❌ 没有房间 R%d。" % rid if rid else "请在约会房间的群里发，或带上房间号。\n") + CHG_USAGE.format(t=storycal.example_time()))
    me = _me(bot, event)
    if not me or not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])):
        await chg.finish("你不是这个房间的参与者。")
    day = room["day"]
    if p and re.fullmatch(r"[Dd]\d+", p[0]):
        day = int(p.pop(0)[1:])
    hm = rooms.parse_hm(p[0], day) if p else None
    if not hm:
        await chg.finish(CHG_USAGE.format(t=storycal.example_time()))
    ok, why = rooms.propose_change(room["id"], me["id"], day, *hm)
    if not ok:
        await chg.finish(f"❌ {why}")
    r = room["id"]
    old, new = _when(room), f"D{day} {hm[0]}-{hm[1]}"
    if not [m for m in rooms.members(r) if m["role_id"] != me["id"]]:      # 只有自己（踩点）：不用等谁同意，直接生效
        await chg.finish(await _vote_result(room, me, True, bool(here and here["id"] == r)))
    vote_btns = [Btn("同意", f"同意时间线 R{r}")]          # 不同意就不理它；「拒绝时间线」是整场取消，不放在这里免得误点
    others = [m for m in rooms.members(r) if m["role_id"] != me["id"]]
    if here and here["id"] == r:            # 在房间群里：大家都看得到，直接在群里挂按钮
        await reply_buttons(chg, _propose_in_room(me, room, others, day, hm),
                            [Btn("同意时间线", "同意时间线")], per_row=2)
        await chg.finish()
    for m in others:                        # 在个人群发的：房间里的人看不到，推到他们各自的个人群
        await _push(m["role_id"], f"📝 {me['name']} 申请把 R{r} {room['kind']}的时间线改成 {new}（原来 {old}）。\n"
                                  f"同意发「同意时间线 R{r}」，全部同意后生效；不同意就不用理，时间不变。", buttons=vote_btns)
    await chg.finish(f"📝 已申请把 R{r} 的时间线从 {old} 改成 {new}，已通知" + "、".join(m["name"] for m in others) + "，全部同意后生效。\n"
                     f"在那之前时间线还是 {old}。")


async def _vote_result(room, me, agree: bool, in_room: bool) -> str:
    """同意/拒绝时间线：记票、通知相关的人、生效时改房间群名，返回给投票人的回执。卡拉在群里和 QQ 号听到的两条路共用。"""
    rid, old = room["id"], _when(room)
    st, ch = rooms.vote_change(rid, me["id"], agree)
    if st == "none":
        return f"R{rid} 现在没有待处理的时间线申请（可能已经生效或被拒绝了）。发「时间线」看当前安排。"
    new = f"D{ch['day']} {ch['a']}-{ch['b']}"
    by = roles.get_role(ch["by"])
    by_name = by["name"] if by else "对方"
    if st == "rejected":
        for m in rooms.members(rid):        # 申请人和其他收到申请的人都告诉一声，免得还在等
            if m["role_id"] != me["id"]:
                mine = m["role_id"] == ch["by"]
                await _push(m["role_id"], f"❌ {me['name']} 拒绝了{'你' if mine else ' ' + by_name + ' '}把 R{rid} 改到 {new} 的申请，"
                                          f"时间线保持 {old}。")
        return f"已拒绝 {by_name} 的申请（改到 {new}），R{rid} 的时间线保持 {old}。" + ("" if in_room else "已通知其他参与者。")
    if st == "conflict":
        for m in rooms.members(rid):
            if m["role_id"] != me["id"]:
                await _push(m["role_id"], f"⚠️ R{rid} 改到 {new} 的申请没能生效：{ch['clash']}。时间线保持 {old}。")
        return f"大家都同意了，但{ch['clash']}，改到 {new} 的申请作废，R{rid} 的时间线保持 {old}。"
    if st == "waiting":
        wait = [m["name"] for m in rooms.members(rid) if not m["agree"]]
        return f"已记下你同意改到 {new}。还在等：{'、'.join(wait)}。全部同意后生效。"
    for m in rooms.members(rid):
        await _push(m["role_id"], f"✅ R{rid} {room['kind']}的时间线已从 {old} 改为 {new}，所有人的时间线都已更新。")
    r2 = rooms.get(rid)
    gi = rooms.group_info(r2) if r2 else None
    if gi and gi[1].startswith(pool.QQ_PREFIX):             # 房间群群名里带着时间，改了要跟着改
        try:
            await qqmanager.room_rename(gi[0], pool.get(r2["pool_id"]), qqpool.room_name(r2, [m["name"] for m in rooms.members(rid)]))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[房间] 改时间后改群名失败：{e}")
    return f"✅ 全部同意，时间线已改为 {new}（原来 {old}）。"


def _propose_in_room(me, room, others, day: int, hm) -> str:
    return (f"📝 {me['name']} 申请把时间线改成 D{day} {hm[0]}-{hm[1]}（原来 {_when(room)}）。\n"
            f"要 {'、'.join(m['name'] for m in others)} 都发「同意时间线」才生效；不同意就不用理，时间不变。（「拒绝时间线」是整场取消，不是不同意改时间。）")


async def _decline(room, me, in_room: bool) -> str:
    """「拒绝时间线」= 不想参加（照老系统，作者定的更严）：开始写戏之前，任何一个参与者发，整场取消、所有人的时间线拿掉、群放回去；
    多人房也整场解散；有人申请改时间也一样直接取消（不再是对申请投反对票：不同意改时间就不理它）。官约 / 官电 / 心愿不能拒。"""
    why = rooms.decline_check(room, me["id"])
    if why:
        return f"❌ {why}"
    rid = room["id"]
    others = [m["name"] for m in rooms.members(rid) if m["role_id"] != me["id"]]
    await _finish_room(rid, "cancelled", f"（{me['name']} 拒绝了，整场取消）")
    return f"✅ 已取消 R{rid}（{_when(room)}），所有人的时间线上都拿掉了" + (f"，也通知了{'、'.join(others)}" if others else "") + "。"


async def _vote(matcher, bot: Bot, event: GroupMessageCreateEvent, args, agree: bool):
    me = _me(bot, event)
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    if not room:
        rid = _room_arg(args.extract_plain_text()) if args else None
        room = rooms.get(rid) if rid else None
    if not me or not room:
        await matcher.finish(f"请在约会房间的群里发（或带上房间号：{'同意' if agree else '拒绝'}时间线 R12）。")
    if not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])):
        await matcher.finish("你不是这个房间的参与者。")
    in_room = rooms.room_of_group(bot.self_id, event.group_openid) is not None
    await matcher.finish(await (_vote_result(room, me, True, in_room) if agree else _decline(room, me, in_room)))


agree_tl = _cmd("同意时间线")
reject_tl = _cmd("拒绝时间线")


@agree_tl.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote(agree_tl, bot, event, args, True)


@reject_tl.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote(reject_tl, bot, event, args, False)


my_count = _cmd("我的数量")


@my_count.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await my_count.finish(NEED_ROLE)
    from changri.core import messaging as m
    def one(n: int, lim: int) -> str:
        return f"{n}/{lim}" if lim > 0 else f"{n}（不限）"
    lines = [f"📊 {me['name']} 今天（{season.day_label() if season.running() else '自然日'}）的数量"]
    for k, label in (("sms", "短信"), ("gift", "送礼")):
        lines.append(f"{label} {one(m.day_count(me['id'], k), m.daily_limit(k))}")
    for k in ("约会", "电话", "微信") + (("踩点",) if rooms.stakeout_enabled() else ()):
        lines.append(f"{k} {one(rooms.day_count(me['id'], k), rooms.daily_limit(k))}")
    from changri.core import extensions as _ext
    if _ext.is_enabled("wishes"):
        lines.append(f"挂心愿 {one(wishes.day_count(me['id'], 'post'), get_int('limit_挂心愿', 0))}")
        lines.append(f"摘心愿 {one(wishes.day_count(me['id'], 'take'), get_int('limit_摘心愿', 0))}")
    from changri.core.overview import day_totals
    t = day_totals()
    kinds = ["约会", "电话"] + (["踩点"] if rooms.stakeout_enabled() else []) + ["短信", "礼物"] + (["心愿"] if _ext.is_enabled("wishes") else [])
    lines.append("🌐 全员今天：" + "｜".join(f"{k} {t.get(k, 0)}" for k in kinds))
    await my_count.finish("\n".join(lines))


my_arc = _cmd("我的弧长")


@my_arc.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await my_arc.finish(NEED_ROLE)
    from changri.core import arclen
    await reply_long(my_arc, arclen.report(me["id"]))


see_arc = _cmd("查看弧长")


@see_arc.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await see_arc.finish("只有管理员可以查看别人的弧长。自己的发「我的弧长」。")
    name = args.extract_plain_text().strip()
    if not name:
        await see_arc.finish("用法：查看弧长 角色名")
    r = roles.get_role_by_name(name)
    if not r:
        await see_arc.finish(f"没有叫「{name}」的角色。")
    from changri.core import arclen
    await reply_long(see_arc, arclen.report(r["id"]))
