"""信件（核心）：「寄信 收件人 内容」，有每日次数、冷却，出岔子时可以公开播报；这些在设置的「信件」里调。
开了「混乱寄信」扩展后，信才会错字、丢半截、涂黑、乱序、落款写错、误投、被撕成两半（概率在「混乱寄信」设置里）。规则在 core/chaosletter.py。"""
import random

from nonebot import logger
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import announce, blocks, chaosletter, extensions, messaging, roles
from changri.core.db import get_config, get_int
from changri.core.qqsend import make_send
from changri.plugins.play import NEED_ROLE, _cmd, _me

letter = _cmd("寄信")


@letter.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await letter.finish(NEED_ROLE)
    text = args.extract_plain_text().strip()
    name, _, content = text.partition(" ")
    content = content.strip()
    if not name or not content:
        await letter.finish("✉️ 寄信 收件人 内容\n例：寄信 阿林 今天的月色很好")
    to = roles.get_role_by_name(name)
    if not to:
        await letter.finish(f"❌ 找不到收信人「{name}」。")
    if to["id"] == me["id"]:
        await letter.finish("不能寄给自己。")
    limit_len = get_int("msg_max_len", 200)
    if len(content) > limit_len:
        await letter.finish(f"内容太长了，最多 {limit_len} 字。")
    cap = chaosletter.daily_limit()
    if cap > 0 and chaosletter.day_count(me["id"]) >= cap:
        await letter.finish(f"🕊️ 今天的寄信次数用完了（{cap}/{cap}）。")
    left = chaosletter.cooldown_left(me["id"])
    if left:
        await letter.finish(f"⏳ 鸽子正在休息，{left} 秒后再试。")
    blk = blocks.get_block(to["id"], me["id"])
    c = chaosletter.compose(me, to, content) if extensions.is_enabled("chaos_letter") else chaosletter.plain(me, to, content)
    chaosletter.log(me["id"], to["id"], c, content)
    done = f"🕊️ 信件已由鸽子衔往 {name} 处。今天已寄 {chaosletter.day_count(me['id'])}/{cap if cap > 0 else '不限'}。"
    if blk:
        await letter.finish(done if blk["silent"] else f"❌ {name} 已拒绝你的联络。")
    send = make_send()
    sent, _ = await messaging.push_to_role(c["true_to"], f"📮 一封信送到了你手里\n{c['shown']}\n落款：{c['sign']}", send)
    if c["torn"]:
        third, tail = c["torn"]
        await messaging.push_to_role(third, f"📮 一张残页被风吹到了你这里\n……{tail}\n落款：{c['sign']}", send)
    if c["effects"] and get_config("letter_public_send", "1") == "1" and random.random() * 100 < get_int("letter_public_chance", 50):
        ann = announce.get_announce()
        if ann:
            try:
                await send(ann[0], ann[1], f"📰 邮差今天出了岔子：{me['name']} 寄给 {name} 的信，{'、'.join(c['effects'])}。")
            except Exception as e:  # noqa: BLE001
                logger.warning("混乱寄信公开播报失败：{}", e)
    await letter.finish(done + ("" if sent else "\n（对方那边暂时收不到，信丢了……）"))
