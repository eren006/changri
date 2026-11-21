"""现成插件：摇汽水。一瓶汽水会在第 1～N 下之间随机一下喷出来（开瓶时就定好了），大家轮流摇，喷到谁谁中招。
每个群同时只有一瓶，存在 data_set 里（按群存，不按角色存；一局几分钟就结束，跨季度也不会堆数据）。不涉及金币、道具。"""
import random
import time

from changri_api import Bot, GroupMessageCreateEvent, data_get, data_set, on_command, sender_role, setting

EXT_ID = "soda_shake"

start = on_command("摇汽水", aliases={"来瓶汽水", "拿汽水"}, priority=10, block=True)
shake = on_command("摇一下", aliases={"摇一摇", "摇摇"}, priority=10, block=True)
stop = on_command("不摇了", aliases={"收走汽水", "放下汽水"}, priority=10, block=True)

CALM = ["瓶子里咕噜了一下，没事。", "冒了几个小气泡，又安静了。", "瓶盖轻轻响了一声……虚惊一场。", "气泡往上窜了一截，还好没开。",
        "晃得有点狠，大家都往后缩了缩，没喷。", "手心都是汗，汽水倒是很稳。"]


def _key(event) -> str:
    return f"bottle_{event.group_openid}"


def _name(bot, event) -> str:
    me = sender_role(bot, event)
    return me["name"] if me else "一位路人"


def _bottle(event) -> dict | None:
    """这个群现在那瓶汽水；放太久没人摇的算作废。"""
    b = data_get(EXT_ID, _key(event))
    if not isinstance(b, dict):
        return None
    limit = max(1, int(setting(EXT_ID, "timeout") or 30)) * 60
    if time.time() - b.get("ts", 0) > limit:
        data_set(EXT_ID, _key(event), None)
        return None
    return b


@start.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    b = _bottle(event)
    if b:
        await start.finish(f"🥤 桌上已经有一瓶了，摇了 {b['n']} 下还没喷。发「摇一下」接着摇，「不摇了」收走。")
    total = min(12, max(2, int(setting(EXT_ID, "shakes") or 6)))
    data_set(EXT_ID, _key(event), {"pop": random.randint(1, total), "total": total, "n": 0, "last": "", "ts": time.time()})
    await start.finish(f"🥤 {_name(bot, event)} 拿出一瓶汽水。最多 {total} 下，它一定会喷——但不知道是哪一下。\n"
                       "大家轮流发「摇一下」，喷到谁谁中招。")


@shake.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    b = _bottle(event)
    if not b:
        await shake.finish("桌上没有汽水。发「摇汽水」拿一瓶。")
    me = event.author.member_openid
    if setting(EXT_ID, "no_twice") and b["last"] == me:
        await shake.finish("你刚摇过，换个人来。")
    b["n"] += 1
    b["last"], b["ts"] = me, time.time()
    who = _name(bot, event)
    if b["n"] >= b["pop"]:
        data_set(EXT_ID, _key(event), None)
        lines = [ln.strip() for ln in str(setting(EXT_ID, "penalties") or "").splitlines() if ln.strip()]
        tail = f"\n中招：{random.choice(lines)}" if lines else ""
        await shake.finish(f"💦 第 {b['n']} 下——噗！汽水喷了 {who} 一身！{tail}\n想再来就发「摇汽水」。")
    data_set(EXT_ID, _key(event), b)
    left = b["total"] - b["n"]
    hint = "只剩最后一下了，下一个人……自求多福。" if left == 1 else f"最多还剩 {left} 下。"
    await shake.finish(f"🫧 第 {b['n']} 下，{who} 摇了摇：{random.choice(CALM)}{hint}")


@stop.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _bottle(event):
        await stop.finish("桌上本来就没有汽水。")
    data_set(EXT_ID, _key(event), None)
    await stop.finish("🥤 汽水收走了，谁都没被喷。")
