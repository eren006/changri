"""扩展：目击系统。规则在 core/sighting.py；这里只有把提示发出去的定时任务（没有群里的指令）。"""
import nonebot
from nonebot import logger

from changri.core import sighting
from changri.core.db import per_bot
from changri.core.qqsend import make_send

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


@scheduler.scheduled_job("interval", seconds=20, id="sighting_tick", max_instances=1, coalesce=True)
@per_bot
async def _sighting_tick():
    if not nonebot.get_bots() or not sighting.enabled():
        return
    try:
        await sighting.push_pending(make_send())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[目击] 定时任务出错：{e}")
