"""QQ 号断线提醒：掉线超过 2 分钟，用卡拉往公告群发一条；恢复了再发一条。不发就记日志。"""
import time

import nonebot
from nonebot import logger

from core import announce, qqmanager
from core.db import current_bot, get_int, per_bot
from plugins._long import send_to_group

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


async def _notify(text: str) -> None:
    ann = announce.get_announce()
    bot = nonebot.get_bots().get(ann[0]) if ann else None
    if not bot:
        logger.warning(f"[QQ号监听] {text}（没有设公告群或机器人不在线，只记日志）")
        return
    try:
        await send_to_group(bot, ann[1], text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[QQ号监听] 提醒发送失败：{e}")


async def _notify_cmd(app_id: str, text: str) -> None:
    from core.db import use_bot
    with use_bot(app_id):
        await _notify(text)


if _notify_cmd not in qqmanager._notifiers:
    qqmanager.add_notifier(_notify_cmd)


@scheduler.scheduled_job("interval", seconds=30, id="qqlink_watch", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    from core import qqpool
    secs = get_int("qqlink_alert_secs", 120)
    for ln in qqpool.links():
        if not ln["enabled"]:
            continue
        res = qqmanager.watch(current_bot(), time.time(), secs, ln["id"])
        if not res:
            continue
        kind, reason = res
        st = qqmanager.link_status(current_bot(), ln["id"])
        who = f"QQ {st['self_id']}（{ln['name']}）" if st["self_id"] else f"监听用的 QQ 号「{ln['name']}」"
        await _notify(f"⚠️ {who} 掉线了：{reason}。听群消息、改群名暂时不能用，管理员请处理。" if kind == "alert" else f"✅ {who} 已恢复连接。")
