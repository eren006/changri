"""漂流瓶（额外扩展，默认关闭）：「漂流瓶 内容」扔一个，「漂流瓶 编号 内容」回信。规则在 core/bottle.py。"""
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import bottle, godmode, messaging
from changri.core.qqsend import make_send
from changri.plugins.play import NEED_ROLE, _cmd, _me
from changri.core.textutil import parse_id

drift = _cmd("漂流瓶")


@drift.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await drift.finish(NEED_ROLE)
    if me["is_npc"]:
        await drift.finish("NPC 不能用漂流瓶。")
    text = args.extract_plain_text().strip()
    usage = "🍾 漂流瓶\n扔：漂流瓶 内容\n回信：漂流瓶 编号 内容\n例：漂流瓶 有人能听到我说话吗\n例：漂流瓶 1 我听到啦"
    if not text:
        await drift.finish(usage)
    head, _, rest = text.partition(" ")
    bid_ = parse_id(head)
    if bid_ and rest.strip() and bottle.mine(me["id"], bid_):      # 编号不是自己的瓶子，多半是正文碰巧带数字，按新瓶子处理
        head = str(bid_)
        ok, tip, other, deliver = bottle.reply(me, bid_, rest.strip())
        if ok and deliver:
            await messaging.push_to_role(other, f"🍾 漂流瓶（编号{head}）有了新的回信：\n「{rest.strip()}」\n\n想继续回信就发送：漂流瓶 {head} 你想说的话", make_send())
        await drift.finish(tip)
    ok, tip, catcher, bid = bottle.throw(me, text)
    if ok:
        sent, _ = await messaging.push_to_role(catcher, f"🍾 你捡到一个漂流瓶（编号{bid}）：\n「{text}」\n\n想回信就发送：漂流瓶 {bid} 你想说的话", make_send())
        if not sent:
            tip += "\n（瓶子漂得有点远，对方那边暂时没收到。）"
    await drift.finish(tip)
