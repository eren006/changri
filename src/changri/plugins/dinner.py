"""晚餐（额外扩展，默认关闭）：管理员「开始晚餐 座位数 [风格]」，玩家「入座 座位号」，相邻座位「戳你一下 角色名」。规则在 core/dinner.py。
晚餐上的小游戏（开了晚餐就能玩）：围猎、巡逻，规则在 core/dinner_games.py。"""
import asyncio

import nonebot
from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg
from nonebot_plugin_apscheduler import scheduler

from changri.core import dinner, dinner_games as games, drinking, messaging, roles
from changri.core.db import per_bot
from nonebot.adapters.qq import MessageSegment

from changri.core.qqsend import make_send, make_send_image, reply_long
from changri.plugins.play import NEED_ROLE, _cmd, _is_admin, _me

start_cmd = _cmd("开始晚餐")
sit_cmd = _cmd("入座")
end_cmd = _cmd("结束晚餐")
poke_cmd = _cmd("戳你一下")


@start_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await start_cmd.finish("❌ 权限不足，仅管理员可用。")
    p = args.extract_plain_text().split()
    if not p or not p[0].isdigit():
        await start_cmd.finish("用法：开始晚餐 座位数 [风格]\n风格：" + "、".join(dinner.MENUS) + "、无菜\n例：开始晚餐 5 现代西餐")
    ok, msg = dinner.start(int(p[0]), p[1] if len(p) > 1 else "现代中餐")
    await start_cmd.finish(msg if ok else "❌ " + msg)


@sit_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await sit_cmd.finish(NEED_ROLE)
    t = args.extract_plain_text().strip()
    if not t.isdigit():
        await sit_cmd.finish("用法：入座 座位号")
    ok, msg = dinner.sit(me["name"], int(t))
    await sit_cmd.finish(msg if ok else "❌ " + msg)


@end_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await end_cmd.finish("❌ 权限不足，仅管理员可用。")
    hunted = games.hunt_end()
    await end_cmd.finish(("🏁 晚餐已结束。" + ("进行中的围猎也一起结束了。" if hunted else "")) if dinner.stop() else "现在没有进行中的晚餐。")


@poke_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await poke_cmd.finish(NEED_ROLE)
    name = args.extract_plain_text().strip()
    if not name:
        await poke_cmd.finish("用法：戳你一下 对方角色名")
    left = dinner.cooldown_left(me["id"])
    if left:
        await poke_cmd.finish(f"⏳ 你戳得太快了，请等 {(left + 59) // 60} 分钟后再试。")
    why, _ = dinner.poke_check(me["name"], name)
    if why:
        await poke_cmd.finish("❌ " + why)
    target = roles.get_role_by_name(name)
    dinner.mark_poked(me["id"])
    ok, _ = await messaging.push_to_role(target["id"], f"👆 {me['name']} 戳了你一下", make_send()) if target else (False, "")
    await poke_cmd.finish(f"✅ 你戳了 {name} 一下，消息已送达。" if ok else f"你戳了 {name} 一下（TA 那边暂时没收到）。")


# ================= 围猎 =================

hunt_start = _cmd("开始狩猎", {"开始围猎"})
hunt_track = _cmd("追踪")
hunt_trap = _cmd("设陷阱")
hunt_shoot = _cmd("开枪")
hunt_pass = _cmd("观望")
hunt_status = _cmd("狩猎状态", {"围猎状态"})
hunt_rank = _cmd("狩猎排行", {"围猎排行"})
hunt_rank_reset = _cmd("重置狩猎排行")
hunt_skip = _cmd("跳过狩猎", {"狩猎跳过"})
hunt_end = _cmd("结束狩猎", {"结束围猎"})

HUNT_HELP = ("用法：开始狩猎 [轮流] [边长 4-7] [猎物名]\n不写「轮流」就是并发：每轮先侦查、再开火，大家同时出手。"
             "边长不写按入座人数定。要先「开始晚餐」并让大家入座。")


async def _hunt_reply(matcher, text: str) -> None:
    """叙述发文字，地图单独发一张图（不嵌在 Markdown 里）；出不了图就发带文字地图的版本。"""
    rest, had_map = games.split_map(text)
    png = games.hunt_map_png() if had_map else None
    if not png:
        await reply_long(matcher, games.plain(text))
        return
    if rest:
        await reply_long(matcher, rest)
    await matcher.send(MessageSegment.file_image(png))


