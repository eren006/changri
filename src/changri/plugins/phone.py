"""小手机：玩家在个人群里发「手机码」，机器人回网页链接和激活码。网页本身在 phone/ 下，和机器人同进程。"""
from nonebot import on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent

from changri.core import phonecodes, roles
from changri.core.db import get_config


def phone_url() -> str:
    from changri.core import phoneserver
    return phoneserver.player_url()


cmd = on_command("手机码", aliases={"我的手机", "小手机"}, priority=10, block=True)


@cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if get_config("phone_enabled", "0") != "1":
        await cmd.finish("小手机现在没有开放。")
    role = roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)
    if not role:
        await cmd.finish("请先在你的个人群里发「创建新角色 名字」绑定角色。")
    code = phonecodes.code_for_role(role["id"])
    url = phone_url()
    where = f"🔗 打开：{url}" if url else "🔗 网址请向管理员要（管理员还没在设置平台填小手机地址）"
    await cmd.finish(f"📱 {role['name']} 的小手机\n{where}\n🔑 激活码：{code}\n激活码只属于你，别发给别人。")
