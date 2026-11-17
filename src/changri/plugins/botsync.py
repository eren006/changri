"""设置平台里增删改机器人后，这里每 5 秒对一次：新增的直接连上，改了密钥的断开重连，停用/移除的断开。
设置平台和机器人在同一个进程里，所以不需要重启程序。"""
import asyncio

import nonebot
from nonebot import logger
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq.config import BotInfo
nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from changri.core.botconf import qq_bots_config  # noqa: E402

_started: dict[str, str] = {b["id"]: b["secret"] for b in qq_bots_config()}


def _stop(adapter, bot_id: str) -> None:
    """取消这个机器人的连接任务（适配器没有按机器人记任务，只能从协程的局部变量里认）。"""
    for task in list(adapter.tasks):
        frame = getattr(task.get_coro(), "cr_frame", None)
        bot = frame.f_locals.get("bot") if frame else None
        if bot is not None and getattr(bot, "self_id", None) == bot_id:
            task.cancel()


def _start(adapter, cfg: dict) -> None:
    task = asyncio.create_task(adapter.run_bot_websocket(BotInfo(**cfg)))
    adapter.tasks.add(task)
    task.add_done_callback(adapter.tasks.discard)


@scheduler.scheduled_job("interval", seconds=5, id="bot_sync", max_instances=1, coalesce=True)
async def _sync():
    want = {b["id"]: b for b in qq_bots_config()}
    adapter = nonebot.get_adapter(QQAdapter)
    for bot_id, old_secret in list(_started.items()):
        if bot_id not in want or want[bot_id]["secret"] != old_secret:
            _stop(adapter, bot_id)
            del _started[bot_id]
            logger.info(f"[机器人] 已断开 {bot_id}（密钥改了、停用或移除）")
    await asyncio.sleep(0)               # 让被取消的连接先收尾
    for bot_id, cfg in want.items():
        if bot_id not in _started:
            _start(adapter, cfg)
            _started[bot_id] = cfg["secret"]
            logger.info(f"[机器人] 已连接 {bot_id}")


# ---- 在线更新：新版本跑满 90 秒才算成功（否则启动保护会回滚）；每 6 小时悄悄查一次有没有新版本（顺便匿名上报使用情况） ----
import datetime  # noqa: E402

from changri.core import updater  # noqa: E402
from changri.core.db import set_config  # noqa: E402

scheduler.add_job(updater.confirm_healthy, "date", run_date=datetime.datetime.now() + datetime.timedelta(seconds=90), id="update_confirm")


@scheduler.scheduled_job("interval", hours=6, id="update_check", next_run_time=datetime.datetime.now() + datetime.timedelta(minutes=2))
async def _update_check():
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, updater.check)
        set_config("update_latest", info["version"] if info["newer"] else "")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[更新] 检查失败：{e}")
    try:                                                   # 顺便匿名上报一次使用情况（后台「基础」里能关，见 core/usage.py）
        from changri.core import usage
        from changri.core.db import list_bot_ids
        bots, online = len(list_bot_ids()), len(nonebot.get_bots())
        await asyncio.get_running_loop().run_in_executor(None, usage.report, bots, online)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[使用情况] 上报失败：{e}")


@scheduler.scheduled_job("interval", seconds=30, id="heartbeat", max_instances=1, coalesce=True)
async def _heartbeat():
    """心跳：设置平台靠它显示每个机器人在不在线。"""
    import time

    from changri.core.db import set_config

    now = str(int(time.time()))
    for bot_id in nonebot.get_bots():
        set_config(f"heartbeat:{bot_id}", now)


# 启动 90 秒后还活着，就撤掉扩展的崩溃保护标记（见 core/extensions.py 的 load_all）
from changri.core import extensions as _ext  # noqa: E402

scheduler.add_job(_ext.confirm_boot, "date", run_date=datetime.datetime.now() + datetime.timedelta(seconds=90), id="ext_boot_confirm", replace_existing=True)
