"""心愿（官方扩展，可在「功能与设置」的「额外」里开关，默认关闭，改了要重启）：挂心愿、看心愿、摘心愿、撤心愿（悬赏心愿已去掉）。
规则和数据在 core/wishes.py；摘走后开的约会房间走 play.py 里的房间逻辑。开放时段、数量上限在设置里的「心愿」。"""
import re

import nonebot  # noqa: F401
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg  # noqa: F401

from changri.core import rooms, season, storycal, wishes
from changri.core.buttons import Btn
from changri.core.qqsend import make_send, reply_buttons
from changri.plugins.play import NEED_ROLE, NO_GROUP_TEXT, _announce_in_room, _cmd, _group_line, _invite, _me, _push, _qq_hint, _slot, settle_wish_room

async def _broadcast(w) -> None:
    await wishes.broadcast(w, make_send())


USAGE = "用法：挂心愿 时间 地点 内容 [| 昵称]\n例：挂心愿 {t} 花园 一起散步 | 神秘人A\n（昵称最多 10 个字；对外只显示性别和昵称，不显示真名）"


wish_post = _cmd("挂心愿")
wish_list = _cmd("看心愿")
wish_take = _cmd("摘心愿")
wish_back = _cmd("撤心愿")


@wish_post.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_post.finish(NEED_ROLE)
    text, sign = args.extract_plain_text().strip(), ""
    if re.search(r"[|｜]", text):
        text, sign = (x.strip() for x in re.split(r"[|｜]", text, 1))
    p = text.split(None, 2)
    if len(p) < 3:
        await wish_post.finish(USAGE.format(t=storycal.example_time()))
    ok, info = wishes.post(me["id"], p[0], p[1], p[2], sign)
    if not ok:
        await wish_post.finish(f"❌ {info}")
    w = wishes.get(int(info))
    await _broadcast(w)
    from changri.core import letters
    paid = letters.paid_note(me["id"], "wish")
    await wish_post.finish(wishes.post_ok_text(w) + (f"\n{paid}" if paid else "") + season.prep_note("心愿"))


@wish_list.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    ws = wishes.list_open()
    if not ws:
        await wish_list.finish("当前没有漂浮的心愿。")
    # 摘心愿会马上开房间、通知对方：按钮只把指令填进输入框，玩家自己按发送
    await reply_buttons(wish_list, "📜 当前心愿：\n" + "\n".join(wishes.line(w) for w in ws) + "\n\n摘一个发「摘心愿 编号」",
                        [Btn(f"摘 #{w['id']}", f"摘心愿 {w['id']}", enter=False) for w in ws], per_row=4)
    await wish_list.finish()


@wish_take.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_take.finish(NEED_ROLE)
    n = wishes.parse_id(args.extract_plain_text())
    if not n:
        await wish_take.finish("用法：摘心愿 编号，例：摘心愿 3（编号在「看心愿」里）")
    ok, info, room_id = wishes.take(n, me["id"])
    if not ok:
        await wish_take.finish(f"❌ {info}")
    if not await settle_wish_room(n, room_id):
        await wish_take.finish(f"❌ {NO_GROUP_TEXT.format(kind='心愿')}")
    w, room = wishes.get(n), rooms.get(room_id)
    await _push(w["poster_id"], f"🌠 你的心愿 #{n} 被 {me['name']} 摘走了！")
    await _invite(w["poster_id"], room, fix=("💌 心愿", "💌 心愿约会"))
    await _announce_in_room(room_id)
    await wish_take.finish(f"✅ 你摘了心愿 #{n}，对方已收到通知。\n{_slot(room)}\n{_group_line(room)}\n房间号 R{room_id}" + _qq_hint(me["id"], room))


@wish_back.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await wish_back.finish(NEED_ROLE)
    n = wishes.parse_id(args.extract_plain_text())
    if not n:                                   # 不带编号：列出自己挂着的（照老系统）
        mine = wishes.list_open(me["id"])
        await wish_back.finish(("📜 你发布的心愿：\n" + "\n".join(wishes.line(w) for w in mine) if mine else "你现在没有挂着的心愿。")
                               + "\n\n使用「撤心愿 编号」撤回")
    ok, why = wishes.withdraw(n, me["id"])
    await wish_back.finish(f"✅ 心愿 #{n} 已撤回。" if ok else f"❌ {why}")


