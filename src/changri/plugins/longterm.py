"""长期群模式的群指令（规则和数据在 core/longterm.py）。

QQ 群先「/模式 长期群」（plugins/trpg.py 的模式指令），再「/切换长期群 名字」接到后台建好的某个长期群。
长期群模式的群里：恋综的指令不响应（plugins/trpg.py 的 _mute_romance）；ra / rt 等检定读这里的属性（plugins/dice.py）。
"""
from nonebot import logger, on_command
from nonebot.adapters.qq import GroupMessageCreateEvent, MessageEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg
from nonebot.rule import Rule

from changri.core import admins, longterm as lt, roomlog, trpg
from changri.core.qqsend import reply_long

NOT_ADMIN = "这个要机器人管理员才能用。"


def _in_lt(event) -> bool:
    return isinstance(event, GroupMessageCreateEvent) and trpg.get_mode(event.group_openid) == lt.MODE


async def _lt_rule(event: MessageEvent) -> bool:
    return _in_lt(event)


def _cmd(name: str, aliases: set[str] | None = None, tight: bool = True):
    # 比恋综（10 被屏蔽）低、比跑团骰子的 录属性 / 属性（20）高：长期群里的 /属性 /st 归这里。
    # tight：指令词后面要空格或者到头，不然「调整一下」「使用说明」这种聊天也会被当成指令（指令默认不用带 /）
    return on_command(name, aliases=aliases or set(), rule=Rule(_lt_rule), priority=14, block=True, force_whitespace=tight or None)


def _is_admin(event: GroupMessageCreateEvent) -> bool:
    return admins.is_admin(current_bot.get().self_id, event.author.member_openid)


def _group(event: GroupMessageCreateEvent):
    return lt.bound_group(event.group_openid)


NO_GROUP = "这个群还没接到长期群。管理员发「/切换长期群 名字」（长期群在后台「长期群」页先建好）。"


def _me(event: GroupMessageCreateEvent, g: dict):
    return lt.member_of(g["id"], event.author.member_openid)


def need_register(g: dict) -> str:
    extra = "".join(f"\n{k}：…" for k in lt.reg_fields(g))
    return f"你还没注册。发「/注册 名字」{('，可以换行接着填：' + extra) if extra else ''}"


def _mentioned(event) -> str | None:
    for m in getattr(event, "mentions", None) or []:
        if getattr(m, "bot", False) or getattr(m, "is_you", False):
            continue
        openid = getattr(m, "member_openid", "")
        if openid:
            return openid
    return None


def _target(event, g: dict, name: str):
    """先看 @ 了谁，再按名字（含别名）找。"""
    openid = _mentioned(event)
    if openid:
        return lt.member_of(g["id"], openid)
    return lt.member_by_name(g["id"], name) if name else None


# ---------- 切换 / 查看长期群 ----------

switch_cmd = _cmd("切换长期群", {"接长期群", "绑定长期群"})
list_cmd = _cmd("长期群", {"长期群列表"})


@switch_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    name = args.extract_plain_text().strip()
    groups = lt.list_groups()
    if not name:
        cur = _group(event)
        names = "、".join(g["name"] for g in groups) or "（还没有，先在后台「长期群」页建）"
        await switch_cmd.finish(f"这个群现在接的是：{cur['name'] if cur else '（还没接）'}\n可以切到：{names}\n用法：/切换长期群 名字")
    if not _is_admin(event):
        await switch_cmd.finish(NOT_ADMIN)
    g = lt.find_group(name)
    if not g:
        await switch_cmd.finish(f"没有叫「{name}」的长期群。现有：" + ("、".join(x["name"] for x in groups) or "（还没有，先在后台建）"))
    lt.bind(event.group_openid, g["id"])
    await switch_cmd.finish(f"好，这个群接到长期群「{g['name']}」。玩家先发「/注册 名字」。")


@list_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    g = _group(event)
    if not g:
        await list_cmd.finish(NO_GROUP)
    await list_cmd.finish(f"🏠 长期群「{g['name']}」：{g['n_members'] if 'n_members' in g else len(lt.list_members(g['id']))} 人注册。\n"
                          f"货币：{g['currency']}（{lt.rate_text(g)}）\n"
                          "发 /长期群帮助 看所有指令。")


# ---------- 注册 ----------

