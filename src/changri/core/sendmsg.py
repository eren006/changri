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
        gift, why = gifts.resolve(gift, gift_by_name_ok, role_id=me["id"])       # 礼品店的礼物只能送图鉴里有的
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
    original = content
    to_id = target["id"]
    if chaos:
        content, to_id = chaos["shown"], chaos["true_to"]
        sign = chaos["sign"] if chaos["sign"] != me["name"] else sign
    lost = False
    if kind == "gift":
        lost, to_id = _gift_chaos(me, target)
    mid = messaging.queue_message(kind, me["id"], to_id, gift, content, sign)
    if to_id != target["id"] or content != original:          # 出了岔子：发件人的小手机按他原本写的、原本发给谁显示，不穿帮
        messaging.set_original(mid, target["id"] if to_id != target["id"] else 0, original if content != original else None)
    for data in images:
        msgimages.save(mid, data)
    if chaos:                                                 # 后续（撕走的半截、公告）等真送到了再做，补送成功的也做
        from . import chaosletter
        chaosletter.attach(chaos.get("log_id", 0), mid, chaos)
    tail = f"（今天 {used + 1}/{lim}）" if lim > 0 else ""
    ok_text = f"已送达「{target['name']}」。{tail}发错了 2 分钟内可以撤回。"
    blk = blocks.get_block(to_id, me["id"])          # 看的是最后真正会收到的人有没有拉黑我（送错了人就看那个人）
    if blk:
        messaging.mark_blocked(mid, bool(blk["silent"]))
        if blk["silent"]:    # 静默：发件人看到和正常送达一模一样的提示
            return True, ok_text
        from . import roles
        got = roles.get_role(to_id)
        return False, f"❌ {got['name'] if got else target['name']} 已拒绝你的联络。"
    from . import milestone
    milestone.bump(KIND_NAME[kind])                          # 「第 N 次记录」公告
    if lost:                 # 礼物在路上丢了：谁也没收到，送的人看到的和正常送达一样，次数和冷却照算
        messaging.mark_lost(mid)
        return True, ok_text
    if await messaging.deliver(mid, send_fn, shown_to=target["id"] if to_id != target["id"] and kind == "gift" else 0):
        await after_delivered(mid, send_fn)
        return True, ok_text
    return True, f"「{target['name']}」那边暂时收不到，已记下，稍后会自动重试。"


# ---------- 混乱短信（额外）：开着时短信可能出岔子 ----------

def _gift_chaos(me, target, rng=None) -> tuple[bool, int]:
    """开了「混乱短信」时礼物也可能出岔子：先掷「丢失」，没丢再掷「送错人」（随机换成另一位嘉宾）。
    返回 (丢了没有, 真正收到的人)。两个概率默认都是 0。"""
    import random

    from . import chaosletter, extensions
    rng = rng or random
    if not extensions.is_enabled("chaos_letter"):
        return False, target["id"]
    if rng.random() * 100 < chaosletter.pct("chaos_gift_lost", 0):
        return True, target["id"]
    if rng.random() * 100 < chaosletter.pct("chaos_gift_mis", 0):
        other = chaosletter.pick_others({me["id"], target["id"]}, rng)
        if other:
            return False, other
    return False, target["id"]


def _chaos(me, target, kind: str, content: str, sign: str):
    """开了「混乱短信」就按概率改这条短信；没出岔子（或没开、不是短信、只有图片）返回 None。"""
    from . import chaosletter, extensions
    if kind != "sms" or not content.strip() or not extensions.is_enabled("chaos_letter"):
        return None
    shown_from = dict(me) if not sign else {**dict(me), "name": sign}       # 自定义署名的，落款写错时换掉的是署名
    c = chaosletter.compose(shown_from, target, content)
    if not c["effects"]:
        return None
    c["log_id"] = chaosletter.log(me["id"], target["id"], c, content)
    return c


async def after_delivered(mid: int, send_fn) -> None:
    """短信 / 礼物真送到对方群之后要做的事：截信器（发件人身上）、回音壁（收件人身上）、混乱短信的后续。
    当场送到的和后来自动重试送到的都走这里（messaging.retry_pending 回调）。匿名短信只做混乱后续，不触发截信器 / 回音壁（会露出化名背后是谁）。"""
    from .db import get_conn
    with get_conn() as c:
        m = c.execute("SELECT m.*, f.name AS from_name, t.name AS to_name FROM messages m JOIN roles f ON f.id=m.from_role_id "
                      "JOIN roles t ON t.id=m.to_role_id WHERE m.id=?", (mid,)).fetchone()
    if not m or m["kind"] != "sms":
        return
    shown_from = m["sign"] or m["from_name"]
    if not (m["alias_id"] or m["orig_alias"]):
        from . import specials
        specials.on_sms(m["from_role_id"], m["to_role_id"], shown_from, m["to_name"], m["content"] or "")
    await _chaos_after(mid, shown_from, send_fn)


async def _chaos_after(mid: int, shown_from: str, send_fn) -> None:
    """撕成两半的后半截送到第三个人；按设置公开播报出了什么岔子。都是锦上添花，失败不影响短信本身。只做一次。"""
    import random

    from . import announce, chaosletter
    c = chaosletter.take_after(mid)
    if not c:
        return
    try:
        if c["torn"]:
            third, tail = c["torn"]
            chaosletter.log_torn(c["log_id"], third, tail, c["sign"])
            await messaging.push_to_role(third, f"📩 一条撕坏的短信飘到了你这里\n……{tail}\n落款：{c['sign']}", send_fn)
        if get_config("letter_public_send", "1") == "1" and random.random() * 100 < get_int("letter_public_chance", 50):
            ann = announce.get_announce()
            if ann:
                await send_fn(ann[0], ann[1], f"📰 信号出了岔子：{shown_from} 发给 {c['to_name']} 的短信，{'、'.join(c['effects'])}。")
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("[混乱短信] 后续处理失败：%s", e)


async def recall(m) -> None:
    """撤回 messaging.last_recallable 找到的那一条：先撤文字（失败就抛出去，调用方告诉玩家），再撤跟着发的图片（撤不掉不影响）。"""
    import nonebot

    from .qqsend import recall_group_message
    bot = nonebot.get_bots().get(m["delivered_bot"])
    if bot is None:
        raise RuntimeError("对方群所在的机器人现在不在线")
    await recall_group_message(bot, m["delivered_group"], m["delivered_msg_id"])
    for img in (m["delivered_img_ids"] or "").split(","):
        if img:
            try:
                await recall_group_message(bot, m["delivered_group"], img)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning("[撤回] 图片没撤掉：%s", e)
    messaging.mark_recalled(m["id"])
