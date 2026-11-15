"""自定义回复：群里说了设置过的词，机器人自动回一句。优先级低于所有游戏指令，不拦截别的功能。"""
from nonebot import logger, on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent

from changri.core import admins, autoreply, roles
from changri.core.db import get_conn

matcher = on_message(priority=30, block=False)


@matcher.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    text = event.get_plaintext().strip()
    if not text:
        return
    try:
        role = roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)
        with get_conn() as c:
            personal = c.execute("SELECT 1 FROM role_groups WHERE bot_id=? AND group_openid=?", (bot.self_id, event.group_openid)).fetchone() is not None
        reply = autoreply.pick(text, bot_id=bot.self_id, group=event.group_openid, has_role=bool(role),
                               is_admin=admins.is_admin(bot.self_id, event.author.member_openid), in_personal=personal,
                               role_name=role["name"] if role else "")
    except Exception as e:  # noqa: BLE001 自定义回复出错不能影响别的功能
        logger.warning(f"[自定义回复] 出错：{e}")
        return
    for text in reply or []:
        await matcher.send(text)
