"""核心玩法：私约/电话/官约/微信（约会房间）、时间线、我的待回、心愿、论坛。
规则和数据在 core/rooms.py、pool.py、wishes.py、forum.py、pending.py；这里只管解析指令、回话、通知。
官方机器人建不了群：房间从「群池」里领一个空群，群号由管理员在设置平台填；群池用完就排队。"""
import json
import re
import time

import nonebot
from nonebot import logger, on_command, on_message
from nonebot.adapters.qq import Bot, GroupAddRobotEvent, GroupMessageCreateEvent
from nonebot.params import CommandArg

from plugins._long import make_send, reply_long, send_to_group  # noqa: E402
from core import admins, announce, forum, messaging, pending, pool, roles, rooms, season, windows, wishes
from core.db import get_config, get_conn, get_int, per_bot, set_config

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"
NOT_ADMIN = "❌ 权限不足，仅管理员可用。"
SEP = re.compile(r"[/、，,]")


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _me(bot: Bot, event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


def _is_admin(bot: Bot, event: GroupMessageCreateEvent) -> bool:
    return admins.is_admin(bot.self_id, event.author.member_openid)


def _names(text: str) -> list[str]:
    return [x.strip() for x in SEP.split(text) if x.strip()]


def _resolve(names: list[str]) -> tuple[list, str]:
    found, missing = [], []
    for n in names:
        r = roles.get_role_by_name(n)
        (found if r else missing).append(r or n)
    return found, ("、".join(missing) if missing else "")


async def _push(role_id: int, text: str) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, make_send())
    return ok


