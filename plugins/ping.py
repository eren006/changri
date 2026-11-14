"""第 0 步的连通性测试：发 /ping 回「收到」，并带上设置平台里配的一句话。"""
from nonebot import on_command

from core.db import get_config

ping = on_command("ping", aliases={"测试"}, priority=10, block=True)


@ping.handle()
async def _():
    hello = get_config("hello_text", "收到。")
    await ping.finish(hello)
