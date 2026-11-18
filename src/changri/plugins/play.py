"""核心玩法：约会/电话/官约/微信（约会房间）、时间线、我的待回。（论坛、心愿已拆成可开关的官方扩展 plugins/forum.py、plugins/wishes.py）
规则和数据在 core/rooms.py、pool.py、wishes.py、pending.py；这里只管解析指令、回话、通知。
官方机器人建不了群：房间从「群池」里领一个空群，群号由管理员在设置平台填；群池用完就开不成，告诉玩家，不排队。"""
import json
import re
import time

import nonebot
from nonebot import logger, on_command, on_message
from nonebot.adapters.qq import Bot, GroupAddRobotEvent, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core.qqsend import make_send, reply_buttons, reply_long, send_to_group  # noqa: E402
from changri.core.buttons import Btn
from changri.core import admins, announce, handover, messaging, pending, pool, qqmanager, qqpool, roles, rooms, season, storycal, windows, wishes
from changri.core.db import get_config, get_conn, get_int, per_bot, set_config

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


async def _push(role_id: int, text: str, at: bool = True, buttons=None) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, make_send(), at, buttons)     # 发到角色群时默认自动 @ 本人（见 messaging.push_to_role_ex）
    return ok


async def _push_group(role_id: int, room) -> bool:
    """分配到群号的通知拆成两条里的第二条：不 @、纯文字，只写请入群 + 群号（方便直接复制）。"""
    return await _push(role_id, _group_line(room), at=False)


async def _to_group(bot_id: str, group_openid: str, text: str) -> bool:
    """往房间群里说一句。返回是否发出去了。"""
    if group_openid.startswith(pool.QQ_PREFIX):          # 只有群号的房间群：卡拉不在里面，由监听的 QQ 号在群里说
        err = await qqmanager.room_say(bot_id, group_openid[len(pool.QQ_PREFIX):], text, [])
        if err:
            logger.warning(f"[房间] 群里发话失败：{err}")
        return not err
    bot = nonebot.get_bots().get(bot_id)
    if bot is None:
        return False
    try:
        await send_to_group(bot, group_openid, text)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[房间] 发群失败：{e}")
        return False


from changri.core.overview import group_line as _group_line, slot_text as _slot  # noqa: E402


RP_FORMAT_TIP = "✍️ 戏文格式：首行「角色名」+ 换行/空格/冒号（：或:）+ 正文，其余视为闲聊，不计入存档与字数"


def _room_text(room, who_from: str = "") -> str:
    """通知的第一条：@ 本人 + 类型、时间地点、参与者 + 须知（跟长日系统一样）。群号另发第二条（_push_group）。"""
    mem = "、".join(m["name"] for m in rooms.members(room["id"]))
    head = f"💌 {who_from} 向你发起了{room['kind']}" if who_from else f"💌 {room['kind']}"
    tips = [RP_FORMAT_TIP]
    gi = rooms.group_info(room)
    if gi and not gi[1].startswith(pool.QQ_PREFIX):                     # 官方机器人的群：它查不了群成员，要说过话才算到场
        tips.append("到场 ➜ 进群后随便说一句，才算到场")
    if room["kind"] not in rooms.NO_TIMELINE:
        tips += [f"修改时间 ➜ 修改时间线 R{room['id']} D{room['day']} 新时间（个人群里也能发）", f"不想参加 ➜ 拒绝时间线 R{room['id']}"]
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


# ================= 群池管理（管理员） =================

@on_message(priority=1, block=False).handle()
async def _track_entered(bot: Bot, event: GroupMessageCreateEvent):
    """房间群里有人说话 = 到场。顺手把这个群登记进池子（机器人只能在有人说话时才知道群的 openid）。"""
    if not roles.find_group_binding(bot.self_id, event.group_openid):
        pool.note_group(bot.self_id, event.group_openid)
    res = rooms.note_official_speech(bot.self_id, event.group_openid, event.author.member_openid)
    if res and res["complete"]:                         # 最后一个人到了：官方机器人在群里发人齐回执
        await _receipt_all_here(rooms.get(res["room_id"]))


