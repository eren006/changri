"""给扩展用的稳定接口（API 版本 2）。

写扩展只从这里取东西，不要直接 import core.* 或 plugins.*，也不要直接读写数据库里的表：
内部结构以后会变，这里的函数会一直保持可用，所以扩展不会被更新弄坏。
完整说明见 docs/写扩展.md。

设置平台「功能与设置 → 插件接口」那一页是直接读这个文件生成的：
每个公开函数（名字不以 _ 开头）按它上面最近的「# ---------- 分类 ----------」归类，docstring 第一段是说明，
「例：」后面是示例。加函数时照这个格式写，页面会自动出现。
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

import nonebot
from nonebot import on_command, on_message  # noqa: F401  扩展里照常用 NoneBot 的 on_command / on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent, Message  # noqa: F401
from nonebot.params import CommandArg  # noqa: F401  拿指令后面跟的文字：args: Message = CommandArg()

from changri.core import admins as _admins
from changri.core import attrs as _attrs
from changri.core import extensions as _ext
from changri.core import extdata as _extdata
from changri.core import messaging as _messaging
from changri.core import roles as _roles
from changri.core import pending as _pending, season as _season
from changri.core import announce as _announce
from changri.core import entrance as _entrance
from changri.core import relations as _relations
from changri.core import rpg as _rpg
from changri.core.announce import get_announce as _get_announce
from changri.core.db import get_config as _get_config
from changri.core.db import get_conn as _get_conn
from changri.core.qqsend import make_send as _make_send
from changri.core.qqsend import reply_long  # noqa: F401  回复触发指令的人：够长自动发图

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402,F401  定时任务：@scheduler.scheduled_job("interval", seconds=60)

API_VERSION = _ext.API_VERSION


# ---------- 谁在说话 ----------

def sender_role(bot: Bot, event: GroupMessageCreateEvent):
    """发这条消息的人绑定的角色，没有返回 None。返回的对象可以用 role["id"]、role["name"]。
    例：role = sender_role(bot, event)"""
    return _roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


def role_by_name(name: str):
    """按名字找角色，找不到返回 None。可以用 role["id"]、role["name"]。
    例：r = role_by_name("林深")"""
    return _roles.get_role_by_name(name)


def role_by_id(role_id: int):
    """按编号找角色，找不到返回 None。存了角色编号、之后要拿回名字时用。
    例：r = role_by_id(3)"""
    return _roles.get_role(int(role_id))


def all_roles() -> list:
    """玩家清单：所有角色，每项可用 r["id"]、r["name"]。
    例：for r in all_roles(): ..."""
    with _get_conn() as conn:
        return list(conn.execute("SELECT id, name FROM roles ORDER BY id"))


def is_admin(bot: Bot, event: GroupMessageCreateEvent) -> bool:
    """发言人是不是机器人管理员。管理员专用的指令先用它挡一下。
    例：if not is_admin(bot, event): await cmd.finish("只有管理员能用")"""
    return _admins.is_admin(bot.self_id, event.author.member_openid)


# ---------- 发消息 ----------

async def send_group(bot_id: str, group_openid: str, text: str):
    """发到某个群，够长自动发图。bot_id 和 group_openid 要成对（每个机器人看到的群标识不一样），一般直接用 announce_group() 这类函数的返回值。
    例：await send_group(*announce_group(), "今晚八点开播")"""
    return await _make_send()(bot_id, group_openid, text)


async def send_to_role(role_id: int, text: str) -> bool:
    """发到某个角色的个人群。返回是否送达。
    例：ok = await send_to_role(role["id"], "你的包裹到了")"""
    ok, _ = await _messaging.push_to_role(role_id, text, _make_send())
    return ok


# ---------- 特殊的群 ----------

def announce_group() -> tuple[str, str] | None:
    """公告群，没设置返回 None。返回 (bot_id, group_openid)，可以直接 send_group(*announce_group(), text)。
    例：g = announce_group()"""
    return _get_announce()


def backstage_group() -> tuple[str, str] | None:
    """后台群（给管理组看的），没设置返回 None。
    例：g = backstage_group()"""
    return _announce.get_backstage()


def water_group() -> tuple[str, str] | None:
    """水群，没设置返回 None。
    例：g = water_group()"""
    return _announce.get_water()


def stage_group() -> tuple[str, str] | None:
    """戏群（出场、挂心愿发到这里），没设置返回 None。管理员在群里发「设为戏群」设置。
    例：g = stage_group()
    if g: await send_group(*g, "第二幕开始")"""
    return _entrance.get_stage()


def staff_notice_group() -> tuple[str, str] | None:
    """给管理组发提醒用：后台群优先，没设就是公告群；都没设返回 None。
    例：g = staff_notice_group()"""
    return _announce.get_staff_notice()


# ---------- 属性（COC / DND 骰子也读这个） ----------

def get_attr(role_id: int, name: str) -> int | None:
    """读角色的某个属性（HP、力量……），没有这个属性返回 None。金币不是属性，用 item_count。
    例：hp = get_attr(role["id"], "HP")"""
    return _attrs.get_attr(role_id, name)


def set_attr(role_id: int, name: str, value: int) -> tuple[bool, str]:
    """把角色的某个属性设成一个值。返回 (是否成功, 说明)，失败时把说明回给玩家。
    例：ok, msg = set_attr(role["id"], "HP", 80)"""
    return _attrs.set_attrs(role_id, [(name, int(value))])


def list_attrs(role_id: int) -> list[tuple[str, int]]:
    """角色的所有属性，[(名字, 数值), ...]。
    例：for name, v in list_attrs(role["id"]): ..."""
    return _attrs.list_attrs(role_id)


# ---------- 物品与背包（金币也在这里） ----------

_MAX_ITEM_DELTA = 9999


def _item_view(it) -> dict:
    return {"code": it["code"], "name": it["name"], "kind": it["kind"], "description": it["description"] or ""}


def item_info(item: str) -> dict | None:
    """按物品名或物品码找物品，找不到返回 None。返回 {"code", "name", "kind", "description"}，kind 是 item（道具）或 currency（货币）。
    例：it = item_info("金币")"""
    it = _rpg.find_item(str(item))
    return _item_view(it) if it else None


def all_items() -> list[dict]:
    """物品库里的所有物品（含货币），每项同 item_info。
    例：names = [it["name"] for it in all_items()]"""
    return [_item_view(it) for it in _rpg.items()]


def inventory(role_id: int) -> list[dict]:
    """角色的背包，同一种物品合成一项：[{"code", "name", "kind", "count"}, ...]。
    例：for it in inventory(role["id"]): ..."""
    out: dict[str, dict] = {}
    for r in _rpg.inventory(int(role_id)):
        row = out.setdefault(r["item_code"], {"code": r["item_code"], "name": r["name"], "kind": r["kind"], "count": 0})
        row["count"] += r["count"]
    return list(out.values())


def item_count(role_id: int, item: str) -> int:
    """角色有几个这种物品（物品名或物品码），没有返回 0。读金币也用它。
    例：gold = item_count(role["id"], "金币")"""
    it = _rpg.find_item(str(item))
    if not it:
        return 0
    return sum(r["count"] for r in inventory(role_id) if r["code"] == it["code"])


def _check_n(count) -> tuple[int, str]:
    try:
        n = int(count)
    except (TypeError, ValueError):
        return 0, "数量要写整数。"
    if n <= 0:
        return 0, "数量要大于 0。"
    if n > _MAX_ITEM_DELTA:
        return 0, f"一次最多 {_MAX_ITEM_DELTA} 个。"
    return n, ""


def give_item(role_id: int, item: str, count: int = 1, note: str = "") -> tuple[bool, str]:
    """往角色背包里放物品（发金币也用它）。一次最多 9999 个。note 会记进物品流水，写清是哪个扩展发的。
    返回 (是否成功, 说明)，物品不存在时说明是「找不到这个物品。」。
    例：ok, msg = give_item(role["id"], "金币", 10, "签到奖励")"""
    n, err = _check_n(count)
    if err:
        return False, err
    return _rpg.adjust(int(role_id), str(item), n, f"扩展：{note}"[:200])


def take_item(role_id: int, item: str, count: int = 1, note: str = "") -> tuple[bool, str]:
    """从角色背包里扣物品（扣金币也用它）。不够扣就什么都不扣，返回 (False, "「金币」只有 3 件。") 这样的说明。
    例：ok, msg = take_item(role["id"], "金币", 5, "买彩票")"""
    n, err = _check_n(count)
    if err:
        return False, err
    return _rpg.adjust(int(role_id), str(item), -n, f"扩展：{note}"[:200])


# ---------- 地点 ----------

def all_places() -> list[dict]:
    """地点列表：[{"id", "name", "description", "locked"}, ...]，locked 为 True 的要钥匙。
    例：names = [p["name"] for p in all_places()]"""
    return [{"id": p["id"], "name": p["name"], "description": p["description"] or "", "locked": bool(p["locked"])} for p in _rpg.places()]


def place_keys(role_id: int) -> list[str]:
    """角色手里有钥匙的地点名。
    例：if "天台" in place_keys(role["id"]): ..."""
    return [p["name"] for p in _rpg.keys(int(role_id))]


def can_use_place(role_id: int, place: str) -> tuple[bool, str]:
    """角色能不能去这个地点（和约会时的检查一样）。返回 (能不能, 不能时的说明)。没开「只能用预设地点」时一律能去。
    例：ok, why = can_use_place(role["id"], "天台")"""
    return _rpg.check_place(int(role_id), str(place))


# ---------- 关系线 ----------

def relations_of(role_id: int) -> list[dict]:
    """角色的所有关系线：[{"other_id", "other_name", "confirmed", "count", "chars"}, ...]。
    confirmed 是否已确认完成，count 细节段数，chars 总字数。只读，不会改动关系线。
    例：for r in relations_of(role["id"]): ..."""
    return [{k: r[k] for k in ("other_id", "other_name", "confirmed", "count", "chars")} for r in _relations.list_for(int(role_id))]


# ---------- 游戏天数 ----------

def season_name() -> str:
    """进行中的季度名，没有返回空串。
    例：name = season_name()"""
    s = _season.running()
    return s["name"] if s else ""


def game_day() -> int | None:
    """现在是 D 几（整数）。没有季度或筹备期返回 None。
    例：d = game_day()"""
    return _season.current_day() if _season.running() else None


def day_key() -> str:
    """「今天」的唯一标记（天数一变就变），做每日次数限制用。一般直接用 today_count / today_add 更省事。
    例：k = day_key()"""
    return _season.day_key()


# ---------- 扩展自己存数据 ----------

# 存在这个机器人自己的 bots/<AppID>/ext_data/ 里，各机器人互不相通（见 core/extdata.py）
_data_lock = _extdata.lock
_data_load = _extdata.load
_data_save = _extdata.save


def data_get(ext_id: str, key: str, default=None):
    """读扩展自己存的数据（每个机器人各存一份，更新主程序、重装扩展都不会丢）。没存过返回 default。
    这里的数据跨季度一直留着，「收尾」也不清：适合设置、累计的统计这类。跟本季角色有关的（连签、积分、本季排行）用 season_data_get。
    例：total = data_get(EXT_ID, "累计签到次数", 0)"""
    with _data_lock:
        return _data_load(ext_id).get(str(key), default)


def data_set(ext_id: str, key: str, value) -> None:
    """存一条扩展自己的数据，跨季度一直留着。value 要能存成 JSON（数字、文字、True/False、列表、字典）。value 写 None 就是删掉这条。每个扩展最多 2MB。
    例：data_set(EXT_ID, "累计签到次数", total + 1)"""
    json.dumps(value)                       # 存不成 JSON 的东西在这里就报错，不写坏文件
    with _data_lock:
        d = _data_load(ext_id)
        if value is None:
            d.pop(str(key), None)
        else:
            d[str(key)] = value
        _data_save(ext_id, d)


def data_keys(ext_id: str, prefix: str = "") -> list[str]:
    """扩展存过的所有键（可只列某个前缀开头的）。只列 data_set 存的，不含 season_data_set 的。
    例：for k in data_keys(EXT_ID, "统计_"): ..."""
    with _data_lock:
        return sorted(k for k in _data_load(ext_id) if k.startswith(prefix))


def season_data_get(ext_id: str, key: str, default=None):
    """读本季的数据（版本 3）。管理员「收尾」时全部清空，新一季从空的开始；没存过返回 default。
    角色是一季一季重建的，跟角色有关的东西（连签、积分、本季排行）都存这里，上一季的人不会出现在新一季里。
    例：streak = season_data_get(EXT_ID, f"连签_{role['id']}", 0)"""
    with _data_lock:
        return _data_load(ext_id, True).get(str(key), default)


def season_data_set(ext_id: str, key: str, value) -> None:
    """存一条本季的数据（版本 3），「收尾」时清空。value 写 None 就是删掉这条。和 data_set 的数据分开存，各自最多 2MB。
    例：season_data_set(EXT_ID, f"连签_{role['id']}", streak + 1)"""
    json.dumps(value)
    with _data_lock:
        d = _data_load(ext_id, True)
        if value is None:
            d.pop(str(key), None)
        else:
            d[str(key)] = value
        _data_save(ext_id, d, True)


def season_data_keys(ext_id: str, prefix: str = "") -> list[str]:
    """本季存过的所有键（版本 3），做本季排行时用它列出所有人。
    例：for k in season_data_keys(EXT_ID, "积分_"): ..."""
    with _data_lock:
        return sorted(k for k in _data_load(ext_id, True) if k.startswith(prefix))


def today_count(ext_id: str, name: str, role_id: int) -> int:
    """某个角色今天已经做过几次 name 这件事（天数一变自动归零）。做「每天限几次」用。
    例：if today_count(EXT_ID, "签到", role["id"]) >= 1: await cmd.finish("今天签过了")"""
    with _data_lock:
        rec = _data_load(ext_id).get(f"_today:{name}") or {}
    return int(rec.get("counts", {}).get(str(role_id), 0)) if rec.get("day") == _season.day_key() else 0


def today_add(ext_id: str, name: str, role_id: int, n: int = 1) -> int:
    """给某个角色今天的 name 计数加 n，返回加完以后的次数。换天会自动清掉前一天的记录。
    例：times = today_add(EXT_ID, "签到", role["id"])"""
    key, today = f"_today:{name}", _season.day_key()
    with _data_lock:
        d = _data_load(ext_id)
        rec = d.get(key) or {}
        if rec.get("day") != today:
            rec = {"day": today, "counts": {}}
        counts = rec["counts"]
        counts[str(role_id)] = int(counts.get(str(role_id), 0)) + int(n)
        d[key] = rec
        _data_save(ext_id, d)
        return counts[str(role_id)]


# ---------- 定时任务：每个团各跑一遍 ----------

def each_team(fn):
    """定时任务专用（版本 3）：一台服务器可能接了好几个机器人（好几个团），定时任务本身不知道是替哪个团跑的，
    只会落在第一个团上。加上它，就对每个启用的团各跑一遍，里面的 all_roles、data_get、season_data_get、announce_group 都是那个团自己的；
    一个团出错不影响别的团。写在 @scheduler.scheduled_job 下面一行（顺序反了不起作用）。
    例：@scheduler.scheduled_job("cron", hour=20, minute=0)
    @each_team
    async def nightly():
        for r in all_roles(): ..."""
    import functools
    import inspect
    import logging

    from changri.core import db as _db

    @functools.wraps(fn)
    async def run_each():
        for app_id in _db.list_bot_ids():
            try:
                with _db.use_bot(app_id):
                    r = fn()
                    if inspect.isawaitable(r):
                        await r
            except Exception as e:  # noqa: BLE001
                logging.getLogger("changri.ext").warning(f"[{app_id}] 扩展定时任务 {getattr(fn, '__name__', '?')} 出错：{e}")
    run_each.__changri_each_team__ = True
    return run_each


# ---------- 自己的设置项（在 manifest.json 的 settings 里声明，设置平台自动出表单） ----------

def setting(ext_id: str, key: str, default=None):
    """读自己的设置项，已按声明的类型转好（int / bool / 字符串）。改了设置马上生效，不用重启。
    例：n = setting(EXT_ID, "reward")"""
    return _ext.get_setting(ext_id, key, default)


def read_config(key: str, default: str = "") -> str:
    """读主程序的某个配置（只读），比如 brand_name。
    例：brand = read_config("brand_name", "长日")"""
    return _get_config(key, default)


def register_pending(name: str, fn) -> None:
    """往玩家的「我的待回」里加一段。fn(role) -> list[str]，role 是角色（可用 role["id"]、role["name"]），没有要回的事返回空列表。
    例：register_pending("我的扩展", lambda role: ["还没签到"] if today_count(EXT_ID, "签到", role["id"]) == 0 else [])"""
    _pending.register(name, fn)
