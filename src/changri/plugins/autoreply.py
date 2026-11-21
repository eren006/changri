"""自定义回复：群里说了设置过的词，机器人自动回一句。优先级低于所有游戏指令，不拦截别的功能。
回复里的 {检定:技能} 借跑团骰子插件（plugins/dice）的 _check_text 掷，跟 /ra 同一套规则。"""
from nonebot import logger, on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent

from changri.core import admins, autoreply, roles
from changri.core.db import get_conn

matcher = on_message(priority=30, block=False)


def _check(event: GroupMessageCreateEvent, what: str) -> str:
    """回复里的 {检定:侦查}：跟说话人自己发 /ra 侦查 一样（按这个群的规则读角色卡或角色属性），但不记成长标记。
    骰子功能关着就不掷。读不到数值时给一句短提示，不把 /ra 那段长说明塞进回复里。"""
    import sys
    from changri.core import dice
    from changri.core.db import get_config
    mod = sys.modules.get("changri.plugins.dice")
    if mod is None or get_config("dice_enabled", "1") != "1":
        return f"（{what}检定：骰子功能没开）"
    res = mod._check_text(event, what, 0)
    if isinstance(res, dice.Rolled):
        return str(res)
    return f"（{what}检定：{str(res).splitlines()[0][:60]}）"


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
                               role_name=role["name"] if role else "", check=lambda what: _check(event, what))
    except Exception as e:  # noqa: BLE001 自定义回复出错不能影响别的功能
        logger.warning(f"[自定义回复] 出错：{e}")
        return
    for text in reply or []:
        await matcher.send(text)
