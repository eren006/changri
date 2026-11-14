"""核心玩法：私约/电话/官约/微信（约会房间）、时间线、我的待回。（论坛、心愿已拆成可开关的官方扩展 plugins/forum.py、plugins/wishes.py）
规则和数据在 core/rooms.py、pool.py、wishes.py、pending.py；这里只管解析指令、回话、通知。
官方机器人建不了群：房间从「群池」里领一个空群，群号由管理员在设置平台填；群池用完就排队。"""
import json
import re
import time

import nonebot
from nonebot import logger, on_command, on_message
from nonebot.adapters.qq import Bot, GroupAddRobotEvent, GroupMessageCreateEvent
from nonebot.params import CommandArg

from plugins._long import make_send, reply_long, send_to_group  # noqa: E402
from core import admins, announce, messaging, pending, pool, qqmanager, qqpool, roles, rooms, season, windows, wishes
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


async def _push(role_id: int, text: str, at: bool = True) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, make_send(), at)     # 发到角色群时默认自动 @ 本人（见 messaging.push_to_role_ex）
    return ok


async def _push_group(role_id: int, room) -> bool:
    """分配到群号的通知拆成两条里的第二条：不 @、纯文字，只写请入群 + 群号（方便直接复制）。"""
    return await _push(role_id, _group_line(room), at=False)


