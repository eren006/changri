"""往 QQ 群里发消息的工具（原 plugins/_long.py）：长内容画成图片发（官方机器人没有合并转发），画不出来或发不出去就退回文字；
@人、撤回、下载消息里的图片。群指令、设置平台、小手机都从这里发。"""
import asyncio
import collections
import re
import threading
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


_render_lock = threading.Lock()          # 署名是模块里的全局变量，几个线程同时画会串：一次只画一张
_render_cache: collections.OrderedDict = collections.OrderedDict()     # (署名, 文字) → 图；自动复盘给每个参与者发同一份，只画一次
_RENDER_CACHE_N = 4


def _render(text: str) -> list[bytes]:
    """画图很慢（2 万字要 4 秒左右），只能在线程里调（见 _render_async），别在事件循环里直接调。"""
    from changri.core.render import forward_image as F
    with _render_lock:
        F._apply_brand()
        key = (F.BRAND_LABEL, text)
        if key in _render_cache:
            _render_cache.move_to_end(key)
            return _render_cache[key]
        pngs, _cut = F.render_forward([{"name": F.BRAND, "text": text}])
        pngs = pngs[:5]       # 被动回复同一条消息最多 5 次
        _render_cache[key] = pngs
        while len(_render_cache) > _RENDER_CACHE_N:
            _render_cache.popitem(last=False)
        return pngs


async def _render_async(text: str) -> list[bytes]:
    """放到线程里画：画的时候机器人照样收消息、回指令。"""
    return await asyncio.to_thread(_render, text)


async def send_to_group(bot, group_openid: str, text: str) -> str:
    """主动发到某个群：够长就发图。图发了一半失败就不再补文字，免得重复。返回第一条消息的 id（撤回要用，拿不到就是空串）。"""
    first = ""
    wanted = _image_wanted(text)
    text = _strip_img(text)
    if wanted:
        sent = 0
        try:
            for png in await _render_async(text):
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
            for png in await _render_async(text):
                await matcher.send(MessageSegment.file_image(png))
                sent += 1
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[长图] 画图或发图失败，改发文字：{e}")
            if sent:
                return
    for part in chunk_text(text)[:5]:
        await matcher.send(part)


BTN_MD_MAX = 1200      # 正文再长就按原来的方式发（长图 / 拆文字），按钮另发一条短的
_btn_off_until = 0.0


async def reply_buttons(matcher, text: str, btns, per_row: int = 3, tail: str = "👇 点下面的按钮", fallback: bool = True) -> bool:
    """回复一段文字并在下面挂按钮（见 core/buttons.py）。
    · 关了按钮、没有按钮、QQ 拒绝过（多半没 Markdown 权限，一小时内不再试）：原样走 reply_long，玩家照文字提示手打指令；
    · QQ 明确拒绝：消息一定没发出去，退回纯文字；其他异常（超时、回包出错）可能已经发出去了，不补发，免得重复；
    · 正文太长：先照原来的方式发正文，再另发一条短的「tail」挂按钮（这条失败了就算了，正文里有手打的提示）。
    返回按钮是否（可能）发出去了。fallback=False：发不了按钮时什么都不发，由调用方换别的办法（比如公知改发长图）。"""
    global _btn_off_until
    from nonebot.adapters.qq import Message
    from nonebot.adapters.qq.exception import ActionFailed
    from nonebot.adapters.qq.models import MessageKeyboard

    from changri.core import buttons

    btns = [b for b in (btns or []) if b]
    if not btns or not buttons.enabled() or time.time() < _btn_off_until:
        if fallback:
            await reply_long(matcher, text)
        return False
    too_long = len(_strip_img(text)) > BTN_MD_MAX or text.startswith(IMG)
    if too_long:
        await reply_long(matcher, text)
    md, kb = buttons.build(tail if too_long else _strip_img(text), btns, per_row)
    try:
        await matcher.send(Message([MessageSegment.markdown(md), MessageSegment.keyboard(MessageKeyboard.model_validate(kb))]))
    except ActionFailed as e:
        _btn_off_until = time.time() + 3600
        logger.warning("[按钮] 被 QQ 拒绝（多半是机器人没有群 Markdown / 按钮权限），一小时内改发纯文字：" + ascii(str(e))[:200])
        if fallback and not too_long:
            await reply_long(matcher, text)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("[按钮] 发送出现异常（可能已发出，不再补发纯文字）：" + type(e).__name__ + " " + ascii(str(e))[:200])
        return True
    if not too_long:
        try:
            from changri.plugins._logtap import tap_reply
            tap_reply(matcher, text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[跑团log] 记录回复出错：{e}")
    return True


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


async def _send_with_buttons(bot, group_openid: str, text: str, btns) -> str | None:
    """主动发到群、下面挂按钮（短信、礼物、关系线通知的「回复」）。文本开头的 {{AT:…}} 变成 Markdown 里的真 @。
    发不了（没开按钮、QQ 拒绝过、内容太长）返回 None，调用方照原来的方式发；QQ 明确拒绝时一小时内不再试（和 reply_buttons 共用）。"""
    global _btn_off_until
    from nonebot.adapters.qq import Message
    from nonebot.adapters.qq.exception import ActionFailed
    from nonebot.adapters.qq.models import MessageKeyboard

    from changri.core import buttons

    m = AT_RE.match(text)
    body = text[m.end():] if m else text
    if not btns or not buttons.enabled() or time.time() < _btn_off_until or _image_wanted(body) or len(body) > BTN_MD_MAX:
        return None
    md, kb = buttons.build(body, btns, per_row=3)
    if m:
        md = f'<qqbot-at-user id="{m.group(1)}" />  \n' + md
    try:
        r = await bot.send_to_group(group_openid=group_openid,
                                    message=Message([MessageSegment.markdown(md), MessageSegment.keyboard(MessageKeyboard.model_validate(kb))]))
    except ActionFailed as e:
        _btn_off_until = time.time() + 3600
        logger.warning("[按钮] 主动消息带按钮被 QQ 拒绝，一小时内改发纯文字：" + ascii(str(e))[:200])
        return None
    return getattr(r, "id", "") or ""


def make_send():
    """按群所属的机器人选发送者（定时任务、出场用）。文本开头写 {{AT:成员openid:名字}} 就会在开头 @ 这个人。
    buttons=[Btn…] 时在消息下面挂按钮，发不了就照常发文字（见 _send_with_buttons）。"""
    import nonebot

    async def send(bot_id: str, group_openid: str, text: str, buttons=None) -> str:
        bot = nonebot.get_bots().get(bot_id)
        if bot is None:
            raise RuntimeError(f"机器人 {bot_id} 当前不在线")
        if buttons:
            mid = await _send_with_buttons(bot, group_openid, text, buttons)
            if mid is not None:
                return mid
        m = AT_RE.match(text)
        if m:
            return await send_at(bot, group_openid, m.group(1), m.group(2), text[m.end():])
        return await send_to_group(bot, group_openid, text)

    send.buttons_ok = True          # messaging.push_to_role_ex 看这个决定传不传按钮（测试里的假 send 没有）
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
