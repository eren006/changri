"""扩展：牌堆。群里「抽卡 牌堆名 [张数]」，只发「抽卡」列出能抽的牌堆。规则和存取在 core/decks.py；牌堆在后台「跑团 → 牌堆」里添加。
抽取池（抽卡池）的指令还是「抽取」，跟这里不冲突。"""
from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.rule import Rule

from changri.core import decks, extensions
from changri.core.qqsend import reply_long


async def _on() -> bool:
    return extensions.is_enabled("decks")          # 一个进程接多个机器人：每个机器人各看自己的开关


draw = on_command("抽卡", rule=Rule(_on), priority=10, block=True)


@draw.handle()
async def _(args=CommandArg()):
    await reply_long(draw, decks.draw(args.extract_plain_text().strip()), as_image=False)
