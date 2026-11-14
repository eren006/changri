"""跑团骰子：rd 通用掷骰；COC7（ra/rb/rp/sc/en）和 DND5e（ra/rb/rp/dnd/ri/at）；录属性/属性。
规则和做法参考索罗，口吻中性；属性存在这个系统自己的角色上。逻辑都在 core/dice.py。"""
from nonebot import on_command
from nonebot.adapters.qq import GroupMessageCreateEvent, MessageEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg
from nonebot.rule import Rule

from core import attrs, dice, roles
from core.db import get_config, set_config
from plugins._long import reply_long

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"
_initiative: dict[str, dict] = {}        # 先攻表，程序重启就清空，一场战斗够用


def _bid() -> str:
    return current_bot.get().self_id


def _enabled() -> bool:
    return get_config("dice_enabled", "1") == "1"


async def _on(event: MessageEvent) -> bool:
    return _enabled()


async def _explicit(event: MessageEvent) -> bool:
    """r / at / en / sc 这类很短的英文指令，必须带 / 或 . 才算，免得聊天里一句「at 你」就掷骰。"""
    return _enabled() and event.get_plaintext().lstrip().startswith(("/", ".", "。"))


def _cmd(name: str, aliases: set[str] | None = None, short: bool = False):
    return on_command(name, aliases=aliases or set(), rule=Rule(_explicit if short else _on), priority=20, block=True)


def _scope(event: MessageEvent) -> str:
    gid = getattr(event, "group_openid", None)
    if gid:
        return f"{_bid()}|{gid}"
    author = getattr(event, "author", None)
    return f"c2c|{_bid()}|{getattr(author, 'user_openid', '') or event.get_user_id()}"


def _rule(event: MessageEvent) -> str:
    return get_config(f"dice_rule:{_scope(event)}", "") or get_config("dice_default_rule", "coc") or "coc"


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


def _lookup(event: MessageEvent):
    proxy = _proxy_openid(event)

    def lookup(name: str):
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


@rd_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await reply_long(rd_cmd, dice.rd(args.extract_plain_text()))


async def _check(matcher, event: MessageEvent, raw: str, extra: int) -> None:
    if _rule(event) == "dnd":
        text = dice.dnd_check(raw, (extra > 0) - (extra < 0), _lookup(event))
    else:
        text = dice.coc_check(raw, extra, _lookup(event), proxy=bool(_proxy_openid(event)))
    await reply_long(matcher, text)


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
    await reply_long(sc_cmd, dice.coc_sanity(args.extract_plain_text(), _lookup(event)))


@en_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if _rule(event) == "dnd":
        await en_cmd.finish("技能成长是 COC 的规矩，DND 里没有。要用就 /切换规则 COC。")
    await reply_long(en_cmd, dice.coc_growth(args.extract_plain_text()))


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
