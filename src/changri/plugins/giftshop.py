"""礼品店（额外扩展）：发「礼品店」逛一逛，随机拿到一件还没有的礼物收进图鉴（有冷却）；送礼写编号（#003）只能送图鉴里有的。
图鉴是「图鉴」「我的图鉴」（在 social.py）。管理（上架、导出、导入）在后台「礼品店」页。"""
from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.adapters.qq import GroupMessageCreateEvent

from changri.core import gifts
from changri.plugins.social import _sender_role

shop = on_command("礼品店", priority=10, block=True)


@shop.handle()
async def _(event: GroupMessageCreateEvent):
    me = _sender_role(event)
    if not me:
        await shop.finish("你想走进去，却发现自己还没有名字。\n先创建一个角色，再来逛吧。")
    await shop.finish(gifts.visit(me["id"])["text"])


steal_cmd = on_command("偷礼物", priority=10, block=True)
steal_log_cmd = on_command("偷盗记录", priority=10, block=True)


@steal_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    """偷礼物 对方名：从对方图鉴里偷一件你没有的（规则在 core/gifts.steal，和小手机礼品店共用）。被偷的人个人群收到提醒。"""
    from changri.core import roles
    from changri.plugins.play import _push
    me = _sender_role(event)
    if not me:
        await steal_cmd.finish("你还没有角色。")
    if not gifts.steal_enabled():
        await steal_cmd.finish("偷取礼物没有开放。")
    name = args.extract_plain_text().strip()
    target = roles.get_role_by_name(name) if name else None
    if not target:
        await steal_cmd.finish("用法：偷礼物 对方名\n从对方的图鉴里偷一件你还没有的礼物。" if not name else f"没有叫「{name}」的角色。")
    ok, text, row = gifts.steal(me["id"], target["id"])
    if ok and row:
        note = gifts.steal_text(row, target["id"])
        if note:
            await _push(target["id"], "🕵️ " + note)
    await steal_cmd.finish(text if ok else f"❌ {text}")


@steal_log_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    import time as _t
    me = _sender_role(event)
    if not me:
        await steal_log_cmd.finish("你还没有角色。")
    rows = gifts.steal_log(me["id"])
    if not rows:
        await steal_log_cmd.finish("还没有偷盗记录。")
    await steal_log_cmd.finish("🕵️ 偷盗记录\n" + "\n".join(f"{_t.strftime('%m-%d %H:%M', _t.localtime(r['ts']))} {r['text']}" for r in rows))