async def _to_group(bot_id: str, group_openid: str, text: str) -> None:
    bot = nonebot.get_bots().get(bot_id)
    if bot is None:
        return
    try:
        await send_to_group(bot, group_openid, text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 发群失败：{e}")


from core.overview import group_line as _group_line, slot_text as _slot  # noqa: E402


def _room_text(room, who_from: str = "") -> str:
    mem = "、".join(m["name"] for m in rooms.members(room["id"]))
    head = f"💌 {who_from} 向你发起了{room['kind']}" if who_from else f"💌 {room['kind']}"
    return f"{head}\n{_slot(room)}\n参与者：{mem}\n{_group_line(room)}\n房间号 R{room['id']}（进群后发「结束{room['kind']}」可以结束）"


async def _announce_in_room(room_id: int) -> None:
    """房间领到群后，在那个群里发一条说明。"""
    r = rooms.get(room_id)
    gi = rooms.group_info(r) if r else None
    if not gi:
        return
    mem = "、".join(m["name"] for m in rooms.members(room_id))
    await _to_group(gi[0], gi[1], f"📌 R{room_id} {r['kind']}\n{_slot(r)}\n参与者：{mem}\n参与者请进群后在这里聊；聊完发「结束{r['kind']}」。群里没有的人，进群后随便说一句就算到场。")


async def _tell_announce(text: str) -> None:
    a = announce.get_announce()
    if a:
        await _to_group(a[0], a[1], text)


# ================= 群池管理（管理员） =================

@on_message(priority=1, block=False).handle()
async def _track_entered(bot: Bot, event: GroupMessageCreateEvent):
    """房间群里有人说话 = 到场。顺手把这个群登记进池子（机器人只能在有人说话时才知道群的 openid）。"""
    if not roles.find_group_binding(bot.self_id, event.group_openid):
        pool.note_group(bot.self_id, event.group_openid)
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    if not room:
        return
    me = _me(bot, event)
    if me:
        rooms.mark_entered(room["id"], me["id"])


@nonebot.on_type(GroupAddRobotEvent, priority=9, block=False).handle()
async def _added(bot: Bot, event: GroupAddRobotEvent):
    pool.note_group(bot.self_id, event.group_openid)


set_pool = _cmd("设为群池")


@set_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await set_pool.finish(NOT_ADMIN)
    parts = args.extract_plain_text().split()
    set_name = parts[0] if parts else ""
    real = parts[1] if len(parts) > 1 else ""
    d = pool.is_pool_group(bot.self_id, event.group_openid)
    pool.register(bot.self_id, event.group_openid, real_gid=real, set_name=set_name)
    ok, why = pool.set_in_pool(pool.is_pool_group(bot.self_id, event.group_openid)["id"], True)
    if not ok:
        await set_pool.finish(f"❌ {why}。")
    await set_pool.finish("✅ 这个群已放进群池" + (f"（组「{set_name}」）" if set_name else "") + ("" if real else "。群号还没填，请在设置平台「群池」页补上，玩家要靠它申请入群。"))


unset_pool = _cmd("取消群池")


@unset_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await unset_pool.finish(NOT_ADMIN)
    g = pool.is_pool_group(bot.self_id, event.group_openid)
    if not g or not g["in_pool"]:
        await unset_pool.finish("这个群不在群池里。")
    ok, why = pool.set_in_pool(g["id"], False)
    await unset_pool.finish("✅ 已移出群池。" if ok else f"❌ {why}。")


show_pool = _cmd("查看群池")


@show_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await show_pool.finish(NOT_ADMIN)
    c = pool.counts()
    await show_pool.finish(f"群池：空闲 {c['free']}，占用 {c['occupied']}，待清场 {c['cleaning']}；排队中的房间 {c['waiting']} 个。\n详细管理在设置平台「群池」页。")


# ================= 发起房间 =================

async def _start_room(matcher, bot: Bot, event: GroupMessageCreateEvent, kind: str, time_text: str, place: str, names: list[str]):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    targets, missing = _resolve(names)
    if missing:
        await matcher.finish(f"❌ 找不到角色：{missing}。")
    ok, info, room_id = rooms.create(kind, me["id"], [t["id"] for t in targets], time_text, place)
    if not ok:
        await matcher.finish(f"❌ {info}")
    room = rooms.get(room_id)
    for t in targets:
        await _push(t["id"], _room_text(room, me["name"]))
    await _announce_in_room(room_id)
    await matcher.finish(f"✅ {kind}已发起，房间号 R{room_id}。\n{_slot(room)}\n{_group_line(room)}\n对方已收到通知。")


private = _cmd("私约")


@private.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    p = args.extract_plain_text().split(None, 2)
    if len(p) < 3:
        await private.finish("用法：私约 时间 地点 对方（多人用 / 隔开）\n例：私约 1400-1500 咖啡厅 张三/李四")
    await _start_room(private, bot, event, "私约", p[0], p[1], _names(p[2]))


phone = _cmd("电话")


@phone.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    p = args.extract_plain_text().split(None, 1)
    if len(p) < 2:
        await phone.finish("用法：电话 时间 对方（多人用 / 隔开）\n例：电话 1400-1500 张三")
    await _start_room(phone, bot, event, "电话", p[0], "", _names(p[1]))


wechat = _cmd("微信")


@wechat.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    t = args.extract_plain_text().strip()
    if not t:
        await wechat.finish("用法：微信 对方（多人用 / 隔开）\n例：微信 张三")
    await _start_room(wechat, bot, event, "微信", "", "", _names(t))


official = _cmd("发起官约")


@official.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await official.finish(NOT_ADMIN)
    p = args.extract_plain_text().split(None, 2)
    if len(p) < 3:
        await official.finish("用法：发起官约 时间 地点 名字/名字（至少两人）\n例：发起官约 1400-1500 后山 张三/李四")
    targets, missing = _resolve(_names(p[2]))
    if missing:
        await official.finish(f"❌ 找不到角色：{missing}。")
    if len(targets) < 2:
        await official.finish("官约至少要两位角色。")
    ok, info, room_id = rooms.create("官约", targets[0]["id"], [t["id"] for t in targets[1:]], p[0], p[1], official=True)
    if not ok:
        await official.finish(f"❌ {info}")
    room = rooms.get(room_id)
    for t in targets:
        await _push(t["id"], _room_text(room).replace("💌 官约", "🏢 官约"))
    await _announce_in_room(room_id)
    await official.finish(f"✅ 官约已发起，房间号 R{room_id}，已通知 {len(targets)} 人。\n{_slot(room)}\n{_group_line(room)}")


official_phone = _cmd("发起官电")


@official_phone.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await official_phone.finish(NOT_ADMIN)
    p = args.extract_plain_text().split(None, 1)
    if len(p) < 2:
        await official_phone.finish("用法：发起官电 时间 名字/名字（至少两人）\n例：发起官电 2000-2100 张三/李四")
    targets, missing = _resolve(_names(p[1]))
    if missing:
        await official_phone.finish(f"❌ 找不到角色：{missing}。")
    if len(targets) < 2:
        await official_phone.finish("官电至少要两位角色。")
    ok, info, room_id = rooms.create("电话", targets[0]["id"], [t["id"] for t in targets[1:]], p[0], "", official=True, note="官电")
    if not ok:
        await official_phone.finish(f"❌ {info}")
    room = rooms.get(room_id)
    for t in targets:
        await _push(t["id"], _room_text(room).replace("💌 电话", "📞 官电"))
    await _announce_in_room(room_id)
    await official_phone.finish(f"✅ 官电已发起，房间号 R{room_id}，已通知 {len(targets)} 人。\n{_slot(room)}\n{_group_line(room)}")


def _plans() -> list[dict]:
    try:
        return json.loads(get_config("official_plans", "[]"))
    except ValueError:
        return []


plan_cmd = _cmd("计划官约")
run_plan = _cmd("执行官约")


@plan_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await plan_cmd.finish(NOT_ADMIN)
    t = args.extract_plain_text().strip()
    if t in ("", "查看"):
        ps = _plans()
        await plan_cmd.finish("📝 已计划的官约\n" + "\n".join(f"{i + 1}. {p['time']} @{p['place']} {p['names']}" for i, p in enumerate(ps)) + "\n发「执行官约」一次性发起。" if ps else "还没有计划的官约。写法：计划官约 时间 地点 名字/名字")
    if t.startswith("清空"):
        set_config("official_plans", "[]")
        await plan_cmd.finish("✅ 已清空计划。")
    p = t.split(None, 2)
    if len(p) < 3 or not rooms.parse_hm(p[0]):
        await plan_cmd.finish("用法：计划官约 时间 地点 名字/名字\n例：计划官约 1400-1500 后山 张三/李四\n（先攒着，发「执行官约」一起发起；「计划官约 清空」清掉）")
    targets, missing = _resolve(_names(p[2]))
    if missing or len(targets) < 2:
        await plan_cmd.finish(f"❌ {'找不到角色：' + missing if missing else '官约至少要两位角色。'}")
    set_config("official_plans", json.dumps(_plans() + [{"time": p[0], "place": p[1], "names": p[2]}], ensure_ascii=False))
    await plan_cmd.finish(f"✅ 已记下，现在共 {len(_plans())} 条计划。")


@run_plan.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await run_plan.finish(NOT_ADMIN)
    ps = _plans()
    if not ps:
        await run_plan.finish("没有计划的官约。")
    done, fail, left = [], [], []
    for p in ps:
        targets, _ = _resolve(_names(p["names"]))
        ok, info, rid = rooms.create("官约", targets[0]["id"], [t["id"] for t in targets[1:]], p["time"], p["place"], official=True) if len(targets) >= 2 else (False, "角色已不存在", 0)
        if ok:
            room = rooms.get(rid)
            for t in targets:
                await _push(t["id"], _room_text(room).replace("💌 官约", "🏢 官约"))
            await _announce_in_room(rid)
            done.append(f"R{rid} {p['time']} {p['names']}")
        else:
            fail.append(f"{p['time']} {p['names']}：{info}")
            left.append(p)
    set_config("official_plans", json.dumps(left, ensure_ascii=False))     # 失败的留着，改好再执行
    await reply_long(run_plan, f"✅ 已发起 {len(done)} 场\n" + "\n".join(done) + (f"\n\n❌ 失败 {len(fail)} 场（保留在计划里）\n" + "\n".join(fail) if fail else ""))


async def _finish_room(room_id: int, status: str, why: str) -> None:
    room = rooms.get(room_id)
    mem = rooms.members(room_id)
    gi = rooms.group_info(room) if room else None
    g = rooms.end(room_id, status)
    verb = "已结束" if status == "ended" else "已取消"
    for m in mem:
        await _push(m["role_id"], f"🔚 R{room_id} {room['kind']}{verb}{why}。" + ("请退出那个群，管理员清场后才能再给别人用。" if gi else ""))
    if gi:
        await _to_group(gi[0], gi[1], f"🔚 R{room_id} {verb}{why}。请大家退出本群，谢谢。")
        await _tell_announce(f"🧹 R{room_id} {room['kind']}{verb}，群 {gi[2]} 等待清场（设置平台「群池」页确认清空后放回池）。")


end_room = _cmd("结束私约", {"结束电话", "结束官约", "结束微信", "结束心愿", "结束约会"})


@end_room.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    if not room:
        await end_room.finish("这里没有进行中的约会房间。")
    me = _me(bot, event)
    if not me and not _is_admin(bot, event):
        await end_room.finish(NEED_ROLE)
    if me and not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])) and not _is_admin(bot, event):
        await end_room.finish("你不是这个房间的参与者。")
    await _finish_room(room["id"], "ended", "")


