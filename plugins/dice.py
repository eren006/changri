"""跑团骰子：rd 通用掷骰；COC7（ra/rb/rp/sc/en）和 DND5e（ra/rb/rp/dnd/ri/at）；录属性/属性。
规则和做法参考索罗，口吻中性；属性存在这个系统自己的角色上。逻辑都在 core/dice.py。"""
from nonebot import on_command
from nonebot.adapters.qq import GroupMessageCreateEvent, MessageEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg
from nonebot.rule import Rule

from core import attrs, dice, messaging, roles, trpg
from core.db import get_config, set_config
from plugins._long import make_send, reply_long

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"
_initiative: dict[str, dict] = {}        # 先攻表，程序重启就清空，一场战斗够用


def _bid() -> str:
    return current_bot.get().self_id


def _enabled() -> bool:
    return get_config("dice_enabled", "1") == "1"


async def _on(event: MessageEvent) -> bool:
    return _enabled()


def _cmd(name: str, aliases: set[str] | None = None, short: bool = False):
    return on_command(name, aliases=aliases or set(), rule=Rule(_on), priority=20, block=True)


def _scope(event: MessageEvent) -> str:
    gid = getattr(event, "group_openid", None)
    if gid:
        return f"{_bid()}|{gid}"
    author = getattr(event, "author", None)
    return f"c2c|{_bid()}|{getattr(author, 'user_openid', '') or event.get_user_id()}"


def _rule(event: MessageEvent) -> str:
    return get_config(f"dice_rule:{_scope(event)}", "") or get_config("dice_default_rule", "coc") or "coc"


def _house(event: MessageEvent) -> str:
    h = get_config(f"coc_house:{_scope(event)}", "") or get_config("coc_default_house", dice.DEFAULT_HOUSE)
    return h if h in dice.HOUSE_RULES else dice.DEFAULT_HOUSE


def _proxy_openid(event: MessageEvent) -> str | None:
    """代骰：消息里 @ 了别的真人，就取第一个被 @ 的人。"""
    for m in getattr(event, "mentions", None) or []:
        if getattr(m, "scope", None) != "single" or getattr(m, "bot", False) or getattr(m, "is_you", False):
            continue
        openid = getattr(m, "member_openid", "")
        if openid and openid != event.get_user_id():
            return openid
    return None


def _role_of(event: MessageEvent, member: str | None = None):
    if not isinstance(event, GroupMessageCreateEvent):
        return None
    return roles.find_role_for_sender(_bid(), event.group_openid, member or event.get_user_id())


def _trpg_lookup(event: GroupMessageCreateEvent, proxy: str | None, name: str):
    """跑团模式：读当前角色卡（被 @ 的人就读他的卡）。"""
    pid = trpg.player_for(proxy or event.author.member_openid, event.group_openid, create=False)
    card = trpg.active_card(event.group_openid, pid) if pid else None
    if not card:
        return ("被 @ 的这位还没有角色卡，数值取不到。让 TA 先 /st 录属性，或者你直接写数值。" if proxy
                else "你还没有角色卡。用 /st 力量60 敏捷70 录属性，或者直接写数值。")
    val = trpg.get_attr(card["id"], name)
    if val is None:
        have = "、".join(n for n, _ in trpg.list_attrs(card["id"])) or "（没有）"
        return f"角色「{card['name']}」还没有「{name}」这个属性。现有：{have}\n用 /st {name}60 记一个。"
    return card["name"], val


def _lookup(event: MessageEvent):
    proxy = _proxy_openid(event)

    def lookup(name: str):
        if isinstance(event, GroupMessageCreateEvent) and trpg.get_mode(event.group_openid) == "trpg":
            return _trpg_lookup(event, proxy, name)
        if not isinstance(event, GroupMessageCreateEvent):
            return "读角色属性只能在群里用，请在群里发，或者直接写数值。"
        role = _role_of(event, proxy)
        if not role:
            return ("被 @ 的这位还没有绑定角色，数值取不到。让 TA 先绑定角色，或者你直接写数值。" if proxy
                    else NEED_ROLE + "或者直接写数值。")
        val = attrs.get_attr(role["id"], name)
        if val is None:
            have = "、".join(n for n, _ in attrs.list_attrs(role["id"])) or "（没有）"
            return f"角色「{role['name']}」还没有「{name}」这个属性。现有：{have}\n用 /录属性 {name}60 记一个。"
        return role["name"], val
    return lookup


# ---------- 掷骰 ----------

