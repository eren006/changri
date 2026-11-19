"""定时收集的玩家入口：「收集」看正在收集什么，「收集 内容」提交。创建/关闭/查看都在后台「收集」页。规则在 core/collect.py。"""
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import collect
from changri.plugins.play import NEED_ROLE, _cmd, _me
from changri.core.textutil import parse_id

cmd = _cmd("收集")


@cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await cmd.finish(NEED_ROLE)
    text = args.extract_plain_text().strip()
    if not text:
        await cmd.finish(collect.describe_open())
    head, _, rest = text.partition(" ")
    cid = None
    n = parse_id(head)
    if n and rest.strip() and collect.get(n):       # 第一个词是存在的收集编号才当编号，否则整段都是内容
        cid, text = n, rest.strip()
    ok, tip = collect.submit(me["id"], text, cid)
    await cmd.finish(tip)