@nonebot.on_type(GroupAddRobotEvent, priority=9, block=False).handle()
async def _added(bot: Bot, event: GroupAddRobotEvent):
    pool.note_group(bot.self_id, event.group_openid)


set_pool = _cmd("设为备用群池", {"设为群池"})          # 老写法：设为备用群池 组名 群号（群号可以以后在后台补）
set_backup = _cmd("设为备用群")                        # 新写法：设为备用群 群号 [组名]，群号必填


def _pool_args(text: str) -> tuple[str, str]:
    """参数里 5-12 位数字的是群号，其余的是组名（两种写法顺序不同，都认）。"""
    gid, rest = "", []
    for p in text.split():
        if not gid and handover.GID_RE.fullmatch(p):
            gid = p
        else:
            rest.append(p)
    return gid, " ".join(rest)[:20]


async def _take_over(bot: Bot, event: GroupMessageCreateEvent, gid: str, set_name: str) -> str:
    """把这个群交给官方机器人。小号也管着这个群（在线）时，先让小号把群名改回空闲名。返回回复文字。"""
    from changri.core.db import use_bot
    why = handover.check_to_official(bot.self_id, event.group_openid, gid)
    if why:
        return f"❌ {why}"
    a = handover.alt_row(bot.self_id, gid)
    from_qq = bool(a and not a["moved"])
    name = qqpool.free_name(set_name or (a["set_name"] if a else ""))
    renamed = False
    if from_qq:
        with use_bot(bot.self_id):
            renamed = not await qqmanager.rename(bot.self_id, gid, name)
    ok, why = handover.to_official(bot.self_id, event.group_openid, gid, set_name)
    if not ok:
        return f"❌ {why}"
    g = pool.is_pool_group(bot.self_id, event.group_openid)
    head = (f"✅ 群 {gid} 已设为官方机器人的备用群" + (f"（组「{g['set_name']}」）" if g["set_name"] else "") +
            f"。请核对一下 {gid} 是不是这个群的群号，写错了玩家会进不来（发错了就再发一次正确的）。")
    if from_qq:
        head += "它原来在 QQ 小号的群号组里，已经从那边移出，以后由官方机器人在这个群里记复盘、处理指令。"
    tail = (f"\n群名已经由小号改成「{name}」。官方机器人改不了群名，以后会一直保持这个名字。" if renamed
            else "\n" + handover.RENAME_ASK.format(name=name))
    return head + tail + "\n请群主在群设置里给官方机器人打开「获取群内全部消息」和「主动在群聊内发言」。"


@set_backup.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await set_backup.finish(NOT_ADMIN)
    gid, set_name = _pool_args(args.extract_plain_text())
    if not gid:
        await set_backup.finish("用法：在要当备用群的这个群里发「设为备用群 本群群号」，比如「设为备用群 123456789」。群号一定不要写错，玩家靠它申请入群。想分组就在后面加组名。")
    await set_backup.finish(await _take_over(bot, event, gid, set_name))


@set_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await set_pool.finish(NOT_ADMIN)
    real, set_name = _pool_args(args.extract_plain_text())
    if real:                                           # 带了群号：跟「设为备用群」一样（会从小号那边转过来）
        await set_pool.finish(await _take_over(bot, event, real, set_name))
    pool.register(bot.self_id, event.group_openid, real_gid="", set_name=set_name)
    ok, why = pool.set_in_pool(pool.is_pool_group(bot.self_id, event.group_openid)["id"], True)
    if not ok:
        await set_pool.finish(f"❌ {why}。")
    await set_pool.finish("✅ 这个群已放进备用群池" + (f"（组「{set_name}」）" if set_name else "") + "。群号还没填，请在设置平台「群池」页补上，玩家要靠它申请入群。")


