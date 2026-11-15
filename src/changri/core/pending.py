"""「我的待回」的内容来源。核心只负责房间/时间线；关系线、心动信这些扩展自己注册一段进来，扩展关了就不会出现。"""
from typing import Callable

_PROVIDERS: list[tuple[str, Callable]] = []


def register(name: str, fn: Callable) -> None:
    """fn(role_row) -> list[str]（空列表表示没有）。同名重复注册会覆盖，方便热重载。"""
    global _PROVIDERS
    _PROVIDERS = [p for p in _PROVIDERS if p[0] != name] + [(name, fn)]


def collect(role) -> list[str]:
    out: list[str] = []
    for name, fn in _PROVIDERS:
        try:
            out += list(fn(role) or [])
        except Exception as e:  # noqa: BLE001 一个扩展出错不能拖垮整条指令
            out.append(f"（{name} 的待办读取失败：{str(e)[:40]}）")
    return out
