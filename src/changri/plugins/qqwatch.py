"""QQ 号断线提醒：掉线超过 2 分钟，用卡拉往公告群发一条；恢复了再发一条。不发就记日志。
掉线备案（core/outage.py，默认开）：掉线时通知所有玩家暂停 QQ 群回戏、自动打开小手机并发激活码；恢复后关回去、@ 大家回 QQ 群。"""
import time

import nonebot
from nonebot import logger

from changri.core import announce, messaging, outage, qqmanager
from changri.core.db import current_bot, get_int, per_bot
from changri.core.qqsend import make_send, send_to_group

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


async def _notify(text: str) -> None:
    ann = announce.get_announce()
    bot = nonebot.get_bots().get(ann[0]) if ann else None
    if not bot:
        logger.warning(f"[QQ号监听] {text}（没有设公告群或机器人不在线，只记日志）")
        return
    try:
        await send_to_group(bot, ann[1], text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[QQ号监听] 提醒发送失败：{e}")


async def _notify_cmd(app_id: str, text: str) -> None:
    from changri.core.db import use_bot
    with use_bot(app_id):
        await _notify(text)


if _notify_cmd not in qqmanager._notifiers:
    qqmanager.add_notifier(_notify_cmd)


@scheduler.scheduled_job("interval", seconds=30, id="qqlink_watch", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    from changri.core import qqpool
    secs = get_int("qqlink_alert_secs", 120)
    for ln in qqpool.links():
        if not ln["enabled"]:
            continue
        res = qqmanager.watch(current_bot(), time.time(), secs, ln["id"])
        if not res:
            continue
        kind, reason = res
        st = qqmanager.link_status(current_bot(), ln["id"])
        who = f"QQ {st['self_id']}（{ln['name']}）" if st["self_id"] else f"监听用的 QQ 号「{ln['name']}」"
        await _notify(f"⚠️ {who} 掉线了：{reason}。听群消息、改群名暂时不能用，管理员请处理。" if kind == "alert" else f"✅ {who} 已恢复连接。")
        if kind == "alert" and outage.enabled():
            await _fallback_on(ln["id"])
        elif kind != "alert" and ln["id"] in outage.down_links():
            await _fallback_off(ln["id"])


async def _tell_players(text_for) -> int:
    """给每个玩家的个人群发一条（@ 本人）。text_for(role_id) -> 文字。返回送达几个人。"""
    send, n = make_send(), 0
    for rid in outage.players():
        try:
            ok, *_ = await messaging.push_to_role_ex(rid, text_for(rid), send, True)
            n += bool(ok)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[掉线备案] 通知角色 {rid} 失败：{e}")
    return n


async def _fallback_on(link_id: int) -> None:
    res = outage.start(link_id)
    if not res["first"]:
        return                                                          # 已经有号掉线、大家已经切到小手机了
    n = await _tell_players(outage.down_text)
    missing = outage.ready()
    tail = "" if not missing else "\n⚠️ 掉线备案没准备好：" + "；".join(missing) + "。玩家可能打不开小手机。"
    await _notify(("📱 已自动打开小手机，" if res["phone_opened"] else "📱 ") + f"已通知 {n} 位玩家暂停 QQ 群回戏、到小手机上接着写（激活码已发到各自的个人群）。" + tail)


async def _fallback_off(link_id: int) -> None:
    res = outage.end(link_id)
    if not res["all_clear"]:
        return
    n = await _tell_players(lambda rid: outage.UP_TEXT)
    await _notify(f"✅ 已通知 {n} 位玩家回 QQ 群接着回戏" + ("，自动打开的小手机已关回去。" if res["phone_closed"] else "。"))
