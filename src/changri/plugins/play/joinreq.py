"""申请加入别人的房间：申请加入 / 加入请求 / 同意加入 / 拒绝加入。"""

from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import roles, rooms
from changri.core.buttons import Btn
from changri.core.overview import slot_text as _slot
from changri.core.qqsend import finish_usage, reply_buttons
from changri.core.textutil import hm_text

from .common import NEED_ROLE, _cmd, _me
from .finish import _room_arg
from .launch import notify_join_answer, notify_join_request


# ================= 申请加入 =================

join_cmd = _cmd("申请加入")
join_list = _cmd("加入请求")
join_yes = _cmd("同意加入")
join_no = _cmd("拒绝加入")


JOIN_OFF = "❌ 申请加入没有开放。"


@join_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not rooms.joinreq_enabled():
        await join_cmd.finish(JOIN_OFF)
    me = _me(bot, event)
    if not me:
        await join_cmd.finish(NEED_ROLE)
    p = args.extract_plain_text().split()
    if not p:
        await finish_usage(join_cmd, "用法：申请加入 对方名 [开始时间]\n例：申请加入 张三 14:00", "申请加入 ")
    target = roles.get_role_by_name(p[0])
    if not target:
        await join_cmd.finish(f"❌ 找不到角色：{p[0]}。")
    cands = rooms.find_joinable(target["id"], p[1] if len(p) > 1 else "")
    if not cands:
        await join_cmd.finish(f"「{target['name']}」现在没有可以加入的约会（能加入的：{'、'.join(rooms.joinable_kinds()) or '无'}）。")
    if len(cands) > 1:
        await join_cmd.finish(f"「{target['name']}」有好几场，请带上开始时间：\n" + "\n".join(f"· {_slot(r)} {r['kind']}" for r in cands))
    ok, why, qid = rooms.request_join(cands[0]["id"], me["id"])
    if not ok:
        await join_cmd.finish(f"❌ {why}")
    r = cands[0]
    await notify_join_request(me, r, qid)
    await join_cmd.finish("✅ 申请已发出，等对方回复。")


@join_list.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not rooms.joinreq_enabled():
        await join_list.finish(JOIN_OFF)
    me = _me(bot, event)
    if not me:
        await join_list.finish(NEED_ROLE)
    qs = rooms.pending_requests(me["id"])
    if not qs:
        await join_list.finish("没有待处理的加入申请。")
    text = "🙋 收到的加入申请\n" + "\n".join(f"#{q['id']} {q['applicant']} 想加入 R{q['room_id']} {q['kind']}（D{q['day']} {hm_text(q['start_hm'], q['end_hm'])}）" for q in qs)
    btns = [b for q in qs for b in (Btn(f"同意 #{q['id']} {q['applicant']}", f"同意加入 {q['id']}"), Btn(f"拒绝 #{q['id']}", f"拒绝加入 {q['id']}", style="danger"))]
    await reply_buttons(join_list, text + "\n同意发「同意加入 编号」，拒绝发「拒绝加入 编号」", btns, per_row=2)
    await join_list.finish()


async def _answer(matcher, bot, event, args, accept: bool):
    if not rooms.joinreq_enabled():
        await matcher.finish(JOIN_OFF)
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    n = _room_arg(args.extract_plain_text())
    if not n:
        await matcher.finish("用法：同意加入 编号，例：同意加入 3（编号在「加入请求」里）")
    ok, why, q = rooms.answer_request(n, me["id"], accept)
    if not ok:
        await matcher.finish(f"❌ {why}")
    await notify_join_answer(me, q, accept)
    await matcher.finish("✅ 已同意，对方会收到群号。" if accept else "已拒绝。")


@join_yes.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _answer(join_yes, bot, event, args, True)


@join_no.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _answer(join_no, bot, event, args, False)