async def _hunt_push(bot_id: str, group: str, text: str) -> None:
    """定时结算这类主动消息：同样文字一条、地图一张。"""
    rest, had_map = games.split_map(text)
    png = games.hunt_map_png() if had_map else None
    if not png:
        await make_send()(bot_id, group, games.plain(text))
        return
    if rest:
        await make_send()(bot_id, group, rest)
    await make_send_image()(bot_id, group, png)


@hunt_start.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await hunt_start.finish("❌ 权限不足，仅管理员可用。")
    a = args.extract_plain_text().split()
    if a and a[0] in ("帮助", "?", "？"):
        await hunt_start.finish(HUNT_HELP)
    ok, text = games.hunt_start(bot.self_id, event.group_openid, a)
    if not ok:
        await hunt_start.finish("❌ " + text + "\n" + HUNT_HELP)
    await _hunt_reply(hunt_start, text)


async def _hunt_here(matcher, event) -> None:
    g = games.hunt_active()
    if not g:
        await matcher.finish("现在没有进行中的围猎。")
    if g["group"] != event.group_openid:
        await matcher.finish("🏹 围猎在开局的那个群里进行，回那个群再来。")


def _hunt_cmd(matcher, action: str):
    @matcher.handle()
    async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
        me = _me(bot, event)
        if not me:
            await matcher.finish(NEED_ROLE)
        await _hunt_here(matcher, event)
        ok, text, _ended = games.hunt_act(me["name"], action, args.extract_plain_text())
        if not ok:
            await matcher.finish("❌ " + text)
        await _hunt_reply(matcher, text)


_hunt_cmd(hunt_track, "追踪")
_hunt_cmd(hunt_trap, "设陷阱")
_hunt_cmd(hunt_shoot, "开枪")
_hunt_cmd(hunt_pass, "观望")


@hunt_status.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    await _hunt_reply(hunt_status, games.hunt_status())


@hunt_rank.handle()
async def _():
    await hunt_rank.finish(games.hunt_rank())


@hunt_rank_reset.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await hunt_rank_reset.finish("❌ 权限不足，仅管理员可用。")
    games.hunt_rank_reset()
    await hunt_rank_reset.finish("🧹 已清空围猎排行榜。")


@hunt_skip.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await hunt_skip.finish("❌ 权限不足，仅管理员可用。")
    ok, text, _ended = games.hunt_skip()
    if not ok:
        await hunt_skip.finish("❌ " + text)
    await _hunt_reply(hunt_skip, text)


@hunt_end.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await hunt_end.finish("❌ 权限不足，仅管理员可用。")
    await hunt_end.finish("🏁 围猎已结束，这一局不计分。" if games.hunt_end() else "现在没有进行中的围猎。")


