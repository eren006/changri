"""QQ 端「角色卡 / 名片」：常规功能，不管「物品与商城」开没开都能用；商城关着时卡上不显示货币和背包。
跑团模式的群里「角色卡」归跑团（plugins/trpg.py 的 /st），这里会被 ROMANCE_PLUGINS 屏蔽。"""
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent, MessageSegment

from changri.core import formats, roles, rpgcard
from changri.core.buttons import Btn
from changri.core.qqsend import reply_buttons

NEED_ROLE = "你还没有角色。在你的个人群里发「创建新角色 名字」就能创建。"

card = on_command("角色卡", aliases={"名片", "我的名片", "角色名片"}, priority=10, block=True)


@card.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)
    if not me:
        await card.finish(NEED_ROLE)
    try:
        png = rpgcard.render_card(me["id"])
        if png:
            await card.send(MessageSegment.file_image(png))
            await _next(me)
            return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[角色卡] 画图失败，改发文字：{e}")
    info = rpgcard.data(me["id"])
    pf = info["profile"]
    lines = [f"🪪 {info['name']} 的角色卡" + (f"（简称：{pf['short']}）" if pf["short"] else ""),
             f"{pf['gender']} · {pf['age']} 岁 · 皮相 {pf['look']}"] + ([f"✏️ {pf['bio']}"] if pf["bio"] else [])
    if info["attrs"]:
        lines.append("属性：" + "、".join(f"{a['name']} {a['value']}" for a in info["attrs"]))
    if info["money"]:
        lines.append("货币：" + "、".join(f"{x['name']} {x['count']}" for x in info["money"]))
    if info["shop"]:
        lines.append("背包：" + ("、".join(f"{x['name']}×{x['count']}" for x in info["items"]) or "空"))
    await card.send("\n".join(lines))
    await _next(me)


async def _next(me) -> None:
    """卡片后面跟一条短消息挂按钮（按钮挂不到图片上）。按钮发不出去（没权限、被关）就什么都不补，卡片已经发了。"""
    await reply_buttons(card, formats.CARD_NEXT, [Btn(label, cmd) for label, cmd in formats.card_actions(me)], per_row=3, fallback=False)
