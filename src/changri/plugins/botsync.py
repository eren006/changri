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


# ---- 在线更新：新版本启动 90 秒后跑升级后检查，过了才算成功（不过就回滚）；每 6 小时悄悄查一次有没有新版本（顺便匿名上报使用情况）；
#      落后太多时每 10 分钟看一次能不能在空闲时段自动更新 ----
import datetime  # noqa: E402
import time  # noqa: E402

from changri.core import updater  # noqa: E402
from changri.core.db import set_config  # noqa: E402

_BOOT_TS = time.time()


async def _post_update_check():
    """刚更新完（有 update_pending.json）：启动 90 秒后检查设置平台、小手机、数据库、机器人在线、定时任务、报错；不过就回滚。"""
    from changri.core import autoupdate
    try:
        res = await autoupdate.post_check(_BOOT_TS)
    except Exception as e:  # noqa: BLE001  检查本身出错：不回滚，交给看门进程 15 分钟后兜底确认
        logger.warning(f"[更新] 升级后检查出错：{e}")
        return
    if res != "没有待确认的更新":
        logger.info(f"[更新] 升级后检查：{res}")


scheduler.add_job(_post_update_check, "date", run_date=datetime.datetime.now() + datetime.timedelta(seconds=updater.HEALTHY_AFTER),
                  id="update_post_check", misfire_grace_time=None)     # 只跑一次的任务：到点时程序正忙也要补做（默认晚 1 秒就跳过）


@scheduler.scheduled_job("interval", minutes=10, id="auto_update", max_instances=1, coalesce=True)
async def _auto_update():
    """落后太多时，在空闲时段自动更新（条件见 core/autoupdate.py）。后台定时检查还没发现新版本时什么也不做，不联网。"""
    from changri.core import autoupdate
    from changri.core.db import get_config
    if not get_config("update_latest", "") or updater.read_pending() is not None:
        return
    snap = autoupdate.snapshot()
    try:
        to = await asyncio.get_running_loop().run_in_executor(None, autoupdate.maybe_update, snap)
    except Exception as e:  # noqa: BLE001  装的过程出错：原来的版本没动，下个 10 分钟再说
        logger.warning(f"[更新] 自动更新没装上：{e}")
        return
    if to:
        logger.info(f"[更新] 自动更新到 {to}，马上重启")
        updater.restart()


@scheduler.scheduled_job("interval", hours=6, id="update_check", next_run_time=datetime.datetime.now() + datetime.timedelta(minutes=2))
async def _update_check():
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, updater.check)
        set_config("update_latest", info["version"] if info["newer"] else "")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[更新] 检查失败：{e}")
    try:                                                   # 顺便拉作者的通知（core/notices.py），管理员打开后台时弹出来
        from changri.core import notices
        await asyncio.get_running_loop().run_in_executor(None, notices.fetch)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[通知] 拉取失败：{e}")


async def _usage_report(only_if_more_online: bool = False) -> None:
    """匿名上报一次使用情况（后台「基础」里能关，见 core/usage.py）。
    only_if_more_online：机器人连上时用，连着的比上次报出去的多才发（断线重连不重复报）。"""
    try:
        from changri.core import usage
        from changri.core.db import list_bot_ids
        bots, online = len(list_bot_ids()), len(nonebot.get_bots())
        if only_if_more_online and online <= usage.last_online():
            return
        await asyncio.get_running_loop().run_in_executor(None, usage.report, bots, online)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[使用情况] 上报失败：{e}")


@scheduler.scheduled_job("interval", hours=1, id="usage_report", next_run_time=datetime.datetime.now() + datetime.timedelta(minutes=2))
async def _usage_report_job():
    await _usage_report()


@scheduler.scheduled_job("interval", minutes=5, id="usage_save", max_instances=1, coalesce=True)
async def _usage_save():
    """还没报出去的指令次数存盘，重启（包括 Windows 直接关窗口）最多丢 5 分钟的。"""
    from changri.core import usage
    await asyncio.get_running_loop().run_in_executor(None, usage.save_pending)


_driver = nonebot.get_driver()


@_driver.on_bot_connect
async def _usage_on_connect():
    """刚在后台加了机器人、或者启动 2 分钟后才连上：等 1 分钟（几个机器人一起连上只报一次）再补报，主站就能看到连着几个。"""
    scheduler.add_job(_usage_report, "date", run_date=datetime.datetime.now() + datetime.timedelta(minutes=1),
                      kwargs={"only_if_more_online": True}, id="usage_on_connect", replace_existing=True, misfire_grace_time=None)


@_driver.on_shutdown
async def _usage_save_on_exit():
    from changri.core import usage
    usage.save_pending()


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

# misfire_grace_time=None：到点时程序正忙也要补做。默认晚 1 秒就跳过，跳过了这次启动就一直没「确认平安」，
# 之后一旦异常退出（断电、被强行结束），下次启动会把用户扩展全部当成嫌疑自动停用
scheduler.add_job(_ext.confirm_boot, "date", run_date=datetime.datetime.now() + datetime.timedelta(seconds=90), id="ext_boot_confirm",
                  replace_existing=True, misfire_grace_time=None)