to_qq_cmd = _cmd("转为小号群", {"设为小号群"})


@to_qq_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """官方机器人的备用群交给 QQ 小号：先确认小号此刻真在这个群里，再转；小号顺手把群名改成空闲名（改成了说明它是管理员）。"""
    if not _is_admin(bot, event):
        await to_qq_cmd.finish(NOT_ADMIN)
    gid, why = handover.check_new_qq(bot.self_id, event.group_openid)
    if why:
        await to_qq_cmd.finish(f"❌ {why}。")
    lid, why = await handover.alt_link_in_group(bot.self_id, gid)
    if not lid:
        await to_qq_cmd.finish(f"❌ 没转：{why}。")
    ok, why, set_name = handover.to_new_qq(bot.self_id, event.group_openid, args.extract_plain_text().strip(), lid)
    if not ok:
        await to_qq_cmd.finish(f"❌ {why}。")
    name = qqpool.free_name(set_name)
    err = await qqmanager.rename(bot.self_id, gid, name)
    tail = (f"小号已把群名改成「{name}」。" if not err else
            f"小号没能改群名，多半还不是管理员：请群主把小号设为管理员（{err}）。")
    await to_qq_cmd.finish(f"✅ 群 {gid} 已交给 QQ 小号管（组「{set_name}」），以后由小号改群名、查人、记复盘。{tail}\n"
                           "想再交回官方机器人：发「设为备用群 本群群号」，或者在后台点「转到官方备用群」。")


release_pool = _cmd("放回备用群", {"放回群池"})


@release_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """管理员确认群里人都退光了：在这个群里发「放回备用群」，或者在别的群发「放回备用群 群号」，群就回到可用的备用群里。"""
    if not _is_admin(bot, event):
        await release_pool.finish(NOT_ADMIN)
    gid = args.extract_plain_text().strip()
    if gid:
        with get_conn() as c:
            g = c.execute("SELECT * FROM pool_groups WHERE bot_id=? AND real_gid=? AND group_openid NOT LIKE 'qq:%' AND moved=0",
                          (bot.self_id, gid)).fetchone()
        where = f"群 {gid}"
    else:
        g = pool.is_pool_group(bot.self_id, event.group_openid)
        where = "这个群" + (f"（{g['real_gid']}）" if g and g["real_gid"] else "")
    if not g:
        await release_pool.finish(f"{where}不是官方机器人的备用群。" + ("" if gid else "要放回别的群，发「放回备用群 群号」。"))
    if g["state"] == "occupied":
        await release_pool.finish(f"{where}还在用（R{g['room_id']}），房间结束后再放回。")
    if g["state"] == "free":
        await release_pool.finish(f"{where}已经是空闲的备用群了，不用放回。")
    pool.release(g["id"], "free")
    await release_pool.finish(f"✅ {where}已放回备用群，可以分给下一场了。")


unset_pool = _cmd("取消备用群池", {"取消群池"})


@unset_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await unset_pool.finish(NOT_ADMIN)
    g = pool.is_pool_group(bot.self_id, event.group_openid)
    if not g or not g["in_pool"]:
        await unset_pool.finish("这个群不在备用群池里。")
    ok, why = pool.set_in_pool(g["id"], False)
    await unset_pool.finish("✅ 已移出备用群池。" if ok else f"❌ {why}。")


show_pool = _cmd("查看备用群池", {"查看群池"})


@show_pool.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await show_pool.finish(NOT_ADMIN)
    c = pool.counts()
    await show_pool.finish(f"备用群池：空闲 {c['free']}，占用 {c['occupied']}，待清场 {c['cleaning']}。\n详细管理在设置平台「群池」页。")


# ================= 发起房间 =================