reg_cmd = _cmd("注册", {"登记"})


@reg_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g = _group(event)
    if not g:
        await reg_cmd.finish(NO_GROUP)
    fields = lt.reg_fields(g)
    name, info = lt.parse_register(args.extract_plain_text(), fields)
    if not name:
        await reg_cmd.finish(need_register(g))
    old = _me(event, g)
    m, err = lt.register(g["id"], event.author.member_openid, name, info)
    if not m:
        await reg_cmd.finish(err)
    missing = [k for k in fields if not m["info"].get(k)]
    tail = ("\n还没填：" + "、".join(missing) + "（再发一次「/注册 名字」换行写「项目：内容」补上）") if missing else ""
    head = "资料改好了。" if old else f"✅ 注册好了，欢迎来到「{g['name']}」！"
    await reply_long(reg_cmd, head + "\n" + lt.panel_text(m["id"]) + tail)


# ---------- 看自己 ----------

me_cmd = _cmd("我的", {"面板", "角色卡", "我的面板", "个人面板"})
attr_cmd = _cmd("属性", {"我的属性"})
bag_cmd = _cmd("背包", {"我的背包"})


async def _need_me(matcher, event):
    g = _group(event)
    if not g:
        await matcher.finish(NO_GROUP)
    m = _me(event, g)
    if not m:
        await matcher.finish(need_register(g))
    return g, m


async def _whose(matcher, event, args, own_only=False):
    """不带参数看自己；带名字 / @ 人看别人（面板、属性、背包都公开）。"""
    g = _group(event)
    if not g:
        await matcher.finish(NO_GROUP)
    name = args.extract_plain_text().strip()
    if (name or _mentioned(event)) and not own_only:
        m = _target(event, g, name)
        if not m:
            await matcher.finish(f"没找到「{name or '这位'}」。")
        return g, m
    return await _need_me(matcher, event)


@me_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    _, m = await _whose(me_cmd, event, args)
    await reply_long(me_cmd, lt.panel_text(m["id"]))


@attr_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    _, m = await _whose(attr_cmd, event, args)
    await reply_long(attr_cmd, lt.attrs_text(m["id"]))


@bag_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    _, m = await _whose(bag_cmd, event, args)
    await reply_long(bag_cmd, lt.bag_text(m["id"]))


# ---------- 自己改属性 ----------

st_cmd = _cmd("st", {"录属性", "设置属性"}, tight=False)        # /st侦查70 不带空格也认（跟跑团一样）


@st_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g, m = await _need_me(st_cmd, event)
    text = args.extract_plain_text().strip()
    if not text:
        await reply_long(st_cmd, lt.attrs_text(m["id"]))
        return
    if not g["self_attrs"] and not _is_admin(event):
        await st_cmd.finish("这个长期群的属性由管理员来设，自己改不了。")
    pairs = lt.parse_attrs(text)
    if not pairs:
        defs = "、".join(d["name"] for d in lt.attr_defs(g["id"])) or "（后台还没设属性）"
        await st_cmd.finish(f"格式：/st 力量60 敏捷70\n能改的属性：{defs}")
    ok, err = lt.set_attrs(m["id"], pairs, only_defined=True)
    if not ok:
        await st_cmd.finish(err)
    await reply_long(st_cmd, "改好了。\n" + lt.attrs_text(m["id"]))


# ---------- 赠送 ----------

give_cmd = _cmd("赠送", {"转账", "赠送物品"})


@give_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g, me = await _need_me(give_cmd, event)
    text = args.extract_plain_text().strip()
    usage = f"用法：/赠送 对方名字 物品 数量（也可以 @ 对方；送{g['currency']}就写「/赠送 名字 {g['currency']} 10」）"
    if _mentioned(event):
        text = "@ " + text                                   # @ 的人已经从文字里拿掉了，补一个占位
    who, what, qty = lt.parse_give(text)
    if not what:
        await give_cmd.finish(usage)
    dst = _target(event, g, who)
    if not dst:
        await give_cmd.finish(f"没找到「{who}」：要是对方注册的名字。\n" + usage)
    ok, msg = lt.give(me["id"], dst["id"], what, qty)
    await give_cmd.finish(("🎁 " if ok else "") + msg)


# ---------- 商城 ----------

