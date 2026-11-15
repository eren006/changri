"""晚餐（额外扩展，默认关闭）：管理员「开始晚餐 座位数 [风格]」，玩家「入座 座位号」，相邻座位「戳你一下 角色名」。规则在 core/dinner.py。"""
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import dinner, messaging, roles
from changri.core.qqsend import make_send
from changri.plugins.play import NEED_ROLE, _cmd, _is_admin, _me

start_cmd = _cmd("开始晚餐")
sit_cmd = _cmd("入座")
end_cmd = _cmd("结束晚餐")
poke_cmd = _cmd("戳你一下")


@start_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await start_cmd.finish("❌ 权限不足，仅管理员可用。")
    p = args.extract_plain_text().split()
    if not p or not p[0].isdigit():
        await start_cmd.finish("用法：开始晚餐 座位数 [风格]\n风格：" + "、".join(dinner.MENUS) + "、无菜\n例：开始晚餐 5 现代西餐")
    ok, msg = dinner.start(int(p[0]), p[1] if len(p) > 1 else "现代中餐")
    await start_cmd.finish(msg if ok else "❌ " + msg)


@sit_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await sit_cmd.finish(NEED_ROLE)
    t = args.extract_plain_text().strip()
    if not t.isdigit():
        await sit_cmd.finish("用法：入座 座位号")
    ok, msg = dinner.sit(me["name"], int(t))
    await sit_cmd.finish(msg if ok else "❌ " + msg)


@end_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await end_cmd.finish("❌ 权限不足，仅管理员可用。")
    await end_cmd.finish("🏁 晚餐已结束。" if dinner.stop() else "现在没有进行中的晚餐。")


@poke_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await poke_cmd.finish(NEED_ROLE)
    name = args.extract_plain_text().strip()
    if not name:
        await poke_cmd.finish("用法：戳你一下 对方角色名")
    left = dinner.cooldown_left(me["id"])
    if left:
        await poke_cmd.finish(f"⏳ 你戳得太快了，请等 {(left + 59) // 60} 分钟后再试。")
    why, _ = dinner.poke_check(me["name"], name)
    if why:
        await poke_cmd.finish("❌ " + why)
    target = roles.get_role_by_name(name)
    dinner.mark_poked(me["id"])
    ok, _ = await messaging.push_to_role(target["id"], f"👆 {me['name']} 戳了你一下", make_send()) if target else (False, "")
    await poke_cmd.finish(f"✅ 你戳了 {name} 一下，消息已送达。" if ok else f"你戳了 {name} 一下（TA 那边暂时没收到）。")
