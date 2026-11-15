"""点歌（额外扩展，默认关闭）：回复音乐分享卡片发「点歌」，或「点歌 歌名」让机器人搜；发到公告群。规则在 core/song.py。

公告群那条是主动消息：先发图文卡片（msg_type 8，nonebot-adapter-qq 1.7.1 还不认识，直接调原始接口），
发不出去就退成带链接的文字，再不行退成不带链接的文字。
"""
import asyncio

import nonebot
from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.drivers import Request
from nonebot.params import CommandArg

from changri.core import announce, blocks, roles, song
from changri.core.db import get_config
from changri.plugins.play import NEED_ROLE, _cmd, _me

USAGE = ("🎵 点歌\n"
         "① 先把网易云 / QQ 音乐的歌分享到群里，回复那张卡片发：点歌 [给 某人] [寄语]\n"
         "② 直接搜：点歌 歌名 [歌手] [给 某人] [寄语]\n"
         "例：点歌 晴天 周杰伦 给 张三 生日快乐\n"
         "例：点歌 网易 起风了 给大家\n"
         "③ B 站：分享 → 复制链接，发：点歌 链接 [给 某人] [寄语]（或写 BV 号）\n"
         "回复卡片时也认：点歌 点歌人：小林 留言：送给你 送给：张三\n"
         "不写点歌人就是匿名，公告群里只写「有人」。")

pick = _cmd("点歌")


async def _post_group(bot: Bot, group_openid: str, body: dict) -> None:
    req = Request("POST", bot.adapter.get_api_base().joinpath("v2", "groups", group_openid, "messages"), json=body)
    await bot._request(req)


async def announce_song(req: dict, s: dict) -> str:
    """发到公告群。返回空串 = 发出去了，否则是原因。"""
    ann = announce.get_announce()
    if not ann:
        return "还没设置公告群"
    bot = nonebot.get_bots().get(ann[0])
    if bot is None:
        return "公告群所属的机器人不在线"
    tries = []
    card = song.card_body(req, s) if get_config("song_card", "1") == "1" else None
    if card:
        tries.append({"msg_type": 8, "card": card})
    tries.append({"msg_type": 0, "content": song.text_body(req, s, with_link=True)})
    tries.append({"msg_type": 0, "content": song.text_body(req, s, with_link=False)})
    last = ""
    for body in tries:
        try:
            await _post_group(bot, ann[1], body)
            return ""
        except Exception as e:  # noqa: BLE001
            last = f"{getattr(e, 'code', '') or ''} {getattr(e, 'message', '') or e}".strip()
            logger.warning(f"点歌发公告群失败（msg_type={body['msg_type']}）：{last}")
    return last or "发送失败"


@pick.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await pick.finish(NEED_ROLE)
    raw = args.extract_plain_text()
    bili, rest = song.find_bili(raw)                       # 「点歌 B站链接/BV号 …」
    if not bili:
        bili, _ = song.find_bili(song.quoted_text(event))   # 回复的是别人贴的 B 站链接
    req = song.parse_args(rest if bili else raw)
    card = None if bili else song.card_from_event(event)
    if not bili and not card and not req["keyword"]:
        q = song.quoted_text(event)
        if "哔哩哔哩" in q or "bilibili" in q.lower():
            await pick.finish("📺 B 站分享到 QQ 的小程序卡片里读不到视频链接。\n请在 B 站点「分享 → 复制链接」，然后发：点歌 粘贴的链接 [给 某人] [寄语]\n也可以直接写 BV 号：点歌 BV1xxxxxxxxx")
        await pick.finish(USAGE)
    bad = song.limit_reached(me["id"])
    if bad:
        await pick.finish(bad)
    if req["to"]:
        to = roles.get_role_by_name(req["to"])
        if not to:
            await pick.finish(f"❌ 找不到「{req['to']}」。想点给所有人就写「给大家」。")
        blk = blocks.get_block(to["id"], me["id"])
        if blk and not blk["silent"]:
            await pick.finish(f"❌ {to['name']} 已拒绝你的联络。")
        req["to"] = to["name"]
    if not announce.get_announce():
        await pick.finish("❌ 管理员还没设置公告群，点歌暂时发不出去。")

    if bili:
        try:
            s = await asyncio.to_thread(song.bili_lookup, bili)
        except RuntimeError as e:
            await pick.finish(f"❌ {e}")
        if not s:
            await pick.finish("❌ 这个 B 站链接没找到视频（只支持视频链接和 BV 号）。")
        source = "bili"
    elif card:
        s, source = await asyncio.to_thread(song.lookup, card), "card"
    else:
        try:
            s = await asyncio.to_thread(song.search, req["keyword"], req["platform"])
        except RuntimeError as e:
            await pick.finish(f"❌ {e}")
        if not s:
            await pick.finish(f"❌ 没搜到「{req['keyword']}」。试试加上歌手，或者回复一张音乐分享卡片来点。")
        source = "search"

    rid = song.record(me["id"], req, s, source)
    err = await announce_song(req, s)
    if err:
        logger.warning(f"点歌 #{rid} 没发到公告群：{err}")
        song.delete(rid)                     # 没发出去不算次数
        await pick.finish("❌ 点歌没能送到公告群，稍后再试。")
    song.mark_announced(rid)
    cap = song.daily_limit()
    used = song.day_count(me["id"])
    who = f"给 {req['to']}" if req["to"] else "给大家"
    await pick.finish(f"🎵 已经把《{s['title']}》{('- ' + s['artist']) if s.get('artist') else ''}点{who}，公告群里能看到。"
                      f"今天已点 {used}/{cap if cap > 0 else '不限'}。")