shop_cmd = _cmd("商城", {"道具商城"})
buy_cmd = _cmd("购买")
use_cmd = _cmd("使用")


@shop_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    g = _group(event)
    if not g:
        await shop_cmd.finish(NO_GROUP)
    await reply_long(shop_cmd, lt.shop_text(g["id"]))


@buy_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    _, me = await _need_me(buy_cmd, event)
    parts = args.extract_plain_text().split()
    qty = 1
    if len(parts) >= 2 and parts[-1].lstrip("xX×").isdigit():
        qty = int(parts.pop().lstrip("xX×"))
    if not parts:
        await buy_cmd.finish("用法：/购买 道具名 数量（数量不写 = 1）。发 /商城 看在卖什么。")
    ok, msg = lt.buy(me["id"], " ".join(parts), qty)
    await buy_cmd.finish(msg)


@use_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    _, me = await _need_me(use_cmd, event)
    name = args.extract_plain_text().strip()
    if not name:
        await use_cmd.finish("用法：/使用 道具名。发 /背包 看你有什么。")
    ok, msg = lt.use(me["id"], name)
    await use_cmd.finish(msg)


# ---------- 字数统计 ----------

count_cmd = _cmd("字数统计", {"统计字数", "交字数", "记字数"})
rank_cmd = _cmd("字数榜", {"字数排行", "货币榜"})


@count_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    g = _group(event)
    if not g:
        await count_cmd.finish(NO_GROUP)
    me = _me(event, g)
    if g["count_admin_only"] and not _is_admin(event):
        await count_cmd.finish("这个长期群的字数只有管理员能交。")
    if not me and not _is_admin(event):
        await count_cmd.finish(need_register(g))
    quoted = "\n".join(el.content or "" for el in (getattr(event, "msg_elements", None) or []))
    items = roomlog.parse_quoted_forward(quoted)
    logger.info(f"[长期群·字数] {g['name']}：引用文字 {len(quoted)} 字，读出 {len(items)} 条")
    if not items:
        await count_cmd.finish("没读到聊天记录：长按那条合并转发，点「回复」，再发「/字数统计」。")
    res = lt.tally(g["id"], items, submitter=me, qq_group=event.group_openid,
                   pending=bool(g["count_review"]) and not _is_admin(event))        # 管理员自己交的直接入账
    await reply_long(count_cmd, lt.tally_text(g, res))


@rank_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g = _group(event)
    if not g:
        await rank_cmd.finish(NO_GROUP)
    by = "coins" if "货币" in args.extract_plain_text() or g["currency"] in args.extract_plain_text() else "chars"
    rows = lt.ranking(g["id"], by)
    if not rows:
        await rank_cmd.finish("还没有人注册。")
    unit = g["currency"] if by == "coins" else "字"
    lines = [f"{i}. {r['name']}　{r[by]} {unit}" + (f"（{r['paras']} 段）" if by == "chars" else "") for i, r in enumerate(rows, 1)]
    await reply_long(rank_cmd, f"🏆 「{g['name']}」{'货币' if by == 'coins' else '字数'}榜\n" + "\n".join(lines))


# ---------- 管理员加减 ----------

adj_cmd = _cmd("调整", {"加减", "管理调整"})


@adj_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g = _group(event)
    if not g:
        await adj_cmd.finish(NO_GROUP)
    if not _is_admin(event):
        await adj_cmd.finish(NOT_ADMIN)
    text = args.extract_plain_text().strip()
    usage = (f"用法：/调整 玩家名字 力量+5 {g['currency']}-10 苹果+2 字数+300（也可以 @ 玩家）\n"
             f"写「=」是直接改成这个数：/调整 名字 {g['currency']}=100\n"
             "后台定义的属性改属性，货币名改货币，「字数」改字数，其余当背包物品。")
    if _mentioned(event):
        m, rest = lt.member_of(g["id"], _mentioned(event)), text
    else:
        name, _, rest = text.partition(" ")
        m = lt.member_by_name(g["id"], name) if name else None
    ops = lt.parse_adjust(rest)
    if not m or not ops:
        await adj_cmd.finish(("没找到这位玩家。\n" if not m and text else "") + usage)
    out = lt.apply_adjust(m["id"], ops, note="管理员在群里调整", log=True)
    await reply_long(adj_cmd, f"✏️ 已调整「{m['name']}」：\n" + "\n".join(out))


