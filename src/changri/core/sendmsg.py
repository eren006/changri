"""发短信/送礼的规则，QQ 指令和小手机网页共用同一份：时段、长度、礼品店、每日上限、冷却、拉黑、投递。
返回 (成功入队, 要告诉发件人的话)。「成功入队」= 记下了（包括静默拉黑、对方暂时收不到先记下重试）。"""
from . import blocks, gifts, godmode, messaging, msgimages, windows
from .db import get_config, get_int

KIND_NAME = {"sms": "短信", "gift": "礼物"}


def precheck(me, target, kind: str, gift: str, content: str, has_image: bool = False) -> str:
    """能发返回空串，否则返回原因（不会入队）。"""
    closed = windows.check("sms") or godmode.check(me["id"], kind)
    if closed:
        return closed
    if target["id"] == me["id"]:
        return "不能发给自己。"
    limit_len = get_int("msg_max_len", 200)
    if len(content) > limit_len or (kind == "gift" and len(gift) > 30):
        return f"内容太长了，最多 {limit_len} 字（礼物名最多 30 字）。"
    if kind == "sms" and not content.strip() and not has_image:
        return "短信内容不能为空。"
    if kind == "gift" and not gift.strip():
        return "要写送什么礼物。"
    if kind == "gift" and gifts.only_preset() and gift not in gifts.catalog():
        return "礼物要从礼品店里选，发「礼品店」看有什么。"
    lim = messaging.daily_limit(kind)
    used = messaging.day_count(me["id"], kind)
    if lim > 0 and used >= lim:
        return f"今天的{KIND_NAME[kind]}次数用完了（{used}/{lim}），天数更新后再发。"
    left = messaging.cooldown_left(me["id"], kind)
    if left:
        return f"发得太快了，{left} 秒后再试。"
    return ""


async def send(me, target, kind: str, gift: str, content: str, sign: str, send_fn, images: list[bytes] | None = None, gift_by_name_ok: bool = False) -> tuple[bool, str]:
    if kind == "gift":
        gift, why = gifts.resolve(gift, gift_by_name_ok)
        if why:
            return False, why
    images = list(images or [])[:1]                          # 每条最多 1 张
    for data in images:
        why = msgimages.check(data)
        if why:
            return False, why
    bad = precheck(me, target, kind, gift, content, has_image=bool(images))
    if bad:
        return False, bad
    sign = sign if sign and sign != me["name"] else ""
    lim = messaging.daily_limit(kind)
    used = messaging.day_count(me["id"], kind)
    chaos = _chaos(me, target, kind, content, sign)
    to_id = target["id"]
    if chaos:
        content, to_id = chaos["shown"], chaos["true_to"]
        sign = chaos["sign"] if chaos["sign"] != me["name"] else sign
    mid = messaging.queue_message(kind, me["id"], to_id, gift, content, sign)
    for data in images:
        msgimages.save(mid, data)
    tail = f"（今天 {used + 1}/{lim}）" if lim > 0 else ""
    ok_text = f"已送达「{target['name']}」。{tail}发错了 2 分钟内可以撤回。"
    blk = blocks.get_block(target["id"], me["id"])   # 对方有没有拉黑我
    if blk:
        messaging.mark_blocked(mid, bool(blk["silent"]))
        if blk["silent"]:    # 静默：发件人看到和正常送达一模一样的提示
            return True, ok_text
        return False, f"❌ {target['name']} 已拒绝你的联络。"
    if await messaging.deliver(mid, send_fn):
        if kind == "sms":                                     # 截信器（发件人身上）、回音壁（收件人身上）
            from . import roles, specials
            got = roles.get_role(to_id)
            specials.on_sms(me["id"], to_id, sign or me["name"], (got["name"] if got else target["name"]), content)
        if chaos:
            await _chaos_after(me, target, chaos, sign, send_fn)
        return True, ok_text
    return True, f"「{target['name']}」那边暂时收不到，已记下，稍后会自动重试。"


# ---------- 混乱短信（额外）：开着时短信可能出岔子 ----------

def _chaos(me, target, kind: str, content: str, sign: str):
    """开了「混乱短信」就按概率改这条短信；没出岔子（或没开、不是短信、只有图片）返回 None。"""
    from . import chaosletter, extensions
    if kind != "sms" or not content.strip() or not extensions.is_enabled("chaos_letter"):
        return None
    shown_from = dict(me) if not sign else {**dict(me), "name": sign}       # 自定义署名的，落款写错时换掉的是署名
    c = chaosletter.compose(shown_from, target, content)
    if not c["effects"]:
        return None
    chaosletter.log(me["id"], target["id"], c, content)
    return c


async def _chaos_after(me, target, c, sign: str, send_fn) -> None:
    """撕成两半的后半截送到第三个人；按设置公开播报出了什么岔子。都是锦上添花，失败不影响短信本身。"""
    import random

    from . import announce
    try:
        if c["torn"]:
            third, tail = c["torn"]
            await messaging.push_to_role(third, f"📩 一条撕坏的短信飘到了你这里\n……{tail}\n落款：{c['sign'] or sign or me['name']}", send_fn)
        if get_config("letter_public_send", "1") == "1" and random.random() * 100 < get_int("letter_public_chance", 50):
            ann = announce.get_announce()
            if ann:
                await send_fn(ann[0], ann[1], f"📰 信号出了岔子：{sign or me['name']} 发给 {target['name']} 的短信，{'、'.join(c['effects'])}。")
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("[混乱短信] 后续处理失败：%s", e)