def _room_arg(text: str) -> int | None:
    m = re.fullmatch(r"[Rr]?(\d+)", text.strip())
    return int(m[1]) if m else None


force_end = _cmd("强结私约", {"强结"})


@force_end.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await force_end.finish(NOT_ADMIN)
    rid = _room_arg(args.extract_plain_text())
    room = rooms.get(rid) if rid else None
    if not room or room["status"] != "open":
        await force_end.finish("用法：强结私约 R房间号（只能结束进行中的）。")
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
        await cancel_official.finish("用法：取消官约 R房间号（取消后不算发生过，从时间线上拿掉）。")
    await _finish_room(rid, "cancelled", "（管理员取消）")
    await cancel_official.finish(f"✅ R{rid} 已取消。")


# ================= 申请加入 =================

join_cmd = _cmd("申请加入")
join_list = _cmd("加入请求")
join_yes = _cmd("同意加入")
join_no = _cmd("拒绝加入")


@join_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await join_cmd.finish(NEED_ROLE)
    p = args.extract_plain_text().split()
    if not p:
        await join_cmd.finish("用法：申请加入 对方名 [开始时间]\n例：申请加入 张三 14:00")
    target = roles.get_role_by_name(p[0])
    if not target:
        await join_cmd.finish(f"❌ 找不到角色：{p[0]}。")
    cands = rooms.find_joinable(target["id"], p[1] if len(p) > 1 else "")
    if not cands:
        await join_cmd.finish(f"「{target['name']}」现在没有可以加入的约会（电话和微信不能加入）。")
    if len(cands) > 1:
        await join_cmd.finish(f"「{target['name']}」有好几场，请带上开始时间：\n" + "\n".join(f"· {_slot(r)} {r['kind']}" for r in cands))
    ok, why, qid = rooms.request_join(cands[0]["id"], me["id"])
    if not ok:
        await join_cmd.finish(f"❌ {why}")
    r = cands[0]
    for m in rooms.members(r["id"]):
        await _push(m["role_id"], f"🙋 {me['name']} 想加入 R{r['id']} {r['kind']}（{_slot(r)}）。\n同意发「同意加入 {qid}」，拒绝发「拒绝加入 {qid}」（「加入请求」可以看全部）。")
    await join_cmd.finish("✅ 申请已发出，等对方回复。")


