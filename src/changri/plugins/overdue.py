"""扩展：超时提醒。规则在 core/overdue.py；这里只有定时任务（没有群里的指令）。"""
import nonebot
from nonebot import logger

from changri.core import overdue
from changri.core.db import per_bot
from changri.core.qqsend import make_send

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


@scheduler.scheduled_job("interval", seconds=60, id="overdue_tick", max_instances=1, coalesce=True)
@per_bot
async def _overdue_tick():
    if not nonebot.get_bots() or not overdue.enabled():
        return
    try:
        await overdue.push(make_send())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[超时提醒] 定时任务出错：{e}")