rd_cmd = _cmd("rd", {"r"}, short=True)
ra_cmd = _cmd("ra", {"检定"}, short=True)
rb_cmd = _cmd("rb", short=True)
rp_cmd = _cmd("rp", short=True)
sc_cmd = _cmd("sc", {"理智检定"}, short=True)
en_cmd = _cmd("en", {"成长"}, short=True)
dnd_cmd = _cmd("dnd", {"属性生成"}, short=True)
ri_cmd = _cmd("ri", {"先攻"}, short=True)
at_cmd = _cmd("at", {"攻击"}, short=True)
rh_cmd = _cmd("rh", {"暗骰"}, short=True)
rah_cmd = _cmd("rah", {"暗检定"}, short=True)
ti_cmd = _cmd("ti", {"临时疯狂"}, short=True)
li_cmd = _cmd("li", {"总结疯狂"}, short=True)
setcoc_cmd = _cmd("setcoc", {"房规"}, short=True)


@rd_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await reply_long(rd_cmd, dice.rd(args.extract_plain_text()))


def _check_text(event: MessageEvent, raw: str, extra: int) -> str:
    if _rule(event) == "dnd":
        return dice.dnd_check(raw, (extra > 0) - (extra < 0), _lookup(event))
    return dice.coc_check(raw, extra, _lookup(event), proxy=bool(_proxy_openid(event)), house=_house(event))


async def _check(matcher, event: MessageEvent, raw: str, extra: int) -> None:
    await reply_long(matcher, _check_text(event, raw, extra))


async def _whisper(event: MessageEvent, text: str) -> bool:
    """把暗骰结果私下发给本人：先发到 TA 的个人群，没有角色就试官方的单聊。都发不出去返回 False（结果不会在群里露出来）。"""
    role = _role_of(event)
    if role:
        ok, _ = await messaging.push_to_role(role["id"], text, make_send())
        if ok:
            return True
    try:
        await current_bot.get().send_to_c2c(openid=event.get_user_id(), message=text)
        return True
    except Exception:  # noqa: BLE001 单聊要玩家先私聊过机器人，发不出去很正常
        return False


async def _hidden(matcher, event: MessageEvent, text: str) -> None:
    if not isinstance(event, GroupMessageCreateEvent):      # 本来就是私聊，直接回
        await reply_long(matcher, text)
        return
    role = _role_of(event)
    who = role["name"] if role else "有人"
    if await _whisper(event, "🎲 暗骰结果（只有你看得到）\n" + text):
        await matcher.finish(f"🎲 {who} 掷了一次暗骰，结果已私下发给 TA。")
    await matcher.finish(f"🎲 {who} 想掷暗骰，但私下发不出去，所以没掷。先在个人群里绑定角色，或私聊机器人一句话再试。")


@ra_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _check(ra_cmd, event, args.extract_plain_text(), 0)


@rb_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _check(rb_cmd, event, args.extract_plain_text(), 1)


@rp_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _check(rp_cmd, event, args.extract_plain_text(), -1)


@sc_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if _rule(event) == "dnd":
        await sc_cmd.finish("理智检定是 COC 的规矩，DND 里没有。要用就 /切换规则 COC。")
    await reply_long(sc_cmd, dice.coc_sanity(args.extract_plain_text(), _lookup(event), _house(event)))


@en_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if _rule(event) == "dnd":
        await en_cmd.finish("技能成长是 COC 的规矩，DND 里没有。要用就 /切换规则 COC。")
    await reply_long(en_cmd, dice.coc_growth(args.extract_plain_text()))


@rh_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _hidden(rh_cmd, event, dice.rd(args.extract_plain_text()))


@rah_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _hidden(rah_cmd, event, _check_text(event, args.extract_plain_text(), 0))


async def _need_coc(matcher, event: MessageEvent) -> None:
    if _rule(event) == "dnd":
        await matcher.finish("疯狂症状和房规是 COC 的东西，DND 里没有。要用就 /切换规则 COC。")


@ti_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_coc(ti_cmd, event)
    await reply_long(ti_cmd, dice.madness(args.extract_plain_text(), False))


@li_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_coc(li_cmd, event)
    await reply_long(li_cmd, dice.madness(args.extract_plain_text(), True))


@setcoc_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_coc(setcoc_cmd, event)
    text = args.extract_plain_text().strip()
    if not text:
        await reply_long(setcoc_cmd, dice.house_text(_house(event)))
        return
    if text not in dice.HOUSE_RULES:
        await setcoc_cmd.finish("房规只有 0-5，不带数字可以看说明。")
    set_config(f"coc_house:{_scope(event)}", text)
    await setcoc_cmd.finish(f"好，这里改用房规 {text}：{dice.HOUSE_RULES[text]}")


