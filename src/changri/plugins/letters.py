"""写信综（个性化模块）的群指令。规则在 core/letters.py。"""
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import letters, messaging, roles
from changri.core.qqsend import make_send
from changri.plugins.play import NEED_ROLE, _cmd, _is_admin, _me
from changri.core.textutil import parse_id

send_letter = _cmd("发送信件", {"写信"})
letter_status = _cmd("信件状态")
quill_edit = _cmd("羽毛笔修改")
use_telescope = _cmd("使用望远镜")
use_quill = _cmd("使用羽毛笔")
guide = _cmd("写信综指南")
register_items = _cmd("注册写信综基础道具")


async def _deliver(role_id: int, text: str) -> bool:
    """发到收件人的个人群（开头 @）。一条发得完就发文字，太长才画成信纸图单独发。"""
    ok, _ = await messaging.push_to_role(role_id, text, make_send())
    return ok


@send_letter.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await send_letter.finish(NEED_ROLE)
    p = letters.parse(args.extract_plain_text())
    if not p["to"]:
        await send_letter.finish("⚠️ 要写【收件人】。照这个格式发：\n" + letters.TEMPLATE)
    to = roles.get_role_by_name(p["to"])
    if not to:
        await send_letter.finish(f"⚠️ 找不到角色「{p['to']}」。")
    ok, reply, out = letters.compose(me, to, p)
    if not ok:
        await send_letter.finish("❌ " + reply)
    if out["deliver"] and not await _deliver(*out["deliver"]):
        reply += "\n（对方那边暂时收不到，信丢了……）"
    if out["copy"]:
        await _deliver(*out["copy"])
    if out["held"]:
        await messaging.push_to_role(out["held"][0], out["held"][1], make_send())
    await send_letter.finish(reply)


@letter_status.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await letter_status.finish(NEED_ROLE)
    await letter_status.finish(letters.status(me["id"]))


@quill_edit.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await quill_edit.finish(NEED_ROLE)
    text = args.extract_plain_text().strip()
    if not text:
        await quill_edit.finish(letters.held_list(me["id"]))
    idx, _, content = text.partition(" ")
    n = parse_id(idx)
    if not n:
        await quill_edit.finish("用法：羽毛笔修改 序号 新内容，例：羽毛笔修改 2 改好的内容（只发「羽毛笔修改」看清单）")
    ok, reply, letter = letters.quill_edit(me["id"], n, content)
    if ok and letter:
        await _deliver(*letter)
    await quill_edit.finish(reply if ok else "❌ " + reply)


def _use(matcher, kind: str):
    @matcher.handle()
    async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
        me = _me(bot, event)
        if not me:
            await matcher.finish(NEED_ROLE)
        name = args.extract_plain_text().strip()
        target = roles.get_role_by_name(name) if name else None
        if not target:
            await matcher.finish(f"用法：{'使用望远镜' if kind == 'telescope' else '使用羽毛笔'} 角色名" + (f"（找不到「{name}」）" if name else ""))
        ok, reply = letters.apply_item(me, target, kind)
        await matcher.finish(reply if ok else "❌ " + reply)


_use(use_telescope, "telescope")
_use(use_quill, "quill")


@guide.handle()
async def _():
    await guide.finish(letters.GUIDE)


@register_items.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await register_items.finish("❌ 这条指令只有管理员能用。")
    letters.ensure_items()
    await register_items.finish("✅ 写信币、望远镜、羽毛笔都在物品库里了（已有的跳过）。可以在后台「物品与商城」上架或发放。")
