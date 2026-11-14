"""给扩展用的稳定接口（API 版本 1）。

写扩展只从这里取东西，不要直接 import core.* 或 plugins.*，也不要直接读写数据库里的表：
内部结构以后会变，这里的函数会一直保持可用，所以扩展不会被更新弄坏。
完整说明见 docs/写扩展.md。
"""
from __future__ import annotations

from typing import Awaitable, Callable

import nonebot
from nonebot import on_command, on_message  # noqa: F401  扩展里照常用 NoneBot 的 on_command / on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent  # noqa: F401

from core import admins as _admins
from core import attrs as _attrs
from core import extensions as _ext
from core import messaging as _messaging
from core import roles as _roles
from core import pending as _pending, season as _season
from core.announce import get_announce as _get_announce
from core.db import get_config as _get_config
from core.db import get_conn as _get_conn
from plugins._long import make_send as _make_send
from plugins._long import reply_long  # noqa: F401  回复触发指令的人：够长自动发图

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402,F401  定时任务：@scheduler.scheduled_job("interval", seconds=60)

API_VERSION = _ext.API_VERSION


# ---------- 谁在说话 ----------

def sender_role(bot: Bot, event: GroupMessageCreateEvent):
    """发这条消息的人绑定的角色，没有返回 None。返回的对象可以用 role["id"]、role["name"]。"""
    return _roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


def role_by_name(name: str):
    return _roles.get_role_by_name(name)


def all_roles() -> list:
    with _get_conn() as conn:
        return list(conn.execute("SELECT id, name FROM roles ORDER BY id"))


def is_admin(bot: Bot, event: GroupMessageCreateEvent) -> bool:
    return _admins.is_admin(bot.self_id, event.author.member_openid)


# ---------- 发消息 ----------

async def send_group(bot_id: str, group_openid: str, text: str):
    """发到某个群（够长自动发图）。bot_id 和 group_openid 要成对，每个机器人看到的群标识不一样。"""
    return await _make_send()(bot_id, group_openid, text)


async def send_to_role(role_id: int, text: str) -> bool:
    """发到某个角色：主群优先，失败试备用群。返回是否送达。"""
    ok, _ = await _messaging.push_to_role(role_id, text, _make_send())
    return ok


def announce_group() -> tuple[str, str] | None:
    """公告群，没设置返回 None。返回 (bot_id, group_openid)，可以直接 send_group(*announce_group(), text)。"""
    return _get_announce()


# ---------- 属性（COC / DND 骰子也读这个） ----------

def get_attr(role_id: int, name: str) -> int | None:
    return _attrs.get_attr(role_id, name)


def set_attr(role_id: int, name: str, value: int) -> tuple[bool, str]:
    """返回 (是否成功, 说明)。"""
    return _attrs.set_attrs(role_id, [(name, int(value))])


def list_attrs(role_id: int) -> list[tuple[str, int]]:
    return _attrs.list_attrs(role_id)


# ---------- 游戏天数 ----------

def season_name() -> str:
    """进行中的季度名，没有返回空串。"""
    s = _season.running()
    return s["name"] if s else ""


def game_day() -> int | None:
    """现在是 D 几（整数）。没有季度或筹备期返回 None。"""
    return _season.current_day() if _season.running() else None


def day_key() -> str:
    """「今天」的唯一标记（天数一变就变），做每日次数限制用。"""
    return _season.day_key()


# ---------- 自己的设置项（在 manifest.json 的 settings 里声明，设置平台自动出表单） ----------

def setting(ext_id: str, key: str, default=None):
    """读自己的设置项，已按声明的类型转好（int / bool / 字符串）。"""
    return _ext.get_setting(ext_id, key, default)


def read_config(key: str, default: str = "") -> str:
    """读主程序的某个配置（只读），比如 brand_name。"""
    return _get_config(key, default)


def register_pending(name: str, fn) -> None:
    """往玩家的「我的待回」里加一段。fn(role) -> list[str]，role 是角色（可用 role["id"]、role["name"]），没有要回的事返回空列表。"""
    _pending.register(name, fn)
