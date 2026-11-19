"""牌堆（核心功能，一直开着）。群里「抽卡 牌堆名 [张数]」，只发「抽卡」列出能抽的牌堆。规则和存取在 core/decks.py；牌堆在后台「跑团 → 牌堆」里添加。
抽取池（抽卡池）的指令还是「抽取」，跟这里不冲突。"""
from nonebot import on_command
from nonebot.params import CommandArg

from changri.core import decks
from changri.core.qqsend import reply_long


draw = on_command("抽卡", priority=10, block=True)


@draw.handle()
async def _(args=CommandArg()):
    await reply_long(draw, decks.draw(args.extract_plain_text().strip()), as_image=False)
