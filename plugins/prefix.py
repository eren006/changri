"""指令前缀的统一开关：设置里「指令必须带前缀」打开时，所有指令都要以 / . 。 开头，不带的不响应；
关闭（默认）时带不带都行，优先用不带前缀的写法。只管用 on_command 定义的指令，自定义回复、记 log 这类普通消息监听不受影响。"""
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.typing import T_State

from core.db import get_config


@run_preprocessor
async def _gate(matcher: Matcher, state: T_State):
    pf = state.get("_prefix") or {}
    if pf.get("command") is None:                       # 不是 on_command 指令
        return
    if get_config("require_prefix", "0") == "1" and not pf.get("command_start"):
        raise IgnoredException("设置要求指令必须带前缀")