async def start_room(kind: str, me, targets: list, time_text: str, place: str = "", note: str = "") -> tuple[bool, str, int]:
    """发起约会/电话/微信并通知对方（QQ 指令和网页小手机共用）。返回 (成功, 文字, 房间号)。"""
    from changri.core import letters                  # 写信综开着、设了「发起约会要几个写信币」：不够就不让发，开成了才扣
    why = letters.check_cost(me["id"], "date") if kind == "约会" else ""
    if why:
        return False, why, 0
    ok, info, room_id = rooms.create(kind, me["id"], [t["id"] for t in targets], time_text, place, note)
    if room_id and not await _ensure_clean(room_id):
        return False, NO_GROUP_TEXT.format(kind=kind), 0
    if not ok:
        return False, info, 0
    room = rooms.get(room_id)
    for t in targets:
        await _invite(t["id"], room, me["name"])
    await _announce_in_room(room_id)
    refused = info.partition("\n")[2]           # 多人邀约里有人拉黑了你：create 在提示后面附了一行
    paid = letters.pay(me["id"], "date", f"发起约会 R{room_id}") if kind == "约会" else ""
    refused = "\n".join(x for x in (refused, paid) if x)
    if kind in rooms.SOLO_KINDS:
        return True, f"✅ {kind}已发起，房间号 R{room_id}（只有你一个人）。\n{_slot(room)}\n{_group_line(room)}\n聊完在群里发「结束{kind}」。", room_id
    return True, f"✅ {kind}已发起，房间号 R{room_id}。\n{_slot(room)}\n{_group_line(room)}\n对方已收到通知。" + (f"\n{refused}" if refused else ""), room_id


async def notify_join_request(me, room, qid: int) -> None:
    for m in rooms.members(room["id"]):
        await _push(m["role_id"], f"🙋 {me['name']} 想加入 R{room['id']} {room['kind']}（{_slot(room)}）。\n同意发「同意加入 {qid}」，拒绝发「拒绝加入 {qid}」（「加入请求」可以看全部）。")


async def notify_join_answer(me, q, accept: bool) -> None:
    room = rooms.get(q["room_id"])
    if accept:
        await _push(q["role_id"], f"✅ {me['name']} 同意了你加入 R{room['id']} {room['kind']}。\n{_slot(room)}")
        await _push_group(q["role_id"], room)
    else:
        await _push(q["role_id"], f"❌ 你加入 R{room['id']} 的申请被拒绝了。")


async def _start_room(matcher, bot: Bot, event: GroupMessageCreateEvent, kind: str, time_text: str, place: str, names: list[str]):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    if re.fullmatch(r"[Dd]\d+", time_text):          # 「约会 D2 1400-1500 …」：玩家只能约当天，不然后面的字整体错位，报成「找不到角色」
        await matcher.finish(f"❌ {kind}只能约当天，前面不用写 {time_text.upper()}。\n"
                             f"想约到别的天：先约当天，再在房间里发「修改时间线 R房间号 {time_text.upper()} 新时间」，所有人同意后生效。")
    targets, missing = _resolve(names)
    if missing:
        await matcher.finish(f"❌ 找不到角色：{missing}。")
    ok, text, _rid = await start_room(kind, me, targets, time_text, place)
    await matcher.finish(text if ok else f"❌ {text}")


private = _cmd("约会", {"私约"})                 # 0.21 起私约改名约会，老叫法照样认


@private.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    p = args.extract_plain_text().split(None, 2)
    if len(p) < 3:
        await private.finish(f"用法：约会 时间 地点 对方（多人用 / 隔开）\n例：约会 {storycal.example_time()} 咖啡厅 张三/李四")
    await _start_room(private, bot, event, "约会", p[0], p[1], _names(p[2]))


phone = _cmd("电话")


@phone.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    p = args.extract_plain_text().split(None, 1)
    if len(p) < 2:
        await phone.finish(f"用法：电话 时间 对方（多人用 / 隔开）\n例：电话 {storycal.example_time()} 张三")
    await _start_room(phone, bot, event, "电话", p[0], "", _names(p[1]))


stakeout = _cmd("踩点")


