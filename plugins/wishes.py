"""心愿（官方扩展，可在「功能与设置」的「额外」里开关，默认关闭，改了要重启）：挂心愿、悬赏心愿、看心愿、摘心愿、撤心愿。
规则和数据在 core/wishes.py；摘走后开的约会房间走 play.py 里的房间逻辑。开放时段、数量上限在设置里的「心愿」。"""
import re

import nonebot  # noqa: F401
from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg  # noqa: F401

from core import announce, rooms, wishes
from core.db import get_config
from plugins._long import make_send, reply_long
from plugins.play import NEED_ROLE, _announce_in_room, _cmd, _group_line, _invite, _me, _push, _room_arg, _slot

def _wish_line(w) -> str:
    who = w["sign"] or w["poster_name"]
    return f"#{w['id']} D{w['day']} {w['start_hm']}-{w['end_hm']} @{w['place']}：{w['content']}" + (f"｜悬赏：{w['reward']}" if w["reward"] else "") + f"（{who}）"


async def _post_wish(matcher, bot, event, args, bounty: bool):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    text = args.extract_plain_text().strip()
    sign = ""
    reward = ""
    if "|" in text or "｜" in text:
        text, reward = re.split(r"[|｜]", text, 1)
        text, reward = text.strip(), reward.strip()
    if bounty and not reward:
        await matcher.finish("用法：悬赏心愿 时间 地点 内容 | 奖励\n例：悬赏心愿 1400-1500 图书馆 陪我看书 | 滋补汤 1")
    p = text.split(None, 2)
    if len(p) < 3:
        await matcher.finish("用法：挂心愿 时间 地点 内容\n例：挂心愿 1400-1500 花园 一起散步")
    ok, info = wishes.post(me["id"], p[0], p[1], p[2], reward, sign)
    if not ok:
        await matcher.finish(f"❌ {info}")
    if get_config("wish_public_send", "0") == "1":
        ann = announce.get_announce()
        if ann:
            try:
                await make_send()(ann[0], ann[1], "📌 新心愿 " + _wish_line(wishes.get(int(info))))
            except Exception as e:  # noqa: BLE001
                logger.warning("心愿公开播报失败：{}", e)
    await matcher.finish(f"✅ 心愿 #{info} 已挂出，等人来摘。" + ("\n（奖励只是登记，兑现靠你们自己，机器人不转物品。）" if reward else ""))


wish_post = _cmd("挂心愿")
wish_bounty = _cmd("悬赏心愿")
wish_list = _cmd("看心愿")
wish_take = _cmd("摘心愿")
wish_back = _cmd("撤心愿")


@wish_post.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _post_wish(wish_post, bot, event, args, False)


@wish_bounty.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _post_wish(wish_bounty, bot, event, args, True)


@wish_list.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    ws = wishes.list_open()
    await reply_long(wish_list, "🌠 开放的心愿\n" + "\n".join(_wish_line(w) for w in ws) + "\n摘一个发「摘心愿 编号」" if ws else "现在没有开放的心愿。")


@wish_take.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_take.finish(NEED_ROLE)
    n = _room_arg(args.extract_plain_text())
    if not n:
        await wish_take.finish("用法：摘心愿 编号")
    ok, info, room_id = wishes.take(n, me["id"])
    if not ok:
        await wish_take.finish(f"❌ {info}")
    w, room = wishes.get(n), rooms.get(room_id)
    await _push(w["poster_id"], f"🌠 你的心愿 #{n} 被 {me['name']} 摘走了！")
    await _invite(w["poster_id"], room, fix=("💌 心愿", "💌 心愿约会"))
    await _announce_in_room(room_id)
    await wish_take.finish(f"✅ 你摘了心愿 #{n}，{w['poster_name']} 已收到通知。\n{_slot(room)}\n{_group_line(room)}\n房间号 R{room_id}")


@wish_back.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_back.finish(NEED_ROLE)
    n = _room_arg(args.extract_plain_text())
    if not n:
        await wish_back.finish("用法：撤心愿 编号")
    ok, why = wishes.withdraw(n, me["id"])
    await wish_back.finish(f"✅ 心愿 #{n} 已撤回。" if ok else f"❌ {why}")


