"""现成插件：每日签到（2.0）。按「玩家本人」长期记（player_data_*，接口版本 4）：连签、总签到天数跨季度一直算，收尾不清；
没有恋综角色的人也能签（只记天数，不发金币）。有角色的照旧发金币，today_add 也记一笔，「我的待回」提醒靠它。
同一个人在别的群签到要先「认人」（有恋综角色的不用），不然算两个人。
1.x 存在本季数据 / data_set 里的连签：这个人第一次用 2.0 签到时接过来。"""
import datetime

from changri_api import (Bot, GroupMessageCreateEvent, data_get, data_set, day_key, give_item, item_count, on_command,
                         player_data_get, player_data_set, register_pending, season_data_get, season_data_set, sender_player,
                         sender_role, setting, today_add, today_count)

EXT_ID = "daily_signin"

signin = on_command("签到", priority=10, block=True)


def _today_no() -> int:
    """连签用的「第几天」：按日历日期，跨季度也接得上。"""
    return datetime.date.today().toordinal()


def _carry_old(pid: int, me) -> int:
    """1.x 的连签（按角色存在本季数据或 data_set 里）：这个人还没有 2.0 的记录时接过来。返回接过来的连签天数（接不上的算 0）。"""
    if me is None or player_data_get(EXT_ID, pid, "last") is not None:
        return 0
    rid = me["id"]
    old = season_data_get(EXT_ID, f"streak_{rid}", data_get(EXT_ID, f"streak_{rid}", 0)) or 0
    season_data_set(EXT_ID, f"streak_{rid}", None)                 # 搬过来就删，不会接两次
    season_data_set(EXT_ID, f"last_{rid}", None)
    data_set(EXT_ID, f"streak_{rid}", None)
    data_set(EXT_ID, f"last_{rid}", None)
    return int(old)


@signin.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = sender_role(bot, event)
    pid = sender_player(bot, event)
    who = me["name"] if me else "你"
    today = day_key()
    if player_data_get(EXT_ID, pid, "signed") == today or (me and today_count(EXT_ID, "签到", me["id"]) >= 1):
        await signin.finish(f"{who}，今天已经签过了，明天再来。")

    now = _today_no()
    old = _carry_old(pid, me)
    last = player_data_get(EXT_ID, pid, "last")
    if last is None and old:
        streak = old + 1                                            # 1.x 的连签接着算（没法知道断没断，就当没断）
    elif isinstance(last, int) and now == last + 1:
        streak = int(player_data_get(EXT_ID, pid, "streak", 0)) + 1
    else:
        streak = 1
    total = int(player_data_get(EXT_ID, pid, "total", 0)) + 1

    gift = ""
    if me:
        cap = max(1, int(setting(EXT_ID, "streak_cap") or 1))
        amount = max(0, int(setting(EXT_ID, "reward") or 0)) + max(0, int(setting(EXT_ID, "streak_bonus") or 0)) * (min(streak, cap) - 1)
        currency = setting(EXT_ID, "currency") or "金币"
        if amount > 0:
            ok, msg = give_item(me["id"], currency, amount, "每日签到")
            if not ok:
                await signin.finish(f"签到失败：{msg}（请管理员检查「每日签到」设置里的「发什么」）")
            gift = f"获得 {currency}×{amount}，现在有 {item_count(me['id'], currency)} 个{currency}。"
        today_add(EXT_ID, "签到", me["id"])

    player_data_set(EXT_ID, pid, "signed", today)
    player_data_set(EXT_ID, pid, "last", now)
    player_data_set(EXT_ID, pid, "streak", streak)
    player_data_set(EXT_ID, pid, "total", total)
    tail = f"连续签到 {streak} 天，" if streak > 1 else ""
    await signin.finish(f"✅ {who}签到成功。{gift}{tail}一共签到 {total} 天。")


def _pending(role) -> list[str]:
    if not setting(EXT_ID, "remind"):
        return []
    return ["今天还没「签到」"] if today_count(EXT_ID, "签到", role["id"]) == 0 else []


register_pending("签到", _pending)
