"""第 0 步的连通性测试：发 /ping 回一句话。设置平台「基础」里的「/ping 的回复」可以写好几行，每次随机回一行。"""
import random

from nonebot import on_command

from changri.core.db import get_config

ping = on_command("ping", aliases={"测试"}, priority=10, block=True)


def reply_text() -> str:
    lines = [x.strip() for x in get_config("hello_text", "收到。").splitlines() if x.strip()]
    return random.choice(lines) if lines else "收到。"


@ping.handle()
async def _():
    await ping.finish(reply_text())
