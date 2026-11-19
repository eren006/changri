"""QQ 端「角色卡 / 名片」：常规功能，不管「物品与商城」开没开都能用；商城关着时卡上不显示货币和背包。
跑团模式的群里「角色卡」归跑团（plugins/trpg.py 的 /st），这里会被 ROMANCE_PLUGINS 屏蔽。"""
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent, MessageSegment

from changri.core import roles, rpgcard

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"

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
    await card.finish("\n".join(lines))
