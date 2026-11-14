"""QQ 端物品、商城与地点指令。部分规则改编自「长日将尽」CC BY-NC-SA 作品（联系 3052553938）。"""
from nonebot import on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from core import messaging, roles, rpg
from plugins._long import make_send, reply_long

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"


def _cmd(name: str, aliases=None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _me(bot: Bot, event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


bag = _cmd("背包", {"我的背包"})
shop = _cmd("商城", {"道具商城"})
buy = _cmd("购买")
use = _cmd("使用")
give = _cmd("赠送道具", {"赠送物品"})
place = _cmd("地点")


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
    await reply_long(shop, "🏪 商城\n" + "\n".join(
        f"[{x['item_code']}] {x['name']} — {x['price']}{x['currency_name']}\n  {x['description']}" for x in rows))


@buy.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await buy.finish(NEED_ROLE)
    parts = args.extract_plain_text().strip().rsplit(maxsplit=1)
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
        await give.finish("用法：赠送道具 角色名 物品名/码 [数量]")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await give.finish("找不到这个角色。")
    count = int(parts[-1]) if parts[-1].isdigit() else 1
    item = " ".join(parts[1:-1] if parts[-1].isdigit() else parts[1:])
    ok, text = rpg.give(me["id"], target["id"], item, count)
    if ok:
        await messaging.push_to_role(target["id"], f"📦 「{me['name']}」给你赠送了{item}×{count}，已放入背包。", make_send())
    await give.finish(("✅ " if ok else "❌ ") + text)


@place.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    sub = args.extract_plain_text().strip() or "查看"
    if sub in ("钥匙", "我的钥匙"):
        if not me:
            await place.finish(NEED_ROLE)
        rows = rpg.keys(me["id"])
        await place.finish("🔑 你的地点钥匙：\n" + ("、".join(x["name"] for x in rows) if rows else "（没有）"))
    rows = rpg.places()
    lines = ["🏢 地点列表"]
    owned = {x["id"] for x in rpg.keys(me["id"])} if me else set()
    for x in rows:
        mark = "📍" if not x["locked"] else ("🔑" if x["id"] in owned else "🔒")
        lines.append(f"{mark} {x['name']}" + (f"（{x['description']}）" if x["description"] else ""))
    if not rows:
        lines.append("（暂无公共地点）")
    await reply_long(place, "\n".join(lines))
