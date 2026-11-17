"""往 QQ 群里发消息的工具（原 plugins/_long.py）：长内容画成图片发（官方机器人没有合并转发），画不出来或发不出去就退回文字；
@人、撤回、下载消息里的图片。群指令、设置平台、小手机都从这里发。"""
import re
import time

from nonebot import logger
from nonebot.adapters.qq import MessageSegment

from changri.core.db import get_config, get_int
from changri.core.textutil import chunk_text


IMG = "{{IMG}}"        # 文本开头带这个标记：不管多长都画成图发（复盘用）；画不出来就退回文字


def _image_wanted(text: str) -> bool:
    """一条消息发得完（≤ textutil.CHUNK 字）就发文字；要拆成好几条时才画成图（后台可以关掉，或再调高字数下限）。"""
    if text.startswith(IMG):
        return True
    return (get_config("long_as_image", "1") == "1" and len(chunk_text(text)) > 1
            and len(text) > get_int("image_min_chars", 200))


def _strip_img(text: str) -> str:
    return text[len(IMG):] if text.startswith(IMG) else text


def _render(text: str) -> list[bytes]:
    from changri.core.render import forward_image as F
    F._apply_brand()
    pngs, _cut = F.render_forward([{"name": F.BRAND, "text": text}])
    return pngs[:5]       # 被动回复同一条消息最多 5 次


async def send_to_group(bot, group_openid: str, text: str) -> str:
    """主动发到某个群：够长就发图。图发了一半失败就不再补文字，免得重复。返回第一条消息的 id（撤回要用，拿不到就是空串）。"""
    first = ""
    wanted = _image_wanted(text)
    text = _strip_img(text)
    if wanted:
        sent = 0
        try:
            for png in _render(text):
                r = await bot.send_to_group(group_openid=group_openid, message=MessageSegment.file_image(png))
                first = first or (getattr(r, "id", "") or "")
                sent += 1
            return first
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[长图] 画图或发图失败，改发文字：{e}")
            if sent:
                return first
    r = await bot.send_to_group(group_openid=group_openid, message=text)
    return getattr(r, "id", "") or ""


async def reply_long(matcher, text: str, as_image: bool = True) -> None:
    """回复触发指令的人：够长就发图，否则按 5 条以内的文字发。as_image=False 时不管多长都发文字（制卡这类要能复制的）。"""
    try:
        from changri.plugins._logtap import tap_reply
        tap_reply(matcher, text)                  # 跑团 log 在记时，骰子回复也进 log
    except Exception as e:  # noqa: BLE001 记 log 出错不能影响回复
        logger.warning(f"[跑团log] 记录回复出错：{e}")
    wanted = as_image and _image_wanted(text)
    text = _strip_img(text)
    if wanted:
        sent = 0
        try:
            for png in _render(text):
                await matcher.send(MessageSegment.file_image(png))
                sent += 1
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[长图] 画图或发图失败，改发文字：{e}")
            if sent:
                return
    for part in chunk_text(text)[:5]:
        await matcher.send(part)


AT_RE = re.compile(r"\A\{\{AT:(\w+):([^}]*)\}\}")
_md_off_until = 0.0


async def send_at(bot, group_openid: str, member_openid: str, name: str, text: str) -> str:
    """开头 @ 一个人。官方机器人只有 markdown 消息里的 <qqbot-at-user> 才是真 @（要机器人有群 markdown 权限）；
    没权限或被拒，就退回纯文字「@名字」，并且一小时内不再试 markdown。群消息里的 <@openid> 不会渲染成 @，所以不用。
    返回发出去的那条消息的 id（撤回要用）。内容很长要发图时：先单发一条 @，图另发，返回图的 id。"""
    global _md_off_until
    if _image_wanted(text):
        await send_at(bot, group_openid, member_openid, name, "")
        return await send_to_group(bot, group_openid, text)
    if time.time() >= _md_off_until:
        try:
            r = await bot.send_to_group(group_openid=group_openid,
                                        message=MessageSegment.markdown(f'<qqbot-at-user id="{member_openid}" />\n{text}'))
            return getattr(r, "id", "") or ""
        except Exception as e:  # noqa: BLE001
            _md_off_until = time.time() + 3600
            logger.warning(f"[@] markdown 发送失败（多半是机器人没有群 markdown 权限），一小时内改发纯文字 @：{e}")
    r = await bot.send_to_group(group_openid=group_openid, message=f"@{name} {text}")
    return getattr(r, "id", "") or ""


def make_send():
    """按群所属的机器人选发送者（定时任务、出场用）。文本开头写 {{AT:成员openid:名字}} 就会在开头 @ 这个人。"""
    import nonebot

    async def send(bot_id: str, group_openid: str, text: str) -> str:
        bot = nonebot.get_bots().get(bot_id)
        if bot is None:
            raise RuntimeError(f"机器人 {bot_id} 当前不在线")
        m = AT_RE.match(text)
        if m:
            return await send_at(bot, group_openid, m.group(1), m.group(2), text[m.end():])
        return await send_to_group(bot, group_openid, text)

    return send


def make_send_image():
    """发一张图（bytes）到群，按群所属的机器人选发送者。"""
    import nonebot

    async def send_image(bot_id: str, group_openid: str, data: bytes) -> None:
        bot = nonebot.get_bots().get(bot_id)
        if bot is None:
            raise RuntimeError(f"机器人 {bot_id} 当前不在线")
        await bot.send_to_group(group_openid=group_openid, message=MessageSegment.file_image(data))

    return send_image


async def download_images(message, limit: int = 3, max_bytes: int = 8 * 1024 * 1024) -> list[bytes]:
    """把消息里带的图片下载下来（QQ 的图片链接会过期，所以收到就存）。下不下来的跳过。"""
    import httpx

    urls = []
    for seg in message:
        if seg.type == "image" and seg.data.get("url"):
            u = seg.data["url"]
            urls.append(u if u.startswith("http") else "https://" + u)
    out: list[bytes] = []
    if not urls:
        return out
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for u in urls[:limit]:
            try:
                r = await client.get(u)
                if r.status_code == 200 and 0 < len(r.content) <= max_bytes:
                    out.append(r.content)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[图片] 下载失败：{e}")
    return out


async def recall_group_message(bot, group_openid: str, message_id: str) -> None:
    """撤回机器人发在群里的一条消息（官方接口只允许 2 分钟内）。适配器没封装群消息撤回，照 delete_c2c_message 的写法直接发 DELETE。"""
    from nonebot.adapters.qq.exception import ActionFailed  # noqa: F401
    from nonebot.drivers import Request

    request = Request("DELETE", bot.adapter.get_api_base().joinpath("v2", "groups", group_openid, "messages", message_id))
    await bot._request(request)
