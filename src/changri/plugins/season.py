"""第 3 步：季度与游戏天数（指令 + 定时）。规则在 core/season.py，这里只解析指令和回话。"""
import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from changri.core import admins, npc, season
from changri.core.announce import get_announce
from changri.core.db import get_config, per_bot, set_config
from changri.core.qqsend import make_send

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

NO = "❌ 权限不足，仅管理员可用。"


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _is_admin(event: GroupMessageCreateEvent) -> bool:
    return admins.is_admin(current_bot.get().self_id, event.author.member_openid)


def _status() -> str:
    s = season.running()
    p = season.phase()
    if p == "none":
        nxt = season.booked()
        tail = "预订中：" + "、".join(f"「{b['name']}」{b['start_date']}~{b['end_date']}" for b in nxt) if nxt else "还没有预订，发「预订季度 名字 MMDD-MMDD」或去设置平台「季度」页。"
        return f"📆 现在没有季度。\n{tail}\n下一步：发「开始季度」。"
    if p == "ended":
        return "📆 上一季已结束，还没收尾。\n下一步：核对存档后发「收尾」（先预览，再「收尾 确认」）；也可以直接「开始季度 确认」。"
    label = {"prep": "筹备期（开始日到了自动进 D0）", "playing": f"进行中（{season.day_label()}）", "over": f"档期已过（天数停在 {season.day_label()}）"}[p]
    lines = [f"📆 「{s['name']}」{label}",
             f"档期：{s['start_date']} ~ {s['end_date']}" + (f"，补戏到 {s['supp_end']}" if s["supp_end"] else ""),
             f"自动天数：{'开' if get_config('auto_day') == '1' else '关'}；心动信：{'开' if get_config('lovemail_enabled', '1') != '0' else '关'}"]
    from changri.core import npc as npc_core
    if npc_core.names():
        lines.append("NPC：" + "、".join(npc_core.names()))
    lines.append("下一步：" + {"prep": "玩家创建角色，等开始日。", "playing": "正常进行；结束时发「结束季度」。", "over": "发「结束季度」。"}[p])
    return "\n".join(lines)


book_cmd = _cmd("预订季度")


@book_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(event):
        await book_cmd.finish(NO)
    parts = args.extract_plain_text().split()
    usage = "用法：预订季度 名字 MMDD-MMDD [补戏MMDD]\n例：预订季度 长日将尽 1115-1120 补戏1123"
    if len(parts) < 2:
        await book_cmd.finish(usage)
    rng = season.parse_range(parts[1])
    if not rng:
        await book_cmd.finish("❌ 档期格式不对，要写成 MMDD-MMDD，例如 1115-1120。\n" + usage)
    rest = parts[2:]
    supp = ""
    if rest and (rest[0].startswith("补戏") or (len(rest[0]) == 4 and rest[0].isdigit())):
        supp = season.parse_mmdd(rest[0]) or ""
        if not supp:
            await book_cmd.finish("❌ 补戏日期格式不对，写成 补戏1123。")
        rest = rest[1:]
    ok, msg = season.book(parts[0], rng[0], rng[1], supp)
    await book_cmd.finish(("✅ " if ok else "❌ ") + msg)


start_cmd = _cmd("开始季度")


@start_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(event):
        await start_cmd.finish(NO)
    parts = args.extract_plain_text().split()
    clear = "确认" in parts
    name = next((p for p in parts if p != "确认"), "")
    ok, msg = season.start(name, clear=clear)
    if not ok and msg == "NEEDS_WIPE":
        await start_cmd.finish(f"⚠️ 上季的角色和互动记录还没清空（{', '.join(f'{k}{v}' for k, v in season.wipe_preview().items())}）。\n"
                               "先发「收尾」，或发「开始季度 确认」自动清空后开始。")
    await start_cmd.finish(msg if ok else "❌ " + msg)


end_cmd = _cmd("结束季度")


@end_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await end_cmd.finish(NO)
    ok, msg = await season.end_and_report(make_send())
    await end_cmd.finish(msg if ok else "❌ " + msg)


wipe_cmd = _cmd("收尾")