@scheduler.scheduled_job("interval", seconds=5, id="hunt_tick", max_instances=1, coalesce=True)
@per_bot
async def _hunt_tick():
    """并发模式到点结算，结果发回开局的群。"""
    if not nonebot.get_bots():
        return
    try:
        due = games.hunt_due()
        if due:
            bot_id, group, text, _ended = due
            await _hunt_push(bot_id, group, text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[围猎] 到点结算出错：{e}")


# ================= 巡逻（捉迷藏淘汰赛） =================

guard_open = _cmd("巡逻开局")
guard_places = _cmd("巡逻设置地点")
guard_join = _cmd("巡逻报名")
guard_hide = _cmd("巡逻躲藏")
guard_call = _cmd("巡逻")
guard_status = _cmd("巡逻状态")
guard_end = _cmd("巡逻结束")


@guard_open.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await guard_open.finish("❌ 权限不足，仅管理员可用。")
    ok, text = games.guard_open(bot.self_id, event.group_openid)
    await guard_open.finish(text if ok else "❌ " + text)


@guard_places.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await guard_places.finish("❌ 权限不足，仅管理员可用。")
    locs = args.extract_plain_text().split()
    if not locs:
        cur = games.guard_locations()
        await guard_places.finish(f"📍 现在的地点（{len(cur)} 个）：{'、'.join(cur)}\n用法：巡逻设置地点 地点1 地点2 …（空格隔开，至少 2 个）")
    ok, text = games.guard_set_locations(locs)
    await guard_places.finish(("✅ " if ok else "❌ ") + text)


@guard_join.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await guard_join.finish(NEED_ROLE)
    ok, text = games.guard_join(me["id"], me["name"])
    await guard_join.finish(text if ok else "❌ " + text)


@guard_hide.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await guard_hide.finish(NEED_ROLE)
    place = args.extract_plain_text().strip()
    if not place:
        await guard_hide.finish("用法：巡逻躲藏 地点（在自己的个人群发，别人看不到）")
    ok, text = games.guard_hide(me["id"], place)
    await guard_hide.finish(text if ok else "❌ " + text)


@guard_call.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await guard_call.finish("❌ 权限不足，仅管理员可用。")
    ok, r = games.guard_call()
    if not ok:
        await guard_call.finish("❌ " + r)
    send = make_send()
    for rid, place in r["caught"]:
        await messaging.push_to_role(rid, f"🚨 第 {r['round']} 轮：你在「{place}」被卫兵逮捕，已淘汰。", send)
        await asyncio.sleep(0.5)
    for rid in r["alive"]:
        tip = "🏆 巡逻赛结束，你是最后的幸存者！" if r["ended"] else (
            f"✅ 第 {r['round']} 轮幸存！选第 {r['round'] + 1} 轮的藏身处：「巡逻躲藏 地点」\n可选：{'、'.join(r['locations'])}")
        await messaging.push_to_role(rid, tip, send)
        await asyncio.sleep(0.5)
    if r["group"] != event.group_openid:
        try:
            await send(r["bot"], r["group"], r["text"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[巡逻] 播报失败：{e}")
        await guard_call.finish("✅ 已结算，结果播报在开局的群里。")
    await guard_call.finish(r["text"])


@guard_status.handle()
async def _():
    await guard_status.finish(games.guard_status())


@guard_end.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await guard_end.finish("❌ 权限不足，仅管理员可用。")
    await guard_end.finish("🧹 已清空巡逻赛。" if games.guard_end() else "现在没有巡逻赛。")


# ================= 喝酒游戏（每个群一局） =================

drink_join = _cmd("加入喝酒游戏")
drink_start = _cmd("开始喝酒游戏")
drink_serve = _cmd("上酒")
drink_cmd = _cmd("喝酒")
drink_left = _cmd("可喝酒")
drink_status = _cmd("喝酒状态", {"喝酒游戏状态"})
drink_list = _cmd("醉酒名单")
drink_end = _cmd("结束喝酒游戏")
drink_reset = _cmd("重置喝酒游戏")
drink_limit = _cmd("设定醉酒阈值")


def _drink_me(matcher):
    @matcher.handle()
    async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
        me = _me(bot, event)
        if not me:
            await matcher.finish(NEED_ROLE)
        g = event.group_openid
        if matcher is drink_join:
            ok, text = drinking.join(g, me["name"])
        else:
            ok, text = drinking.drink(g, me["name"], args.extract_plain_text())
        await matcher.finish(text if ok else "❌ " + text)


_drink_me(drink_join)
_drink_me(drink_cmd)


@drink_start.handle()
async def _(event: GroupMessageCreateEvent):
    ok, text = drinking.start(event.group_openid)
    await drink_start.finish(text if ok else "❌ " + text)


@drink_serve.handle()
async def _(event: GroupMessageCreateEvent):
    ok, text = drinking.serve(event.group_openid)
    if not ok:
        await drink_serve.finish("❌ " + text)
    await reply_long(drink_serve, text)


@drink_left.handle()
async def _(event: GroupMessageCreateEvent):
    await drink_left.finish(drinking.left(event.group_openid))


@drink_status.handle()
async def _(event: GroupMessageCreateEvent):
    await reply_long(drink_status, drinking.status(event.group_openid))


@drink_list.handle()
async def _(event: GroupMessageCreateEvent):
    await drink_list.finish(drinking.drunk_list(event.group_openid))


@drink_end.handle()
async def _(event: GroupMessageCreateEvent):
    ok, text = drinking.end(event.group_openid)
    if not ok:
        await drink_end.finish("❌ " + text)
    await reply_long(drink_end, text)


@drink_reset.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await drink_reset.finish("❌ 权限不足，仅管理员可用。")
    drinking.reset(event.group_openid)
    await drink_reset.finish("🧹 这个群的喝酒游戏已重置。")


@drink_limit.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await drink_limit.finish("❌ 权限不足，仅管理员可用。")
    t = args.extract_plain_text().strip()
    if not t.isdigit():
        await drink_limit.finish(f"用法：设定醉酒阈值 500（现在是 {drinking.threshold()}）")
    ok, text = drinking.set_threshold(int(t))
    await drink_limit.finish(text if ok else "❌ " + text)
