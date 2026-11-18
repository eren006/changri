"""消息监听的挂钩（记录在 core/msgmon.py）：官方机器人收到的群消息 + QQ 小号在群号组里听到的消息。只记不拦。"""
from nonebot import logger, on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent

from changri.core import msgmon, qqmanager, roles
from changri.core.db import use_bot


@on_message(priority=0, block=False).handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    try:
        if not msgmon.enabled():
            return
        mid = event.author.member_openid
        role = roles.find_role_for_sender(bot.self_id, event.group_openid, mid)
        text = event.get_plaintext().strip()
        if getattr(event, "attachments", None):
            text = (text + " " + "[图片]" * len(event.attachments)).strip()
        msgmon.record("official", msgmon.official_group_label(bot.self_id, event.group_openid),
                      role["name"] if role else f"未绑定 {mid[:6]}…", text)
    except Exception as e:  # noqa: BLE001 监听出错不能影响正常处理
        logger.warning(f"[消息监听] 记录失败：{e}")


def _on_listened(app_id, m) -> None:
    try:
        with use_bot(app_id):
            if msgmon.enabled():
                msgmon.record("qq", msgmon.qq_group_label(m.group_id), msgmon.qq_sender_label(m.user_id, m.nickname), m.text, m.time)
    except Exception:  # noqa: BLE001
        pass


if _on_listened not in qqmanager._listeners:
    qqmanager.add_listener(_on_listened)