@stakeout.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not rooms.stakeout_enabled():
        await stakeout.finish("❌ 踩点没有开放。")
    p = args.extract_plain_text().split(None, 1)
    if len(p) < 2:
        await stakeout.finish(f"用法：踩点 时间 地点（一个人的群，不带别人）\n例：踩点 {storycal.example_time()} 大图书馆")
    await _start_room(stakeout, bot, event, "踩点", p[0], p[1].strip(), [])


wechat = _cmd("微信")


@wechat.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await wechat.finish("多人群聊已经搬到小手机里了：小手机 → 发现 → 邀约 → 发起多人群聊（需要管理员在小手机设置里打开）。")


_EMPTY_PLACE = {"无", "空", "-", "—"}


def parse_official(text: str, call: bool = False):
    """「[D几] 时间 [地点] 名字/名字」→ (day, 时间, 地点, 名字列表)；地点可以不写（官电本来就没有）。格式不对返回 None。"""
    p = text.split()
    day = None
    if p and re.fullmatch(r"[Dd]\d+", p[0]):
        day, p = int(p[0][1:]), p[1:]
    if not p or not rooms.parse_hm(p[0], day):
        return None
    t, rest = p[0], p[1:]
    if not rest:
        return None
    place = ""
    if not call and len(rest) >= 2 and not roles.get_role_by_name(rest[0]):       # 第一段本身是角色名就当没写地点
        place, rest = rest[0], rest[1:]
    if place in _EMPTY_PLACE:
        place = ""
    names = _names(" ".join(rest))
    return (day, t, place, names) if names else None


async def launch_official(targets: list, time_text: str, place: str = "", day: int | None = None, call: bool = False) -> tuple[bool, str, int]:
    """发起官约/官电并通知所有人（QQ 指令和后台页面共用）。返回 (成功, 给管理员看的文字, 房间号)。"""
    label = "官电" if call else "官约"
    if len(targets) < 2:
        return False, f"{label}至少要两位角色。", 0
    kind = "电话" if call else "官约"
    ok, info, room_id = rooms.create(kind, targets[0]["id"], [t["id"] for t in targets[1:]], time_text, "" if call else place, "官电" if call else "", official=True, day=day)
    if room_id and not await _ensure_clean(room_id):
        return False, NO_GROUP_TEXT.format(kind=kind), 0
    if not ok:
        return False, info, 0
    room = rooms.get(room_id)
    for t in targets:
        await _invite(t["id"], room, fix=("💌 电话", "📞 官电") if call else ("💌 官约", "🏢 官约"))
    await _announce_in_room(room_id)
    return True, f"✅ {label}已发起，房间号 R{room_id}，已通知 {len(targets)} 人。\n{_slot(room)}\n{_group_line(room)}", room_id


OFFICIAL_USAGE = ("用法：发起官约 [D几] 时间 [地点] 名字/名字（至少两人；地点可以不写，D几 不写就是今天）\n"
                  f"例：发起官约 {storycal.example_time()} 后山 张三/李四\n例：发起官约 D3 {storycal.example_time()} 张三/李四（不写地点）")
OFFICIAL_CALL_USAGE = "用法：发起官电 [D几] 时间 名字/名字（至少两人）\n例：发起官电 D2 2000-2100 张三/李四"

official = _cmd("发起官约")


@official.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await official.finish(NOT_ADMIN)
    parsed = parse_official(args.extract_plain_text())
    if not parsed:
        await official.finish(OFFICIAL_USAGE)
    day, t, place, names = parsed
    targets, missing = _resolve(names)
    if missing:
        await official.finish(f"❌ 找不到角色：{missing}。")
    ok, text, _rid = await launch_official(targets, t, place, day)
    await official.finish(text if ok else f"❌ {text}")


official_phone = _cmd("发起官电")


