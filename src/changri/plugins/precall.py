"""前置电话（额外扩展，默认关闭，独立于小手机）。规则和数据在 core/precall.py。
玩家：提交前置电话 内容 / 查看前置电话 / 拨打前置电话 编号 / 撤回前置电话 / 我的前置电话
管理员：前置电话分组 A组 3 5 8（把这几个编号归到 A组；组名写「无」移回未分组）、可选 A组、不可选 A组、前置电话一览（后台页也能做）"""
import re

from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import precall, rooms, roles
from changri.core.buttons import Btn
from changri.core.db import get_conn
from changri.core.qqsend import reply_buttons
from changri.plugins.play import NEED_ROLE, NOT_ADMIN, _announce_in_room, _cmd, _invite, _is_admin, _me, _group_line, _push, _slot

SHOW_MAX = 60


async def dial_flow(me, no: int) -> tuple[bool, str, int]:
    """拨打 + 通知对方（QQ 指令和网页小手机共用）。"""
    ok, msg, rid = precall.dial(me["id"], no)
    if not ok:
        return False, msg, 0
    if not rid:                                                  # 双方共同选择：还在等对方回选，悄悄告诉对方「有人选了你」（不说是谁）
        e = precall.entry_of_no(no)
        if e:
            await _push(e["role_id"], f"📞 有人选了你的前置电话 #{e['no']}。你也想接通的话，在「查看前置电话」里找到 TA 拨打（你只知道编号，不知道是谁——电话接通后才会互相公开）。")
        return True, f"{msg}（{precall.usage(me['id'])}）", 0
    room = rooms.get(rid)
    from changri.plugins.play import _ensure_clean
    if not await _ensure_clean(rid):                             # 分到的群里还有别的玩家、换不到干净的群：当没有空群，撤掉这次拨打
        with get_conn() as c:
            c.execute("DELETE FROM precall_calls WHERE room_id=?", (rid,))
            c.execute("DELETE FROM precall_picks WHERE from_id=? AND to_id=?", (me["id"], precall.entry_of_no(no)["role_id"]))
        return False, "备用群池暂时没有空闲的群，这次电话没能接通，请稍后再试，或联系管理员多准备几个备用群。", 0
    room = rooms.get(rid)
    for m in rooms.members(rid):
        if m["role_id"] != me["id"]:
            await _invite(m["role_id"], room, me["name"])
    await _announce_in_room(rid)
    return True, f"📞 已拨打 #{no}，房间号 R{rid}。\n{_group_line(room)}\n对方已收到通知。（{precall.usage(me['id'])}）", rid


def _short(t: str) -> str:
    t = " ".join(t.split())
    return t if len(t) <= SHOW_MAX else t[:SHOW_MAX] + "…"


submit = _cmd("提交前置电话", {"前置电话提交"})


@submit.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await submit.finish(NEED_ROLE)
    ok, msg, _no = precall.submit(me["id"], args.extract_plain_text())
    await submit.finish(("✅ " if ok else "❌ ") + msg)


view = _cmd("查看前置电话", {"前置电话"})


@view.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await view.finish(NEED_ROLE)
    if not precall.is_on():
        await view.finish("前置电话现在没有开放。")
    items = precall.visible()
    if not items:
        await view.finish("现在还没有开放选择的前置电话，等管理员分好组。")
    out, last = ["📞 前置电话（拨打：拨打前置电话 编号）"], None
    for it in items:
        if it["grp"] != last:
            out.append(f"\n【{it['grp']}】")
            last = it["grp"]
        out.append(f"#{it['no']}{'（你）' if it['role_id'] == me['id'] else ''}  {_short(it['text'])}")
    out.append(f"\n{precall.usage(me['id'])}")
    # 拨打会马上开电话房间：按钮只把指令填进输入框，玩家自己按发送
    await reply_buttons(view, "\n".join(out), [Btn(f"拨打 #{it['no']}", f"拨打前置电话 {it['no']}", enter=False)
                                               for it in items if it["role_id"] != me["id"]], per_row=4)
    await view.finish()


mine = _cmd("我的前置电话")


@mine.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await mine.finish(NEED_ROLE)
    e = precall.entry_of(me["id"])
    head = f"你的前置电话：#{e['no']}（{e['grp'] or '未分组'}）\n{e['text']}" if e else "你还没有提交前置电话。发「提交前置电话 内容」。"
    await mine.finish(f"{head}\n{precall.usage(me['id'])}")


withdraw = _cmd("撤回前置电话")


@withdraw.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await withdraw.finish(NEED_ROLE)
    await withdraw.finish("已撤回你的前置电话。" if precall.withdraw(me["id"]) else "你还没有提交前置电话。")


dial = _cmd("拨打前置电话", {"拨打"})


@dial.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await dial.finish(NEED_ROLE)
    m = re.search(r"\d+", args.extract_plain_text())
    if not m:
        await dial.finish("用法：拨打前置电话 编号（编号在「查看前置电话」里）")
    ok, text, _rid = await dial_flow(me, int(m.group()))
    await dial.finish(text if ok else f"❌ {text}")


group_cmd = _cmd("前置电话分组")


@group_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await group_cmd.finish(NOT_ADMIN)
    p = args.extract_plain_text().split()
    nums = [int(x.lstrip("#")) for x in p[1:] if x.lstrip("#").isdigit()]
    if len(p) < 2 or not nums:
        await group_cmd.finish("用法：前置电话分组 A组 3 5 8（把编号 3、5、8 归到 A组；组名写「无」= 移回未分组）")
    _n, msg = precall.assign(nums, "" if p[0] == "无" else p[0])
    await group_cmd.finish(msg)


open_cmd = _cmd("可选")


@open_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await open_cmd.finish(NOT_ADMIN)
    g = args.extract_plain_text().strip()
    await open_cmd.finish(f"✅ 「{g}」的前置电话现在可以选了。" if g and precall.set_group_open(g, True) else "❌ 用法：可选 A组（这个组要先建好、归进去至少一个编号）")


close_cmd = _cmd("不可选")


@close_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await close_cmd.finish(NOT_ADMIN)
    g = args.extract_plain_text().strip()
    await close_cmd.finish(f"✅ 「{g}」不再开放选择（已经通了的电话不受影响）。" if g and precall.set_group_open(g, False) else "❌ 用法：不可选 A组")


overview = _cmd("前置电话一览")


@overview.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await overview.finish(NOT_ADMIN)
    es = precall.all_entries()
    if not es:
        await overview.finish("还没有人提交前置电话。")
    gs = {g["name"]: g["open"] for g in precall.groups()}
    lines = [f"#{e['no']} {e['name']}（{e['grp'] + ('·可选' if gs.get(e['grp']) else '·未开放') if e['grp'] else '未分组'}）{_short(e['text'])}" for e in es]
    await overview.finish("📞 前置电话一览\n" + "\n".join(lines))
