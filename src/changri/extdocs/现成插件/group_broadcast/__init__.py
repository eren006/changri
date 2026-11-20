"""现成插件：群播报。管理员发「播报 戏群 内容」，发到对应的群。用到 is_admin、stage_group 这类函数、send_group、CommandArg。"""
from changri_api import (Bot, CommandArg, GroupMessageCreateEvent, Message, announce_group, backstage_group, is_admin, on_command,
                         send_group, setting, stage_group, water_group)

EXT_ID = "group_broadcast"

TARGETS = {"戏群": stage_group, "公告群": announce_group, "水群": water_group, "后台群": backstage_group}
USAGE = "用法：播报 戏群 内容（可以发到 " + "、".join(TARGETS) + "）"

cast = on_command("播报", priority=10, block=True)


@cast.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args: Message = CommandArg()):
    if not is_admin(bot, event):
        await cast.finish("只有管理员能播报。")
    where, _, text = args.extract_plain_text().strip().partition(" ")
    text = text.strip()
    if where not in TARGETS or not text:
        await cast.finish(USAGE)
    g = TARGETS[where]()
    if not g:
        await cast.finish(f"还没设{where}：在那个群里发「设为{where}」。")
    await send_group(*g, f"{setting(EXT_ID, 'prefix') or ''}{text}")
    await cast.finish(f"✅ 已发到{where}。")