@official_phone.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await official_phone.finish(NOT_ADMIN)
    parsed = parse_official(args.extract_plain_text(), call=True)
    if not parsed:
        await official_phone.finish(OFFICIAL_CALL_USAGE)
    day, t, _place, names = parsed
    targets, missing = _resolve(names)
    if missing:
        await official_phone.finish(f"❌ 找不到角色：{missing}。")
    ok, text, _rid = await launch_official(targets, t, "", day, call=True)
    await official_phone.finish(text if ok else f"❌ {text}")


def _plans() -> list[dict]:
    try:
        return json.loads(get_config("official_plans", "[]"))
    except ValueError:
        return []


def _plan_line(i: int, p: dict) -> str:
    return f"{i}. {'D' + str(p['day']) + ' ' if p.get('day') else ''}{p['time']}{' @' + p['place'] if p['place'] else ''} {p['names']}"


plan_cmd = _cmd("计划官约")
run_plan = _cmd("执行官约")


@plan_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await plan_cmd.finish(NOT_ADMIN)
    t = args.extract_plain_text().strip()
    if t in ("", "查看"):
        ps = _plans()
        await plan_cmd.finish("📝 已计划的官约\n" + "\n".join(_plan_line(i + 1, p) for i, p in enumerate(ps)) + "\n发「执行官约」一次性发起。" if ps else "还没有计划的官约。写法：计划官约 [D几] 时间 [地点] 名字/名字")
    if t.startswith("清空"):
        set_config("official_plans", "[]")
        await plan_cmd.finish("✅ 已清空计划。")
    parsed = parse_official(t)
    if not parsed:
        await plan_cmd.finish(f"用法：计划官约 [D几] 时间 [地点] 名字/名字\n例：计划官约 D2 {storycal.example_time()} 后山 张三/李四\n（先攒着，发「执行官约」一起发起；「计划官约 清空」清掉）")
    day, tm, place, names = parsed
    targets, missing = _resolve(names)
    if missing or len(targets) < 2:
        await plan_cmd.finish(f"❌ {'找不到角色：' + missing if missing else '官约至少要两位角色。'}")
    set_config("official_plans", json.dumps(_plans() + [{"day": day, "time": tm, "place": place, "names": "/".join(names)}], ensure_ascii=False))
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
        ok, info, rid = await launch_official(targets, p["time"], p["place"], p.get("day"))
        if ok:
            done.append(f"R{rid} {_plan_line(0, p)[3:]}")
        else:
            fail.append(f"{_plan_line(0, p)[3:]}：{info}")
            left.append(p)
    set_config("official_plans", json.dumps(left, ensure_ascii=False))     # 失败的留着，改好再执行
    await reply_long(run_plan, f"✅ 已发起 {len(done)} 场\n" + "\n".join(done) + (f"\n\n❌ 失败 {len(fail)} 场（保留在计划里）\n" + "\n".join(fail) if fail else ""))


async def _finish_room(room_id: int, status: str, why: str, timeout: bool = False) -> None:
    """timeout=True：「到时间强制结群」到点了（文案不一样：结戏限时已到）。"""
    room = rooms.get(room_id)
    mem = rooms.members(room_id)
    gi = rooms.group_info(room) if room else None
    g = rooms.end(room_id, status)
    verb = "已经结束" if status == "ended" else "已经取消"
    if status != "ended":                                                # 正常结束不再往个人群发通知；取消、超时这类参与者不一定在场，才通知
        for m in mem:
            await _push(m["role_id"], f"🔚 R{room_id} {room['kind']}{verb}{why}。" + ("请退出那个群，管理员清场后才能再给别人用。" if gi else ""))
    if status == "ended":
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
    if gi and gi[1].startswith(pool.QQ_PREFIX):                          # 房间群：群名改回「长日备用」（可在「QQ 小号与群号组（双骰）」页改）
        await qqmanager.room_rename(gi[0], pool.get(room["pool_id"]), qqpool.free_name(pool.get(room["pool_id"])["set_name"]))
    if gi and gi[1].startswith(pool.QQ_PREFIX):                          # 小号的群：小号会自己查人，直接放回池
        if timeout:
            await _to_group(gi[0], gi[1], "结戏限时已到~请及时离开！")
        else:
            await _to_group(gi[0], gi[1], f"{room['kind']}{verb}{why}，请退群！")
    elif gi:                                                             # 官方机器人的群：查不了人，等管理员确认退光了再放回
        head = "结戏限时已到~" if timeout else f"{room['kind']}{verb}{why}，"
        await _to_group(gi[0], gi[1], head + "请退群！管理员确认人都退了，这个群才会给下一场用。")
        staff = announce.get_backstage()                                 # 房间结束不往公告群发任何东西（Erin 要求）；清场提醒只发后台群，没设就不发
        if staff:
            await _to_group(staff[0], staff[1], f"🧹 {gi[2]}（R{room_id} 用过）在等人退群：到群里看一眼，人都退光了，"
                                                f"在那个群里发「放回备用群」（或者在这里发「放回备用群 群号」），也可以到后台「纯官方模式」点「人已退光，放回池」，它才会分给下一场。")


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


