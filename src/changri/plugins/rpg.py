"""QQ 端物品与商城指令（官方扩展「物品与商城」；地点在 plugins/places.py，属性定义是常规功能）。部分规则改编自「长日将尽」CC BY-NC-SA 作品（联系 3052553938）。"""
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent, MessageSegment
from nonebot.params import CommandArg

from changri.core import blocks, messaging, roles, rpg, rpgcard
from changri.core.buttons import Btn
from changri.core.qqsend import make_send, reply_buttons, reply_long

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"


def _cmd(name: str, aliases=None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _me(bot: Bot, event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


bag = _cmd("背包", {"我的背包"})
shop = _cmd("商城", {"道具商城"})
buy = _cmd("购买")
use = _cmd("使用")
give = _cmd("赠送道具", {"赠送物品", "转账", "赠送货币"})
card = _cmd("角色卡", {"名片", "我的名片", "角色名片"})
exchange = _cmd("兑换", {"兑换表"})
craft_cmd = _cmd("合成", {"合成表"})


async def _send_png(matcher, render, role_id: int) -> bool:
    """画一张图发出去；画不出来（比如服务器没装中文字体）返回 False，调用方退回文字。"""
    try:
        png = render(role_id)
        if png:
            await matcher.send(MessageSegment.file_image(png))
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[角色卡] 画图失败，改发文字：{e}")
    return False


@card.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await card.finish(NEED_ROLE)
    if await _send_png(card, rpgcard.render_card, me["id"]):
        return
    info = rpgcard.data(me["id"])
    lines = [f"🪪 {info['name']} 的角色卡"]
    if info["attrs"]:
        lines.append("属性：" + "、".join(f"{a['name']} {a['value']}" for a in info["attrs"]))
    if info["money"]:
        lines.append("货币：" + "、".join(f"{x['name']} {x['count']}" for x in info["money"]))
    lines.append("背包：" + ("、".join(f"{x['name']}×{x['count']}" for x in info["items"]) or "空"))
    await card.finish("\n".join(lines))


@bag.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await bag.finish(NEED_ROLE)
    raw = args.extract_plain_text().strip()
    if raw.startswith("详情 "):
        item = rpg.find_item(raw[3:].strip())
        if not item:
            await bag.finish("找不到这个物品。")
        effects = item["attr_effects"] or "{}"
        await bag.finish(f"📦 [{item['code']}] {item['name']}\n类型：{'货币' if item['kind']=='currency' else '物品'}\n{item['description'] or '（无描述）'}\n属性效果：{effects}")
    inv = rpg.inventory(me["id"])
    if not inv:
        await bag.finish(f"📦 {me['name']}的背包是空的。")
    if await _send_png(bag, rpgcard.render_bag, me["id"]):
        return
    lines = [f"📦 {me['name']}的背包"]
    for x in inv:
        use_text = f"（每件 {x['remaining_uses']} 次）" if x["remaining_uses"] >= 0 else ""
        lines.append(f"[{x['item_code']}] {x['name']}×{x['count']}{use_text}")
    await reply_long(bag, "\n".join(lines))


@shop.handle()
async def _():
    rows = rpg.listings()
    if not rows:
        await shop.finish("🏪 商城暂无上架物品。")
    # 购买按钮只填进输入框（后面可以补数量），玩家自己按发送
    await reply_buttons(shop, "🏪 商城\n" + "\n".join(
        f"[{x['item_code']}] {x['name']} — {x['price']}{x['currency_name']}\n  {x['description']}" for x in rows) + "\n买东西发「购买 名字 [数量]」",
        [Btn(f"🛒 {x['name']}", f"购买 {x['name']} ", enter=False) for x in rows], per_row=3)
    await shop.finish()


@buy.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await buy.finish(NEED_ROLE)
    raw = args.extract_plain_text().strip()
    words = raw.split()
    if words:                                            # 特殊道具：使用 道具名 目标 [参数]
        from changri.core import specials
        it = rpg.find_item(words[0])
        if specials.is_special(it):
            ok, text = specials.use(me, it, words[1:])
            await use.finish(text if ok else "❌ " + text)
    parts = raw.rsplit(maxsplit=1)
    has_count = len(parts) == 2 and parts[1].isdigit()
    count = int(parts[1]) if has_count else 1
    name = parts[0] if has_count else " ".join(parts)
    ok, text = rpg.buy(me["id"], name, count)
    await buy.finish(("✅ " if ok else "❌ ") + text)


@use.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await use.finish(NEED_ROLE)
    parts = args.extract_plain_text().strip().rsplit(maxsplit=1)
    has_count = len(parts) == 2 and parts[1].isdigit()
    count = int(parts[1]) if has_count else 1
    name = parts[0] if has_count else " ".join(parts)
    ok, text = rpg.use_item(me["id"], name, count)
    await use.finish(("✅ " if ok else "❌ ") + text)


@give.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await give.finish(NEED_ROLE)
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await give.finish("用法：赠送道具 角色名 物品名/码 [数量]\n货币也一样，例：转账 小明 金币 50")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await give.finish("找不到这个角色。")
    count = int(parts[-1]) if parts[-1].isdigit() else 1
    item = " ".join(parts[1:-1] if parts[-1].isdigit() else parts[1:])
    b = blocks.get_block(target["id"], me["id"])
    if b:                                                   # 对方拉黑了你：不转，非静默的告诉你
        await give.finish("❌ 赠送失败，请稍后再试。" if b["silent"] else f"❌ 「{target['name']}」已拒绝你的联系。")
    ok, text = rpg.give(me["id"], target["id"], item, count)
    if ok:
        await messaging.push_to_role(target["id"], f"📦 「{me['name']}」给你赠送了{item}×{count}，已放入背包。", make_send())
    await give.finish(("✅ " if ok else "❌ ") + text)


@exchange.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await exchange.finish(NEED_ROLE)
    parts = args.extract_plain_text().strip().split()
    rows = rpg.exchanges(True)
    if not parts:
        if not rows:
            await exchange.finish("🔁 现在没有可以兑换的东西。")
        await exchange.finish("🔁 兑换表\n" + "\n".join(f"{x['id']}. {x['from_name']}×{x['from_n']} → {x['to_name']}×{x['to_n']}" for x in rows)
                              + "\n用法：兑换 编号 [次数]")
    if not parts[0].isdigit():
        await exchange.finish("用法：兑换 编号 [次数]（发「兑换」看编号）")
    times = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    ok, text = rpg.exchange(me["id"], int(parts[0]), times)
    await exchange.finish(("✅ " if ok else "❌ ") + text)


@craft_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await craft_cmd.finish(NEED_ROLE)
    parts = args.extract_plain_text().strip().split()
    if not parts:
        rows = rpg.recipes(True)
        if not rows:
            await craft_cmd.finish("⚗️ 现在没有可以合成的东西。")
        await craft_cmd.finish("⚗️ 合成表\n" + "\n".join(f"{x['id']}. {x['name']}：" + "＋".join(f"{m['name']}×{m['n']}" for m in x["mats"]) + f" → {x['out_name']}×{x['out_n']}" for x in rows)
                               + "\n用法：合成 编号或名字 [次数]")
    times = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    ok, text = rpg.craft(me["id"], parts[0], times)
    await craft_cmd.finish(("✅ " if ok else "❌ ") + text)