@join_list.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await join_list.finish(NEED_ROLE)
    qs = rooms.pending_requests(me["id"])
    await join_list.finish("🙋 收到的加入申请\n" + "\n".join(f"#{q['id']} {q['applicant']} 想加入 R{q['room_id']} {q['kind']}（D{q['day']} {q['start_hm']}-{q['end_hm']}）" for q in qs) if qs else "没有待处理的加入申请。")


async def _answer(matcher, bot, event, args, accept: bool):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    n = _room_arg(args.extract_plain_text())
    if not n:
        await matcher.finish("用法：同意加入 编号（编号在「加入请求」里）")
    ok, why, q = rooms.answer_request(n, me["id"], accept)
    if not ok:
        await matcher.finish(f"❌ {why}")
    room = rooms.get(q["room_id"])
    if accept:
        await _push(q["role_id"], f"✅ {me['name']} 同意了你加入 R{room['id']} {room['kind']}。\n{_slot(room)}\n{_group_line(room)}")
        await matcher.finish("✅ 已同意，对方会收到群号。")
    await _push(q["role_id"], f"❌ 你加入 R{room['id']} 的申请被拒绝了。")
    await matcher.finish("已拒绝。")


@join_yes.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _answer(join_yes, bot, event, args, True)


@join_no.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _answer(join_no, bot, event, args, False)


