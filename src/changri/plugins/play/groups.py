"""备用群池管理（管理员）：设为备用群、转为小号群、放回、取消、查看；机器人进群时记一笔。"""

import nonebot
from nonebot import on_message
from nonebot.adapters.qq import Bot, GroupAddRobotEvent, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import handover, pool, qqmanager, qqpool, roles, rooms
from changri.core.db import get_conn

from .common import NOT_ADMIN, _cmd, _is_admin
from .ticks import _receipt_all_here


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
    pid = pool.register(bot.self_id, event.group_openid, real_gid="", set_name=set_name)
    ok, why = pool.set_in_pool(pid, True)
    if not ok:                                         # 个人群 / 公告群这类：register 已经顺手放进池了，撤回来
        with get_conn() as c:
            c.execute("UPDATE pool_groups SET in_pool=0 WHERE id=?", (pid,))
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
