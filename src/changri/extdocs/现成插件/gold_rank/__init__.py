"""现成插件：富豪榜。用到 all_roles、item_info、item_count、reply_long。"""
from changri_api import Bot, GroupMessageCreateEvent, all_roles, is_admin, item_count, item_info, on_command, reply_long, setting

EXT_ID = "gold_rank"

rank = on_command("富豪榜", priority=10, block=True)


@rank.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if setting(EXT_ID, "admin_only") and not is_admin(bot, event):
        await rank.finish("富豪榜只有管理员能看。")
    currency = setting(EXT_ID, "currency") or "金币"
    it = item_info(currency)
    if not it:
        await rank.finish(f"物品库里没有「{currency}」，请管理员检查「富豪榜」的设置。")
    rows = sorted(((item_count(r["id"], it["code"]), r["name"]) for r in all_roles()), reverse=True)
    rows = [(n, name) for n, name in rows if n > 0][:max(1, min(50, int(setting(EXT_ID, "top") or 10)))]
    if not rows:
        await rank.finish(f"还没有人有{it['name']}。")
    medals = ["🥇", "🥈", "🥉"]
    body = "\n".join(f"{medals[i] if i < 3 else f'{i + 1}.'} {name}：{n}" for i, (n, name) in enumerate(rows))
    await reply_long(rank, f"💰 {it['name']}榜\n{body}")