# ================= 时间线 / 待回 =================

tl = _cmd("时间线")


@tl.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await tl.finish(NEED_ROLE)
    rs = rooms.timeline(me["id"])
    if not rs:
        await tl.finish("你的时间线还是空的。")
    lines, day = [f"📅 {me['name']} 的时间线"], None
    for r in rs:
        if r["day"] != day:
            day = r["day"]; lines.append(f"\nD{day}")
        others = "、".join(m["name"] for m in rooms.members(r["id"]) if m["role_id"] != me["id"])
        st = {"open": "进行中", "ended": "已结束"}.get(r["status"], r["status"])
        lines.append(f"  {r['start_hm']}-{r['end_hm']} {r['kind']}{' @' + r['place'] if r['place'] else ''} 与 {others}（R{r['id']} {st}）")
    await reply_long(tl, "\n".join(lines))


todo = _cmd("我的待回")


@todo.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await todo.finish(NEED_ROLE)
    from core.overview import todo_items
    items = todo_items(me)
    await reply_long(todo, "📋 你的待回\n" + ("\n".join(f"· {x}" for x in items) if items else "没有要回的事。"))


chg = _cmd("修改时间线")


@chg.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    if not room:
        await chg.finish("请在约会房间的群里发。")
    me = _me(bot, event)
    if not me or not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])):
        await chg.finish("你不是这个房间的参与者。")
    p = args.extract_plain_text().split()
    day = room["day"]
    if p and re.fullmatch(r"[Dd]\d+", p[0]):
        day = int(p.pop(0)[1:])
    hm = rooms.parse_hm(p[0]) if p else None
    if not hm:
        await chg.finish("用法：修改时间线 [D几] 时间\n例：修改时间线 D1 1400-1500")
    ok, why = rooms.propose_change(room["id"], me["id"], day, *hm)
    if not ok:
        await chg.finish(f"❌ {why}")
    await chg.finish(f"📝 {me['name']} 申请把时间线改成 D{day} {hm[0]}-{hm[1]}。其他人发「同意时间线」，全部同意后生效；不同意发「拒绝时间线」。")


