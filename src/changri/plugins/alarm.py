"""加百列闹钟（额外扩展，默认关闭）：提醒、再提醒我、我的提醒、删除提醒；群体闹钟只能在后台页面里设。规则在 core/alarm.py。"""
import asyncio
import re
import time

import nonebot
from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import alarm, messaging
from changri.core.db import per_bot
from changri.core.qqsend import make_send
from changri.plugins.play import NEED_ROLE, _cmd, _me

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

set_cmd = _cmd("提醒")
mine_cmd = _cmd("我的提醒")
del_cmd = _cmd("删除提醒")
snooze_cmd = _cmd("再提醒我")

USAGE = ("格式：提醒 时间 内容\n一次性：提醒 30分钟后 记得喝水\n　　　　提醒 14:30 下午茶\n　　　　提醒 明天09:00 早安\n"
         "重复：　提醒 每天20:00 记得推日报\n　　　　提醒 每周一09:00 周会")


@set_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await set_cmd.finish(NEED_ROLE)
    raw = args.extract_plain_text().strip()
    if not raw:
        await set_cmd.finish(USAGE)
    # 时间里可能带空格（「9月14日 20:00」），从长到短试
    words = raw.split()
    parsed, content = None, ""
    for k in (2, 1):
        if len(words) > k:
            p = alarm.parse_time(" ".join(words[:k]))
            if p:
                parsed, content = p, " ".join(words[k:])
                break
    if not parsed:
        if len(words) < 2:
            await set_cmd.finish("加百列需要知道你想被提醒什么……请在时间后面加上提醒内容。")
        await set_cmd.finish(f"加百列没能理解这个时间：「{words[0]}」\n支持：X分钟后、X小时后、HH:MM、明天HH:MM、每天HH:MM、每周一HH:MM、9月14日 20:00")
    ts, rep = parsed
    if ts <= time.time():
        await set_cmd.finish("那个时间已经过了……可以告诉加百列另一个时间吗？")
    ok, info = alarm.add(me["id"], ts, content, rep)
    if not ok:
        await set_cmd.finish(f"加百列记不了这么多了……{info}")
    lab = alarm.repeat_label(rep)
    await set_cmd.finish(f"🔔 加百列记住了（{info}）\n⏰ {alarm.fmt(ts)}" + (f"\n🔁 {lab}重复" if lab else "") + f"\n📝 {content}")


@mine_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await mine_cmd.finish(NEED_ROLE)
    rows = alarm.mine(me["id"])
    if not rows:
        await mine_cmd.finish("加百列这里还没有你的提醒。\n发送「提醒 时间 内容」，我会记住的。")
    lines = [f"[{r['id']}] {alarm.fmt(r['trigger_ts'])}{' 🔁' + alarm.repeat_label(r['repeat']) if r['repeat'] != 'none' else ''}\n　　{r['content']}" for r in rows]
    await mine_cmd.finish(f"🔔 加百列记住的事（{len(rows)} 条）\n" + "\n".join(lines) + "\n\n删除：发送「删除提醒 编号」")


@del_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await del_cmd.finish(NEED_ROLE)
    aid = args.extract_plain_text().strip().upper()
    if not aid:
        await del_cmd.finish("格式：删除提醒 编号\n编号见「我的提醒」")
    c = alarm.delete(me["id"], aid)
    await del_cmd.finish(f"好，加百列把「{c}」从记忆里划掉了。" if c is not None else f"加百列没有找到编号「{aid}」……请发送「我的提醒」确认一下。")


@snooze_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await snooze_cmd.finish(NEED_ROLE)
    m = re.search(r"\d+", args.extract_plain_text())
    ok, info = alarm.snooze(me["id"], int(m.group()) if m else None)
    await snooze_cmd.finish(info)


@scheduler.scheduled_job("interval", seconds=20, id="alarm_tick", max_instances=1, coalesce=True)
@per_bot
async def _alarm_tick():
    if not nonebot.get_bots():
        return
    send = make_send()
    try:
        for rid, content in alarm.due():
            await messaging.push_to_role(rid, f"🔔 加百列提醒你：\n{content}", send)
        for content in alarm.group_due():
            for rid in alarm.player_ids():
                await messaging.push_to_role(rid, f"📢 群体闹钟：\n{content}", send)
                await asyncio.sleep(1.5)        # 几十个群齐发容易触发风控
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[闹钟] 定时任务出错：{e}")