async def _need_dnd(matcher, event: MessageEvent) -> None:
    if _rule(event) != "dnd":
        await matcher.finish("这是 DND 的指令，先 /切换规则 DND。")


@dnd_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_dnd(dnd_cmd, event)
    await reply_long(dnd_cmd, dice.dnd_stats(args.extract_plain_text()))


@ri_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_dnd(ri_cmd, event)
    await reply_long(ri_cmd, dice.initiative(_initiative.setdefault(_scope(event), {}), args.extract_plain_text()))


@at_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_dnd(at_cmd, event)
    await reply_long(at_cmd, dice.dnd_attack(args.extract_plain_text()))


# ---------- 规则与帮助 ----------

rule_cmd = _cmd("切换规则", {"规则", "setrule"})
help_cmd = _cmd("骰子帮助", {"骰子", "dicehelp", "rdhelp"})


@rule_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    text = args.extract_plain_text().strip().lower()
    cur = _rule(event)
    if not text:
        await rule_cmd.finish(f"这里现在用 {dice.RULE_TITLE[cur]} 规则。\n切换：/切换规则 COC 或 /切换规则 DND\n"
                              "DND 下 /ra 掷 D20+修正对难度，/rb 优势，/rp 劣势；/rd 两边通用。")
    rule = dice.RULE_NAMES.get(text)
    if rule is None:
        await rule_cmd.finish("只认 COC 和 DND 两种，换哪个？")
    if rule == cur:
        await rule_cmd.finish(f"本来就是 {dice.RULE_TITLE[rule]}，不用折腾。")
    set_config(f"dice_rule:{_scope(event)}", rule)
    tail = ("检定改成 D20+修正对难度（DC），/rb 优势、/rp 劣势；/sc 和 /en 是 COC 的东西，先收起来。" if rule == "dnd"
            else "检定回到 D100 对技能值，/rb 奖励骰、/rp 惩罚骰，/sc /en 照常用。")
    await rule_cmd.finish(f"好，这里改用 {dice.RULE_TITLE[rule]} 规则。{tail}")


@help_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await reply_long(help_cmd, dice.help_text(args.extract_plain_text(), _rule(event)))


# ---------- 角色属性 ----------

set_attr_cmd = _cmd("录属性", {"设置属性", "st"})
show_attr_cmd = _cmd("属性", {"我的属性"})
del_attr_cmd = _cmd("删属性", {"删除属性"})
clear_attr_cmd = _cmd("清空属性")


def _mine(event: MessageEvent):
    return _role_of(event)


@set_attr_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    role = _mine(event)
    if not role:
        await set_attr_cmd.finish(NEED_ROLE)
    pairs = attrs.parse_pairs(args.extract_plain_text())
    if not pairs:
        await set_attr_cmd.finish("格式：/录属性 力量60 敏捷70 SAN55\n也可以写 力量 60；数值 0~999。")
    ok, err = attrs.set_attrs(role["id"], pairs)
    if not ok:
        await set_attr_cmd.finish(err)
    await set_attr_cmd.finish(f"已记下「{role['name']}」的属性：" + "、".join(f"{n}{v}" for n, v in pairs))


@show_attr_cmd.handle()
async def _(event: MessageEvent):
    role = _mine(event)
    if not role:
        await show_attr_cmd.finish(NEED_ROLE)
    items = attrs.list_attrs(role["id"])
    if not items:
        await show_attr_cmd.finish(f"「{role['name']}」还没有记录属性。用 /录属性 力量60 敏捷70 记一下。")
    await reply_long(show_attr_cmd, f"「{role['name']}」的属性：\n" + "\n".join(f"{n}　{v}" for n, v in items))


@del_attr_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    role = _mine(event)
    if not role:
        await del_attr_cmd.finish(NEED_ROLE)
    name = args.extract_plain_text().strip()
    if not name:
        await del_attr_cmd.finish("格式：/删属性 力量")
    await del_attr_cmd.finish(f"已删掉「{name}」。" if attrs.delete_attr(role["id"], name) else f"没有「{name}」这个属性。")


@clear_attr_cmd.handle()
async def _(event: MessageEvent):
    role = _mine(event)
    if not role:
        await clear_attr_cmd.finish(NEED_ROLE)
    await clear_attr_cmd.finish(f"已清空「{role['name']}」的 {attrs.clear_attrs(role['id'])} 项属性。")