async def _vote(matcher, bot: Bot, event: GroupMessageCreateEvent, args, agree: bool):
    me = _me(bot, event)
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    if not room:
        rid = _room_arg(args.extract_plain_text()) if args else None
        room = rooms.get(rid) if rid else None
    if not me or not room:
        await matcher.finish("请在约会房间的群里发（或带上房间号：拒绝时间线 R12）。")
    if not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])):
        await matcher.finish("你不是这个房间的参与者。")
    st, ch = rooms.vote_change(room["id"], me["id"], agree)
    if st == "none":
        await matcher.finish("现在没有待处理的时间线申请。")
    if st == "rejected":
        await _push(ch["by"], f"❌ {me['name']} 拒绝了 R{room['id']} 的时间线修改。")
        await matcher.finish("已拒绝，申请作废。")
    if st == "waiting":
        await matcher.finish("已记下你同意，等其他人。")
    for m in rooms.members(room["id"]):
        await _push(m["role_id"], f"✅ R{room['id']} 时间线已改为 D{ch['day']} {ch['a']}-{ch['b']}。")
    await matcher.finish(f"✅ 全部同意，时间线已改为 D{ch['day']} {ch['a']}-{ch['b']}。")


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
    from core import messaging as m
    def one(n: int, lim: int) -> str:
        return f"{n}/{lim}" if lim > 0 else f"{n}（不限）"
    lines = [f"📊 {me['name']} 今天（{season.day_label() if season.running() else '自然日'}）的数量"]
    for k, label in (("sms", "短信"), ("gift", "送礼")):
        lines.append(f"{label} {one(m.day_count(me['id'], k), m.daily_limit(k))}")
    for k in ("私约", "电话", "微信"):
        lines.append(f"{k} {one(rooms.day_count(me['id'], k), rooms.daily_limit(k))}")
    lines.append(f"挂心愿 {one(wishes.day_count(me['id'], 'post'), get_int('limit_挂心愿', 0))}")
    lines.append(f"摘心愿 {one(wishes.day_count(me['id'], 'take'), get_int('limit_摘心愿', 0))}")
    await my_count.finish("\n".join(lines))


# ================= 心愿 =================

def _wish_line(w) -> str:
    who = w["sign"] or w["poster_name"]
    return f"#{w['id']} D{w['day']} {w['start_hm']}-{w['end_hm']} @{w['place']}：{w['content']}" + (f"｜悬赏：{w['reward']}" if w["reward"] else "") + f"（{who}）"


async def _post_wish(matcher, bot, event, args, bounty: bool):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    text = args.extract_plain_text().strip()
    sign = ""
    reward = ""
    if "|" in text or "｜" in text:
        text, reward = re.split(r"[|｜]", text, 1)
        text, reward = text.strip(), reward.strip()
    if bounty and not reward:
        await matcher.finish("用法：悬赏心愿 时间 地点 内容 | 奖励\n例：悬赏心愿 1400-1500 图书馆 陪我看书 | 滋补汤 1")
    p = text.split(None, 2)
    if len(p) < 3:
        await matcher.finish("用法：挂心愿 时间 地点 内容\n例：挂心愿 1400-1500 花园 一起散步")
    ok, info = wishes.post(me["id"], p[0], p[1], p[2], reward, sign)
    if not ok:
        await matcher.finish(f"❌ {info}")
    if get_config("wish_public_send", "0") == "1":
        ann = announce.get_announce()
        if ann:
            try:
                await make_send()(ann[0], ann[1], "📌 新心愿 " + _wish_line(wishes.get(int(info))))
            except Exception as e:  # noqa: BLE001
                logger.warning("心愿公开播报失败：{}", e)
    await matcher.finish(f"✅ 心愿 #{info} 已挂出，等人来摘。" + ("\n（奖励只是登记，兑现靠你们自己，机器人不转物品。）" if reward else ""))


wish_post = _cmd("挂心愿")
wish_bounty = _cmd("悬赏心愿")
wish_list = _cmd("看心愿")
wish_take = _cmd("摘心愿")
wish_back = _cmd("撤心愿")


@wish_post.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _post_wish(wish_post, bot, event, args, False)


@wish_bounty.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _post_wish(wish_bounty, bot, event, args, True)


@wish_list.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    ws = wishes.list_open()
    await reply_long(wish_list, "🌠 开放的心愿\n" + "\n".join(_wish_line(w) for w in ws) + "\n摘一个发「摘心愿 编号」" if ws else "现在没有开放的心愿。")