async def _sweep_cleaning() -> None:
    """等人退群的群：除了群主、管理员和我们的 QQ 号，群里没别人了（绑没绑 QQ 都算）就自动放回池。"""
    for g in pool.all_groups():
        if g["state"] != "cleaning" or not g["in_pool"] or not pool.is_qq_only(g):
            continue
        others = await qqmanager.group_others(g["bot_id"], g["real_gid"])
        if others is not None and not others:
            pool.release(g["id"], "free")


review_cmd = _cmd("复盘")


@review_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    """发「复盘 R12」：把那一场房间的戏文画成图发出来（只有参与者和管理员能看）。不带编号就看我最近一场。"""
    from changri.core import roomlog
    from changri.core.qqsend import IMG
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
        await review_cmd.finish("这场没有记到戏文。戏文要按「角色名：正文」的格式写；房间群里要有卡拉（开着「接收全部消息」），或者有监听的 QQ 号（说话的人要绑定过 QQ）。")
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
        await backfill_cmd.finish("补录没有打开（管理员在设置平台「QQ 小号与群号组（双骰）」页打开）。")
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
    m = re.fullmatch(r"[Rr]?(\d+)", text.strip())
    return int(m[1]) if m else None


force_end = _cmd("强结约会", {"强结", "强结私约"})


@force_end.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await force_end.finish(NOT_ADMIN)
    rid = _room_arg(args.extract_plain_text())
    room = rooms.get(rid) if rid else None
    if not room or room["status"] != "open":
        await force_end.finish("用法：强结约会 R房间号（只能结束进行中的）。")
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
    await notify_join_request(me, r, qid)
    await join_cmd.finish("✅ 申请已发出，等对方回复。")


@join_list.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await join_list.finish(NEED_ROLE)
    qs = rooms.pending_requests(me["id"])
    if not qs:
        await join_list.finish("没有待处理的加入申请。")
    text = "🙋 收到的加入申请\n" + "\n".join(f"#{q['id']} {q['applicant']} 想加入 R{q['room_id']} {q['kind']}（D{q['day']} {q['start_hm']}-{q['end_hm']}）" for q in qs)
    btns = [b for q in qs for b in (Btn(f"同意 #{q['id']} {q['applicant']}", f"同意加入 {q['id']}"), Btn(f"拒绝 #{q['id']}", f"拒绝加入 {q['id']}", style="danger"))]
    await reply_buttons(join_list, text + "\n同意发「同意加入 编号」，拒绝发「拒绝加入 编号」", btns, per_row=2)
    await join_list.finish()


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
    await notify_join_answer(me, q, accept)
    await matcher.finish("✅ 已同意，对方会收到群号。" if accept else "已拒绝。")


@join_yes.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _answer(join_yes, bot, event, args, True)


