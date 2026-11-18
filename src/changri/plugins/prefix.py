"""指令前缀的统一开关：设置里「指令必须带前缀」打开时，所有指令都要以 / . 。 开头，不带的不响应；
关闭（默认）时带不带都行，优先用不带前缀的写法。只管用 on_command 定义的指令，自定义回复、记 log 这类普通消息监听不受影响。"""
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.rule import CommandRule
from nonebot.typing import T_State

from changri.core.db import get_config


@run_preprocessor
async def _gate(matcher: Matcher, state: T_State):
    pf = state.get("_prefix") or {}
    if pf.get("command") is None or not _is_command(matcher):   # 不是 on_command 指令（消息里有指令词，但这个是记 log、自定义回复这类普通监听）
        return
    if get_config("require_prefix", "0") == "1" and not pf.get("command_start"):
        raise IgnoredException("设置要求指令必须带前缀")
    from changri.core import usage
    usage.count_command(_cmd_name(matcher, pf))         # 只记次数和指令名，给匿名使用情况上报用（见 core/usage.py）


def _is_command(matcher: Matcher) -> bool:
    """这个 matcher 是不是 on_command 定义的。state 里的 _prefix 是整条消息共用的，普通消息监听也看得到，不能只看它。"""
    return any(isinstance(getattr(dep, "call", None), CommandRule) for dep in getattr(matcher.rule, "checkers", ()) or ())


def _cmd_name(matcher: Matcher, pf: dict) -> str:
    """玩家用的那个指令词（主名字或别名，比如 rd / r / 检定），不含后面写的内容。
    NoneBot 把主名字和别名放在一个无序集合里，分不出哪个是主名字，所以照实记用的是哪个。
    只认长日bot 自带的指令；用户自己装的扩展返回空（只算总数）。"""
    if not str(getattr(matcher, "module_name", "") or "").startswith("changri."):
        return ""
    cmd = pf.get("command") or ()
    return " ".join(cmd) if isinstance(cmd, tuple) else str(cmd)
