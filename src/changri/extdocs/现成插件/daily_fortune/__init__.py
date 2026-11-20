"""现成插件：今日运势。同一个人同一天抽到的一样（用 day_key 做随机种子），不需要存数据。"""
import hashlib

from changri_api import Bot, GroupMessageCreateEvent, day_key, on_command, sender_role, setting

EXT_ID = "daily_fortune"

fortune = on_command("运势", aliases={"今日运势"}, priority=10, block=True)


@fortune.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    role = sender_role(bot, event)
    who = role["name"] if role else "你"
    seed_src = f"{day_key()}|{role['id'] if role else event.author.member_openid}"
    lines = [ln.strip() for ln in str(setting(EXT_ID, "lines") or "").splitlines() if ln.strip()]
    if not lines:
        await fortune.finish("还没写运势内容，请管理员在「今日运势」的设置里加几行。")
    pick = lines[int(hashlib.sha256(seed_src.encode()).hexdigest(), 16) % len(lines)]
    head, _, body = pick.partition("｜")
    title = setting(EXT_ID, "title") or "🔮 今日运势"
    await fortune.finish(f"{title}\n{who}：{head}" + (f"\n{body}" if body else ""))