@wish_take.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_take.finish(NEED_ROLE)
    n = _room_arg(args.extract_plain_text())
    if not n:
        await wish_take.finish("用法：摘心愿 编号")
    ok, info, room_id = wishes.take(n, me["id"])
    if not ok:
        await wish_take.finish(f"❌ {info}")
    w, room = wishes.get(n), rooms.get(room_id)
    await _push(w["poster_id"], f"🌠 你的心愿 #{n} 被 {me['name']} 摘走了！\n" + _room_text(room).replace("💌 心愿", "💌 心愿约会"))
    await _announce_in_room(room_id)
    await wish_take.finish(f"✅ 你摘了心愿 #{n}，{w['poster_name']} 已收到通知。\n{_slot(room)}\n{_group_line(room)}\n房间号 R{room_id}")


@wish_back.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_back.finish(NEED_ROLE)
    n = _room_arg(args.extract_plain_text())
    if not n:
        await wish_back.finish("用法：撤心愿 编号")
    ok, why = wishes.withdraw(n, me["id"])
    await wish_back.finish(f"✅ 心愿 #{n} 已撤回。" if ok else f"❌ {why}")


# ================= 论坛 =================

def _split_sign(text: str, role_name: str) -> tuple[str, str]:
    """「署名 内容」或只有「内容」。"""
    p = text.split(None, 1)
    return (p[0], p[1]) if len(p) == 2 else (role_name, text)


post_cmd = _cmd("发帖")
reply_cmd = _cmd("回复帖子")
up_cmd = _cmd("点赞")
down_cmd = _cmd("点踩")
up_floor = _cmd("点赞楼层")
down_floor = _cmd("点踩楼层")
view_cmd = _cmd("查看帖子")


@post_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await post_cmd.finish(NEED_ROLE)
    t = args.extract_plain_text().strip()
    if not t:
        await post_cmd.finish("用法：发帖 署名 内容（只写内容就用角色名署名）\n例：发帖 路人甲 今天天气真好！")
    sign, content = _split_sign(t, me["name"])
    if len(content) > 1000:
        await post_cmd.finish("帖子太长了（最多 1000 字）。")
    if windows.check("forum"):
        await post_cmd.finish(windows.check("forum"))
    pid = forum.new_post(me["id"], sign, content)
    await _tell_announce(f"📰 新帖 #{forum.pid(pid)}  {sign}：{content[:40]}{'…' if len(content) > 40 else ''}")
    await post_cmd.finish(f"✅ 已发帖，贴号 {forum.pid(pid)}。")


@reply_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await reply_cmd.finish(NEED_ROLE)
    p = args.extract_plain_text().split(None, 1)
    n = forum.parse_post_ref(p[0]) if p else None
    if not n or len(p) < 2:
        await reply_cmd.finish("用法：回复帖子 贴号 [引用N|署名] 内容\n例：回复帖子 001 你说得对！\n　　回复帖子 001 引用2 同意\n　　回复帖子 001 路人乙 我也觉得")
    rest = p[1].strip()
    quote = 0
    m = re.match(r"^引用(\d+)\s*(.*)$", rest, re.S)
    if m:
        quote, rest = int(m[1]), m[2].strip()
    sign, content = (me["name"], rest) if m else _split_sign(rest, me["name"])
    if not content:
        await reply_cmd.finish("回复内容不能为空。")
    ok, why, floor = forum.new_reply(n, me["id"], sign, content[:1000], quote)
    await reply_cmd.finish(f"✅ 已回复 {forum.pid(n)}，你在 {floor} 楼。" if ok else f"❌ {why}")


async def _vote_post(matcher, bot, event, args, val: int):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    n = forum.parse_post_ref(args.extract_plain_text())
    if not n or not forum.get_post(n):
        await matcher.finish("用法：点赞 贴号（点踩同理）。")
    ok, why = forum.vote(f"p{n}", me["id"], val)
    up, down = forum.tally(f"p{n}")
    await matcher.finish(f"{'👍' if val > 0 else '👎'} 已记下。{forum.pid(n)}：👍{up} 👎{down}" if ok else why)