# ---------- 签到 ----------

checkin_cmd = _cmd("签到", {"打卡", "每日签到"})


@checkin_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    _, me = await _need_me(checkin_cmd, event)
    ok, msg = lt.checkin(me["id"])
    await checkin_cmd.finish(msg)


# ---------- 合成 / 兑换 ----------

recipes_cmd = _cmd("合成表", {"兑换表", "配方"})
craft_cmd = _cmd("合成", {"兑换"})


@recipes_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    g = _group(event)
    if not g:
        await recipes_cmd.finish(NO_GROUP)
    await reply_long(recipes_cmd, lt.recipes_text(g["id"]))


@craft_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g, me = await _need_me(craft_cmd, event)
    parts = args.extract_plain_text().split()
    times = 1
    if len(parts) >= 2 and parts[-1].lstrip("xX×").isdigit():
        times = int(parts.pop().lstrip("xX×"))
    if not parts:
        await reply_long(craft_cmd, lt.recipes_text(g["id"]))
        return
    ok, msg = lt.craft(me["id"], " ".join(parts), times)
    await craft_cmd.finish(msg)


# ---------- 管理员批量发放 ----------

grant_cmd = _cmd("发放", {"批量发放"})


@grant_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g = _group(event)
    if not g:
        await grant_cmd.finish(NO_GROUP)
    if not _is_admin(event):
        await grant_cmd.finish(NOT_ADMIN)
    text = args.extract_plain_text().strip()
    usage = (f"用法：/发放 全员 {g['currency']}+50\n　　　/发放 阿青、小白 苹果+1 HP+5\n"
             "（也可以 @ 好几个人再写内容；跟 /调整 一样认属性、货币、字数、物品）")
    ops = lt.parse_adjust(text)
    mentioned = [getattr(m, "member_openid", "") for m in (getattr(event, "mentions", None) or [])
                 if not getattr(m, "bot", False) and not getattr(m, "is_you", False) and getattr(m, "member_openid", "")]
    missing = []
    if mentioned:
        targets = [m for m in (lt.member_of(g["id"], o) for o in mentioned) if m]
    else:
        first = lt._ADJ.search(text.translate(lt._FULL))
        who = text[:first.start()] if first else text
        # 名字和第一项内容之间可能没空格：退回到最后一个分隔符
        targets, missing = lt.resolve_targets(g["id"], who.strip())
    if not targets or not ops:
        await grant_cmd.finish((f"没找到：{'、'.join(missing)}\n" if missing else "") + usage)
    res = lt.bulk_adjust([m["id"] for m in targets], ops, note="管理员在群里发放")
    ok_n = sum(1 for _, out in res if all("→" in x for x in out))
    lines = [f"🎁 发放给 {len(res)} 人（全部成功 {ok_n} 人）：" + " ".join(f"{n}{o}{v}" for n, o, v in ops)]
    lines += [f"· {name}：" + "；".join(out) for name, out in res if not all("→" in x for x in out)]
    if missing:
        lines.append("没找到：" + "、".join(missing))
    await reply_long(grant_cmd, "\n".join(lines))


# ---------- 帮助 ----------

help_cmd = _cmd("长期群帮助", {"帮助", "菜单", "指令", "help"})


@help_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    await reply_long(help_cmd, lt.help_text(_group(event), _is_admin(event)))


# ---------- KPI ----------

kpi_cmd = _cmd("KPI", {"kpi", "考核", "我的KPI"})


@kpi_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g, m = await _whose(kpi_cmd, event, args)
    if not lt.kpi_on(g):
        await kpi_cmd.finish("这个长期群没设 KPI。")
    await kpi_cmd.finish(f"{m['name']}　" + lt.kpi_text(m["id"]))


# ---------- 改名 ----------

rename_cmd = _cmd("改名", {"修改名字", "改名字"})


@rename_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    g, me = await _need_me(rename_cmd, event)
    name = args.extract_plain_text().strip()
    if not name:
        await rename_cmd.finish(f"用法：/改名 新名字（你现在叫「{me['name']}」）。旧名字会留着当别名，转发里写旧名字的发言照样算你的。")
    ok, err = lt.rename(me["id"], name)
    await rename_cmd.finish(f"好，「{me['name']}」改名为「{name}」。旧名字留着当别名。" if ok else err)
