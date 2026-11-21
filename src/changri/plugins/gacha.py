"""抽取池（额外扩展，默认关闭）：抽取、我的抽取次数、我的保底、查看池子，管理员「发放抽取」。池子和奖品在后台「抽取池」页设。规则在 core/gacha.py。"""
import re

from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import gacha, messaging, npc, roles
from changri.core.qqsend import make_send
from changri.plugins.play import NEED_ROLE, _cmd, _is_admin, _me

draw = _cmd("抽取")
mine = _cmd("我的抽取次数")
pity = _cmd("我的保底")
view = _cmd("查看池子")
grant = _cmd("发放抽取")


@draw.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await draw.finish(NEED_ROLE)
    if me["is_npc"]:
        await draw.finish("NPC 不参加抽取。")
    parts = args.extract_plain_text().split()
    times = 1
    if parts and re.fullmatch(r"\d+", parts[-1]) and (len(parts) > 1 or not gacha.find_pool(parts[0])[0]):    # 最后一个数字是次数（只写一个数字时，若它是池子编号就当池子）
        times = int(parts.pop())
    ok, tip = gacha.draw(me["id"], " ".join(parts), times)
    await draw.finish(tip)


@mine.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await mine.finish(NEED_ROLE)
    st = gacha.my_status(me["id"])
    await mine.finish("🎟️ 我的抽取次数\n" + "\n".join(f"{s['name']}：{s['tickets']} 次" for s in st) if st else "现在没有开放的抽取池。")


@pity.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    me = _me(bot, event)
    if not me:
        await pity.finish(NEED_ROLE)
    st = gacha.my_status(me["id"])
    if not st:
        await pity.finish("现在没有开放的抽取池。")
    lines = [f"{s['name']}：已抽 {s['total']} 次，" + (f"距离保底还差 {s['left']} 抽（连续 {s['since']} 抽没出稀有）" if s["pity_n"] and s["has_rare"] else "这个池子没有保底") for s in st]
    await pity.finish("🛡️ 我的保底\n" + "\n".join(lines))


@view.handle()
async def _(args=CommandArg()):
    await view.finish(gacha.pool_text(args.extract_plain_text().strip()))


@grant.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await grant.finish("❌ 这条指令只有管理员能用。")
    parts = args.extract_plain_text().split()
    usage = "用法：发放抽取 角色名 次数 [池子]\n例：发放抽取 乙 3\n给所有玩家发：发放抽取 全体 1 新年池"
    if len(parts) < 2 or not re.fullmatch(r"-?\d+", parts[1]):
        await grant.finish(usage)
    who, n, pool = parts[0], int(parts[1]), " ".join(parts[2:])
    if who in ("全体", "所有人"):
        targets = [r for r in roles.list_all() if not npc.is_npc(r["id"])]
    else:
        r = roles.get_role_by_name(who)
        if not r:
            await grant.finish(f"❌ 找不到角色「{who}」。")
        targets = [r]
    done, last = 0, ""
    for r in targets:
        ok, tip = gacha.grant(r["id"], pool, n)
        if not ok:
            if len(targets) == 1:
                await grant.finish("❌ " + tip)
            last = tip
            continue
        done += 1
        if n > 0:
            await messaging.push_to_role(r["id"], f"🎟️ 你获得了 {n} 次抽取（{tip.split('，')[0]}）。发「抽取」试试手气。", make_send())
    await grant.finish(f"✅ 已给 {done} 人发放 {n} 次" + (f"（有人没发成：{last}）" if last else ""))