async def _vote_floor(matcher, bot, event, args, val: int):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    p = args.extract_plain_text().split()
    n = forum.parse_post_ref(p[0]) if p else None
    if not n or len(p) < 2 or not p[1].isdigit():
        await matcher.finish("用法：点赞楼层 贴号 楼层（点踩楼层同理）。")
    floor = int(p[1])
    if floor == 1:
        await matcher.finish("1 楼就是主帖，直接用「点赞 贴号」。")
    rid = forum.reply_id(n, floor)
    if not rid:
        await matcher.finish(f"{forum.pid(n)} 没有第 {floor} 楼。")
    ok, why = forum.vote(f"r{rid}", me["id"], val)
    up, down = forum.tally(f"r{rid}")
    await matcher.finish(f"{'👍' if val > 0 else '👎'} 已记下。{forum.pid(n)} {floor} 楼：👍{up} 👎{down}" if ok else why)


@up_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_post(up_cmd, bot, event, args, 1)


@down_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_post(down_cmd, bot, event, args, -1)


@up_floor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_floor(up_floor, bot, event, args, 1)


@down_floor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_floor(down_floor, bot, event, args, -1)


del_post = _cmd("删帖")
del_floor = _cmd("删楼层")


@del_post.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await del_post.finish(NOT_ADMIN)
    n = forum.parse_post_ref(args.extract_plain_text())
    await del_post.finish(f"✅ 已删除帖子 {forum.pid(n)}（连同回复和点赞）。" if n and forum.delete_post(n) else "用法：删帖 贴号（没有这个帖子也会提示这条）。")


@del_floor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await del_floor.finish(NOT_ADMIN)
    p = args.extract_plain_text().split()
    n = forum.parse_post_ref(p[0]) if p else None
    if not n or len(p) < 2 or not p[1].isdigit() or not forum.delete_floor(n, int(p[1])):
        await del_floor.finish("用法：删楼层 贴号 楼层（1 楼是主帖，要删整帖用「删帖」）。")
    await del_floor.finish(f"✅ 已删除 {forum.pid(n)} 的 {p[1]} 楼（楼号保留，内容改成「该楼层已被删除」）。")


@view_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    t = args.extract_plain_text().strip()
    if not t:
        ps = forum.list_posts()
        if not ps:
            await view_cmd.finish("论坛还没有帖子。")
        lines = [f"{forum.pid(p['id'])}  {p['sign']}：{p['content'][:30]}{'…' if len(p['content']) > 30 else ''}（{p['n']} 回复 👍{forum.tally('p%d' % p['id'])[0]}）" for p in ps]
        await reply_long(view_cmd, "📰 最近的帖子\n" + "\n".join(lines) + "\n看某个帖子发「查看帖子 贴号」")
    n = forum.parse_post_ref(t)
    th = forum.thread(n) if n else None
    if not th:
        await view_cmd.finish("没有这个帖子。")
    p, rs = th
    up, down = forum.tally(f"p{p['id']}")
    out = [f"📰 {forum.pid(p['id'])}  {p['sign']}  👍{up} 👎{down}", f"1楼  {p['content']}"]
    for r in rs:
        u, d = forum.tally(f"r{r['id']}")
        out.append(f"{r['floor']}楼  {r['sign']}  👍{u} 👎{d}" + (f"\n　　↳ 引用 {r['quote']}" if r["quote"] else "") + f"\n　　{r['content']}")
    await reply_long(view_cmd, "\n".join(out))


# ================= 定时：过期结束、给排队的房间补群 =================

@scheduler.scheduled_job("interval", seconds=30, id="rooms_tick", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    if not nonebot.get_bots():
        return
    try:
        for r in rooms.expired():
            await _finish_room(r["id"], "ended", "（超过存活时间自动结束）")
        for rid in rooms.assign_waiting():
            room = rooms.get(rid)
            for m in rooms.members(rid):
                await _push(m["role_id"], f"🔔 R{rid} {room['kind']}有空群了。\n{_slot(room)}\n{_group_line(room)}")
            await _announce_in_room(rid)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 定时任务出错：{e}")