@wipe_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(event):
        await wipe_cmd.finish(NO)
    if season.running():
        await wipe_cmd.finish("❌ 季度还在进行中，先「结束季度」。")
    if args.extract_plain_text().strip() != "确认":
        pv = "、".join(f"{k}{v}" for k, v in season.wipe_preview().items())
        await wipe_cmd.finish(f"⚠️ 收尾会清空：{pv}。\n管理员、设置、季度记录会保留。确认请发「收尾 确认」。")
    season.wipe()
    await wipe_cmd.finish("✅ 已收尾，本季的角色和互动记录已清空。")


status_cmd = _cmd("季度状态")


@status_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await status_cmd.finish(NO)
    await status_cmd.finish(_status())


sched_cmd = _cmd("修改档期")


@sched_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(event):
        await sched_cmd.finish(NO)
    parts = args.extract_plain_text().split()
    rng = season.parse_range(parts[0]) if parts else None
    if not rng:
        await sched_cmd.finish("用法：修改档期 MMDD-MMDD [补戏MMDD]")
    supp = season.parse_mmdd(parts[1]) if len(parts) > 1 else ""
    if len(parts) > 1 and not supp:
        await sched_cmd.finish("❌ 补戏日期格式不对，写成 补戏1123。")
    ok, msg = season.update_schedule(rng[0], rng[1], supp or "")
    await sched_cmd.finish(("✅ " if ok else "❌ ") + msg)


day_cmd = _cmd("设置天数")


@day_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(event):
        await day_cmd.finish(NO)
    t = args.extract_plain_text().strip().upper()
    if not (t.startswith("D") and t[1:].isdigit()):
        await day_cmd.finish("用法：设置天数 D1")
    if not season.running():
        await day_cmd.finish("❌ 现在没有进行中的季度，先「开始季度」。")
    season.set_day(int(t[1:]))
    await day_cmd.finish(f"✅ 现在是 {t}，今天的投稿次数等计数已重新开始。")


auto_on = _cmd("开启自动天数")


@auto_on.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await auto_on.finish(NO)
    set_config("auto_day", "1")
    set_config("auto_day_last", season.today())
    await auto_on.finish("✅ 自动天数已开启：每天 23:59 加一天并重新计数。")


auto_off = _cmd("关闭自动天数")


@auto_off.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await auto_off.finish(NO)
    set_config("auto_day", "0")
    await auto_off.finish("⏸️ 自动天数已关闭。")


npc_set = _cmd("设为NPC", {"设为npc"})
npc_unset = _cmd("取消NPC", {"取消npc"})
npc_new = _cmd("创建NPC", {"创建npc"})
npc_list = _cmd("NPC列表", {"npc列表", "查看NPC", "查看npc"})


async def _npc_op(matcher, event, args, usage, fn):
    if not _is_admin(event):
        await matcher.finish(NO)
    name = args.extract_plain_text().strip()
    if not name:
        await matcher.finish(usage)
    ok, msg = fn(name)
    await matcher.finish(("✅ " if ok else "❌ ") + msg)


@npc_set.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    await _npc_op(npc_set, event, args, "用法：设为NPC 角色名", lambda n: npc.set_flag(n, True))


@npc_unset.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    await _npc_op(npc_unset, event, args, "用法：取消NPC 角色名", lambda n: npc.set_flag(n, False))


@npc_new.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    await _npc_op(npc_new, event, args, "用法：创建NPC 名字（建出来的 NPC 没有个人群）", npc.create)


@npc_list.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await npc_list.finish(NO)
    ns = npc.listing()
    await npc_list.finish("🎭 NPC：" + "、".join(f"{n['name']}{'' if n['groups'] else '（无群）'}" for n in ns) if ns else "现在没有 NPC。用「创建NPC 名字」或「设为NPC 角色名」。")


@scheduler.scheduled_job("interval", seconds=30, id="season_tick", max_instances=1, coalesce=True)
@per_bot
async def _season_tick():
    if not nonebot.get_bots():
        return
    try:
        r = await season.tick(make_send(), get_announce())
        if r != "idle":
            logger.info(f"[季度] {r}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[季度] 定时任务出错：{e}")
