"""复盘：官方机器人在房间群里收到的发言。"""

from nonebot import logger, on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent


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
