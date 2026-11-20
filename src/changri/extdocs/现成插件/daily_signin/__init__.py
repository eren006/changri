"""现成插件：每日签到。用到 today_count / today_add（每天一次）、season_data_get / season_data_set（记连签，收尾时清空）、give_item（发金币）。"""
import datetime

from changri_api import (Bot, GroupMessageCreateEvent, data_get, data_set, game_day, give_item, item_count, on_command,
                         register_pending, season_data_get, season_data_set, season_name, sender_role, setting, today_add, today_count)

EXT_ID = "daily_signin"

signin = on_command("签到", priority=10, block=True)


def _today_no() -> str:
    """连签用的「第几天」：有季度按季度里的 D 几，没有就按日历日期。"""
    d = game_day()
    return f"{season_name()}#{d}" if d is not None else f"#{datetime.date.today().toordinal()}"


def _is_next(prev: str, now: str) -> bool:
    try:
        (ps, pn), (ns, nn) = prev.rsplit("#", 1), now.rsplit("#", 1)
        return ps == ns and int(nn) == int(pn) + 1
    except (ValueError, AttributeError):
        return False


@signin.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    role = sender_role(bot, event)
    if not role:
        await signin.finish("你还没有角色，先建角色再来签到。")
    rid = role["id"]
    if today_count(EXT_ID, "签到", rid) >= 1:
        await signin.finish(f"{role['name']}，今天已经签过了，明天再来。")

    now = _today_no()
    last = season_data_get(EXT_ID, f"last_{rid}")
    if last is None:                        # 1.0 版存在一直留着的数据里：这一季还接着算，签一次就搬过来
        last = data_get(EXT_ID, f"last_{rid}", "")
    streak = int(season_data_get(EXT_ID, f"streak_{rid}", data_get(EXT_ID, f"streak_{rid}", 0))) + 1 if _is_next(last, now) else 1

    cap = max(1, int(setting(EXT_ID, "streak_cap") or 1))
    amount = max(0, int(setting(EXT_ID, "reward") or 0)) + max(0, int(setting(EXT_ID, "streak_bonus") or 0)) * (min(streak, cap) - 1)
    currency = setting(EXT_ID, "currency") or "金币"
    if amount > 0:
        ok, msg = give_item(rid, currency, amount, "每日签到")
        if not ok:
            await signin.finish(f"签到失败：{msg}（请管理员检查「每日签到」设置里的「发什么」）")

    today_add(EXT_ID, "签到", rid)
    season_data_set(EXT_ID, f"last_{rid}", now)
    season_data_set(EXT_ID, f"streak_{rid}", streak)
    data_set(EXT_ID, f"last_{rid}", None)   # 1.0 版留下的旧记录删掉
    data_set(EXT_ID, f"streak_{rid}", None)
    tail = f"连续签到 {streak} 天。" if streak > 1 else ""
    await signin.finish(f"✅ {role['name']} 签到成功，获得 {currency}×{amount}。{tail}现在有 {item_count(rid, currency)} 个{currency}。")


def _pending(role) -> list[str]:
    if not setting(EXT_ID, "remind"):
        return []
    return ["今天还没「签到」"] if today_count(EXT_ID, "签到", role["id"]) == 0 else []


register_pending("签到", _pending)