@join_no.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _answer(join_no, bot, event, args, False)


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
        lines.append(f"  {r['start_hm']}-{r['end_hm']} {r['kind']}{' @' + r['place'] if r['place'] else ''} 与 {others}（R{r['id']} {st}）")
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
        await del_slot.finish("❌ 权限不足，仅管理员可用。")
    p = args.extract_plain_text().split()
    if len(p) != 2 or not p[1].upper().lstrip("R").isdigit():
        await del_slot.finish("用法：删除时间线 角色名 R房间号（房间号在「时间线 角色名」里看）\n会把这个房间连同所有人的时间线和复盘记录一起删掉。")
    who = roles.get_role_by_name(p[0])
    if not who:
        await del_slot.finish(f"❌ 找不到角色「{p[0]}」。")
    from changri.core import godmode
    ok, msg = godmode.delete_slot(who["id"], int(p[1].upper().lstrip("R")))
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
    return f"D{room['day']} {room['start_hm']}-{room['end_hm']}"


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
    vote_btns = [Btn("同意", f"同意时间线 R{r}"), Btn("拒绝", f"拒绝时间线 R{r}", style="danger")]
    others = [m for m in rooms.members(r) if m["role_id"] != me["id"]]
    if here and here["id"] == r:            # 在房间群里：大家都看得到，直接在群里挂按钮
        await reply_buttons(chg, _propose_in_room(me, room, others, day, hm),
                            [Btn("同意时间线", "同意时间线"), Btn("拒绝时间线", "拒绝时间线", style="danger")], per_row=2)
        await chg.finish()
    for m in others:                        # 在个人群发的：房间里的人看不到，推到他们各自的个人群
        await _push(m["role_id"], f"📝 {me['name']} 申请把 R{r} {room['kind']}的时间线改成 {new}（原来 {old}）。\n"
                                  f"同意发「同意时间线 R{r}」，不同意发「拒绝时间线 R{r}」。全部同意后生效。", buttons=vote_btns)
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
            f"要 {'、'.join(m['name'] for m in others)} 都发「同意时间线」才生效；不同意发「拒绝时间线」，申请作废、时间不变。")


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
    await matcher.finish(await _vote_result(room, me, agree, rooms.room_of_group(bot.self_id, event.group_openid) is not None))


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


# ================= 定时：过期结束、给还没分到群的房间补群 =================

@scheduler.scheduled_job("interval", seconds=30, id="rooms_tick", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    if not nonebot.get_bots():
        return
    # 每一步单独接住错误：一步出错（比如某个群查成员时连接断了）不能让后面补群、查到场整轮都跳过
    for step in (_tick_outbox, _warn_expiring, _tick_expired, _sweep_cleaning, _tick_assign, _check_presence, _tick_pool_alert):
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
        for m in rooms.members(rid):
            await _push(m["role_id"], f"🔔 R{rid} {room['kind']}有空群了。\n{_slot(room)}")
            await _push_group(m["role_id"], room)
        await _announce_in_room(rid)


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
        await say(_propose_in_room(me, room, others, day, hm) if ok else f"❌ {why}")
    else:
        await say(await _vote_result(room, me, cmd == "同意时间线", True))
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


# ================= 复盘：官方机器人在房间群里收到的发言 =================

@on_message(priority=30, block=False).handle()
async def _record_official(bot: Bot, event: GroupMessageCreateEvent):
    """官方机器人在房间群里（大家默认开了「接收全部消息」）收到的发言记进复盘，不接 QQ 号也能复盘、统计字数。
    优先级比所有指令都低、不拦截：被指令接走的话不会走到这里。一个群里只会有卡拉或小号其中一个，不会两边都记。"""
    try:
        from changri.core import roomlog
        roomlog.record_official(bot.self_id, event.group_openid, event.author.member_openid,
                                event.get_plaintext(), event_id=str(getattr(event, "id", "") or ""))
    except Exception as e:  # noqa: BLE001 复盘出错不能影响别的功能
        logger.warning(f"[复盘·卡拉] 记录出错：{e}")