async def _to_group(bot_id: str, group_openid: str, text: str) -> None:
    if group_openid.startswith(pool.QQ_PREFIX):          # 只有群号的房间群：卡拉不在里面，由监听的 QQ 号在群里说
        err = await qqmanager.room_say(bot_id, group_openid[len(pool.QQ_PREFIX):], text, [])
        if err:
            logger.warning(f"[房间] 群里发话失败：{err}")
        return
    bot = nonebot.get_bots().get(bot_id)
    if bot is None:
        return
    try:
        await send_to_group(bot, group_openid, text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 发群失败：{e}")


from core.overview import group_line as _group_line, slot_text as _slot  # noqa: E402


RP_FORMAT_TIP = "✍️ 戏文格式：首行「角色名」+ 换行/空格/冒号（：或:）+ 正文，其余视为闲聊，不计入存档与字数"


def _room_text(room, who_from: str = "") -> str:
    """通知的第一条：@ 本人 + 类型、时间地点、参与者 + 须知（跟长日系统一样）。群号另发第二条（_push_group）。"""
    mem = "、".join(m["name"] for m in rooms.members(room["id"]))
    head = f"💌 {who_from} 向你发起了{room['kind']}" if who_from else f"💌 {room['kind']}"
    tips = [RP_FORMAT_TIP]
    if room["kind"] != "微信":
        tips += [f"修改时间 ➜ 修改时间线 D{room['day']} 新时间", f"不想参加 ➜ 拒绝时间线 R{room['id']}"]
    tips.append(f"结束互动 ➜ 在约会群里直接发「结束{room['kind']}」")
    return f"{head}\n{_slot(room)}\n参与者：{mem}\n房间号 R{room['id']}\n\n" + "\n".join(tips)


async def _invite(role_id: int, room, who_from: str = "", fix=None) -> None:
    """发邀请：第一条 @ + 时间地点人物 + 须知，第二条纯文字「请入群：群号」。fix 可对第一条做替换（官约、官电的标题）。"""
    text = _room_text(room, who_from)
    if fix:
        text = text.replace(*fix)
    await _push(role_id, text)
    await _push_group(role_id, room)


async def _announce_in_room(room_id: int) -> None:
    """房间领到群后，在那个群里发一条说明。"""
    r = rooms.get(room_id)
    gi = rooms.group_info(r) if r else None
    if not gi:
        return
    mem = "、".join(m["name"] for m in rooms.members(room_id))
    if gi[1].startswith(pool.QQ_PREFIX):                                 # 房间群：只改群名，不发言
        await qqmanager.room_rename(gi[0], pool.get(r["pool_id"]), qqpool.room_name(r, [m["name"] for m in rooms.members(room_id)]))
        return
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
    if room_id:
        await _ensure_clean(room_id)
    if not ok:
        await matcher.finish(f"❌ {info}")
    room = rooms.get(room_id)
    for t in targets:
        await _invite(t["id"], room, me["name"])
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
    if room_id:
        await _ensure_clean(room_id)
    if not ok:
        await official.finish(f"❌ {info}")
    room = rooms.get(room_id)
    for t in targets:
        await _invite(t["id"], room, fix=("💌 官约", "🏢 官约"))
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
    if room_id:
        await _ensure_clean(room_id)
    if not ok:
        await official_phone.finish(f"❌ {info}")
    room = rooms.get(room_id)
    for t in targets:
        await _invite(t["id"], room, fix=("💌 电话", "📞 官电"))
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
        if rid:
            await _ensure_clean(rid)
        if ok:
            room = rooms.get(rid)
            for t in targets:
                await _invite(t["id"], room, fix=("💌 官约", "🏢 官约"))
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
    verb = "已经结束" if status == "ended" else "已经取消"
    if status != "ended":                                                # 正常结束不再往个人群发通知；取消、超时这类参与者不一定在场，才通知
        for m in mem:
            await _push(m["role_id"], f"🔚 R{room_id} {room['kind']}{verb}{why}。" + ("请退出那个群，管理员清场后才能再给别人用。" if gi else ""))
    if status == "ended":
        from core import roomlog
        from plugins._long import IMG
        if get_config("room_auto_review", "0") == "1":                   # 自动复盘开着：把本场复盘画成图发给每个参与者
            text = roomlog.review_text(room_id)
            for m in (mem if text else []):
                await _push(m["role_id"], IMG + text)
        else:                                                            # 关着：发一条统计回执，带「复盘 R编号」的查看提示
            text = roomlog.receipt_text(room_id)
            for m in (mem if text else []):
                await _push(m["role_id"], text)
    if gi and gi[1].startswith(pool.QQ_PREFIX):                          # 房间群：群名改回空房间
        await qqmanager.room_rename(gi[0], pool.get(room["pool_id"]), qqpool.free_name(pool.get(room["pool_id"])["set_name"]))
    if gi:
        await _to_group(gi[0], gi[1], f"{room['kind']}{verb}{why}，请退群！")
        await _tell_announce(f"🧹 R{room_id} {room['kind']}{verb[1:]}，群 {gi[2]} 已放回群池（下次分配前会查群里有没有玩家）。")


async def _ensure_clean(room_id: int) -> None:
    """分到的群如果是 QQ 号监听那条线的群，就查一遍群成员：还有别的玩家在里面，
    这个群先放一边（等人退群），给房间换一个。问不到群成员就当没人。"""
    for _ in range(8):
        room = rooms.get(room_id)
        if not room or room["status"] != "open" or not room["pool_id"]:
            return
        g = pool.get(room["pool_id"])
        if not g or not pool.is_qq_only(g):
            return
        members = await qqmanager.group_members(g["bot_id"], g["real_gid"])
        if members is None:
            return
        if not (members & pool.player_qqs()) - pool.room_qqs(room_id):
            return
        pool.reject(g["id"], room_id)
        if not pool.allocate(room_id):
            return


async def _sweep_cleaning() -> None:
    """等人退群的群：群里已经没有玩家了就自动放回池。"""
    for g in pool.all_groups():
        if g["state"] != "cleaning" or not g["in_pool"] or not pool.is_qq_only(g):
            continue
        members = await qqmanager.group_members(g["bot_id"], g["real_gid"])
        if members is not None and not members & pool.player_qqs():
            pool.release(g["id"], "free")


review_cmd = _cmd("复盘")


@review_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """发「复盘 R12」：把那一场房间的戏文画成图发出来（只有参与者和管理员能看）。不带编号就看我最近一场。"""
    from core import roomlog
    from plugins._long import IMG
    me = _me(bot, event)
    if not me and not _is_admin(bot, event):
        await review_cmd.finish(NEED_ROLE)
    rid = _room_arg(args.extract_plain_text())
    room = rooms.get(rid) if rid else None
    if not room and not rid and me:
        mine = [r for r in rooms.timeline(me["id"]) if r["status"] == "ended"]
        room = mine[-1] if mine else None
    if not room:
        await review_cmd.finish("用法：复盘 R12（房间号在邀请和回执里）。不带编号看你最近结束的一场。")
    if me and not any(m["role_id"] == me["id"] for m in rooms.members(room["id"])) and not _is_admin(bot, event):
        await review_cmd.finish("你不是这个房间的参与者。")
    text = roomlog.review_text(room["id"])
    if not text:
        await review_cmd.finish("这场没有记到戏文。房间群里要有监听的 QQ 号，说话的人要绑定过 QQ，戏文要按「角色名：正文」的格式写。")
    await reply_long(review_cmd, IMG + text)


end_room = _cmd("结束私约", {"结束电话", "结束官约", "结束微信", "结束心愿", "结束约会"})


@end_room.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    room = rooms.room_of_group(bot.self_id, event.group_openid)
    arg = _room_arg(args.extract_plain_text())
    if not room and arg:                         # 房间群里没有卡拉：在自己的群里发「结束私约 R12」
        room = rooms.get(arg)
        if room and room["status"] != "open":
            room = None
    if not room:
        await end_room.finish("这里没有进行中的约会房间。在自己的群里结束，要带房间号：结束私约 R12。")
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
        await _push(q["role_id"], f"✅ {me['name']} 同意了你加入 R{room['id']} {room['kind']}。\n{_slot(room)}")
        await _push_group(q["role_id"], room)
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
    from core import extensions as _ext
    if _ext.is_enabled("wishes"):
        lines.append(f"挂心愿 {one(wishes.day_count(me['id'], 'post'), get_int('limit_挂心愿', 0))}")
        lines.append(f"摘心愿 {one(wishes.day_count(me['id'], 'take'), get_int('limit_摘心愿', 0))}")
    await my_count.finish("\n".join(lines))


# ================= 定时：过期结束、给排队的房间补群 =================

@scheduler.scheduled_job("interval", seconds=30, id="rooms_tick", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    if not nonebot.get_bots():
        return
    try:
        for r in rooms.expired():
            await _finish_room(r["id"], "ended", "（超过存活时间自动结束）")
        await _sweep_cleaning()
        for rid in rooms.assign_waiting():
            await _ensure_clean(rid)
            room = rooms.get(rid)
            if not room or not room["pool_id"]:
                continue
            for m in rooms.members(rid):
                await _push(m["role_id"], f"🔔 R{rid} {room['kind']}有空群了。\n{_slot(room)}")
                await _push_group(m["role_id"], room)
            await _announce_in_room(rid)
        await _check_presence()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 定时任务出错：{e}")


async def _receipt_all_here(room) -> None:
    """人到齐了。房间群（只有群号）：由监听的 QQ 号在群里 @ 所有参与者，说清楚是什么、几点、在哪；
    发不出去（QQ 号掉线、没权限）才退回发到每个人自己的群里。"""
    from core.db import current_bot
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
    for m in mem:
        await _push(m["role_id"], f"✅ R{room['id']} {room['kind']}：人都到齐了。\n{_slot(room)}\n聊完了发「结束{room['kind']} R{room['id']}」。")


async def _check_presence(only_gid: str = "") -> None:
    """房间群（只有群号的群）：问 QQ 号群里现在有谁，认过 QQ 的参与者都在群里就算到齐，不用等发言。
    有人进群时马上查那一个群（only_gid）；定时任务每 30 秒再把所有房间扫一遍兜底。"""
    from core.db import current_bot
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


_ROOM_CMD = re.compile(r"[/.。]?(结束(?:私约|电话|官约|微信|心愿|约会)|修改时间线|同意时间线|拒绝时间线)\s*(.*)")


async def _listen_cmd(app_id, msg) -> bool:
    """房间群里没有卡拉：有监听的 QQ 号时，群里发「结束私约 / 修改时间线 / 同意时间线 / 拒绝时间线」由 QQ 号听到，
    按发言人绑定的 QQ 认出他是房间的哪位参与者，照常处理，结果由 QQ 号在群里回。返回是否是房间指令。"""
    m = _ROOM_CMD.fullmatch(msg.text.strip())
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
        hm = rooms.parse_hm(p[0]) if p else None
        if not hm:
            await say("用法：修改时间线 [D几] 时间\n例：修改时间线 D1 1400-1500")
            return True
        ok, why = rooms.propose_change(room["id"], me["id"], day, *hm)
        await say(f"📝 {me['name']} 申请把时间线改成 D{day} {hm[0]}-{hm[1]}。其他人发「同意时间线」，全部同意后生效；不同意发「拒绝时间线」。" if ok else f"❌ {why}")
    else:
        st, ch = rooms.vote_change(room["id"], me["id"], cmd == "同意时间线")
        if st == "none":
            await say("现在没有待处理的时间线申请。")
        elif st == "rejected":
            await _push(ch["by"], f"❌ {me['name']} 拒绝了 R{room['id']} 的时间线修改。")
            await say("已拒绝，申请作废。")
        elif st == "waiting":
            await say("已记下你同意，等其他人。")
        else:
            for x in rooms.members(room["id"]):
                await _push(x["role_id"], f"✅ R{room['id']} 时间线已改为 D{ch['day']} {ch['a']}-{ch['b']}。")
            await say(f"✅ 全部同意，时间线已改为 D{ch['day']} {ch['a']}-{ch['b']}。")
    return True


async def _on_listened(app_id, msg) -> None:
    """QQ 号听到房间群里有人说话 = 到场（群里没有卡拉，靠这个补上）；最后一个人到了，私下回执给每个参与者。"""
    try:
        from core import roomlog
        roomlog.record(app_id, msg)                                  # 复盘：房间群里的发言存下来
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
        from core.db import use_bot
        with use_bot(app_id):
            await _check_presence(gid)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 进群后查到场出错：{e}")


if _on_join not in qqmanager._join_listeners:
    qqmanager.add_join_listener(_on_join)

if _on_listened not in qqmanager._listeners:
    qqmanager.add_listener(_on_listened)
