"""QQ 端地点指令（官方扩展「地点」，可在「功能与设置」里开关，改了要重启）。「只能使用预设地点约会」用设置里的 place_system_enabled。"""
from nonebot import on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import roles, rpg
from changri.core.qqsend import reply_long

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"


def _me(bot: Bot, event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


place = on_command("地点", priority=10, block=True)


@place.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    sub = args.extract_plain_text().strip() or "查看"
    if sub in ("钥匙", "我的钥匙"):
        if not me:
            await place.finish(NEED_ROLE)
        rows = rpg.keys(me["id"])
        await place.finish("🔑 你的地点钥匙：\n" + ("、".join(x["name"] for x in rows) if rows else "（没有）"))
    rows = rpg.places()
    lines = ["🏢 地点列表"]
    owned = {x["id"] for x in rpg.keys(me["id"])} if me else set()
    for x in rows:
        mark = "📍" if not x["locked"] else ("🔑" if x["id"] in owned else "🔒")
        lines.append(f"{mark} {x['name']}" + (f"（{x['description']}）" if x["description"] else ""))
    if not rows:
        lines.append("（暂无公共地点）")
    await reply_long(place, "\n".join(lines))
