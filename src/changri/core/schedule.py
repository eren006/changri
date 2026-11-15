"""定时任务的逻辑（不依赖 QQ/NoneBot，方便测试）：心动信到点派送，以及派送前的两条提醒。"""
import logging
import time

from . import lovemail, messaging
from .announce import get_announce
from .db import get_config, get_conn, set_config

log = logging.getLogger("changri.schedule")


async def run_lovemail_tick(send: messaging.Send, now: time.struct_time | None = None) -> str:
    """每 30 秒调用一次；返回这一轮做了什么（方便测试）。"""
    if not lovemail.enabled():
        return "off"
    now = now or time.localtime()
    cur = now.tm_hour * 60 + now.tm_min
    try:
        h, m = (get_config("lovemail_delivery_time", "22:00").replace('"', "").strip() or "22:00").split(":")
        target = int(h) * 60 + int(m)
    except ValueError:
        return "bad-time"
    day = time.strftime("%Y-%m-%d", now)
    announce = get_announce()
    manual = get_config("lovemail_deliver_now", "0") == "1"

    if cur == (target - 10) % 1440 and get_config("lovemail_notice10_day") != day:
        set_config("lovemail_notice10_day", day)
        with get_conn() as conn:
            gids = [(r[0], r[1]) for r in conn.execute("SELECT bot_id, group_openid FROM role_groups WHERE kind='main' AND group_openid IS NOT NULL")]
        for bot_id, gid in gids:
            try:
                await send(bot_id, gid, "⌛ 投递截止预告：\n心动信箱将于 10 分钟后截止收稿并开始派送，还没投递的小伙伴要抓紧哦～")
            except Exception as e:  # noqa: BLE001
                log.warning(f"[心动信] 截止预告发送失败：{e}")
        return "notice10"
    if cur == (target - 5) % 1440 and get_config("lovemail_notice5_day") != day:
        set_config("lovemail_notice5_day", day)
        if announce:
            try:
                await send(*announce, "📬 邮差正在整理信箱，心动信件即将在 5 分钟后开始派送，请注意查收。")
            except Exception as e:  # noqa: BLE001
                log.warning(f"[心动信] 公告群提醒发送失败：{e}")
        return "notice5"
    # 到点派送：目标时间之后 1 小时内没跑过就补跑（机器人刚好重启也不会漏）；后台「立即派送」也走这里
    due = target <= cur < target + 60 and get_config("lovemail_last_run") != day
    if due or manual:
        set_config("lovemail_last_run", day)
        set_config("lovemail_deliver_now", "0")
        res = await lovemail.deliver_all(send, announce)
        tip = get_config("daily_tip", "").strip()
        if tip and announce and tip != "关闭":
            try:
                await send(*announce, tip)
            except Exception as e:  # noqa: BLE001
                log.warning(f"[心动信] 每日提示发送失败：{e}")
        log.info(f"[心动信] 派送：{res}")
        return "delivered"
    return "idle"
