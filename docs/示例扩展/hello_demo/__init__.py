"""示例扩展：打招呼。只用 changri_api，不碰内部的表。"""
from changri_api import Bot, GroupMessageCreateEvent, on_command, sender_role, setting

EXT_ID = "hello_demo"          # 要和 manifest.json 里的 id 一致

hello = on_command("打招呼", priority=10, block=True)


@hello.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    role = sender_role(bot, event)
    name = role["name"] if role else "陌生人"
    text = setting(EXT_ID, "greeting") * max(1, int(setting(EXT_ID, "times") or 1))
    if setting(EXT_ID, "loud"):
        text += "！"
    await hello.finish(f"{name}，{text}")
