"""跑团骰子：rd 通用掷骰；COC7（ra/rb/rp/sc/en）和 DND5e（ra/rb/rp/dnd/ri/at）；录属性/属性。
规则和做法参考索罗，口吻中性；属性存在这个系统自己的角色上。逻辑都在 core/dice.py。"""
import json
import re
import time

from nonebot import logger, on_command
from nonebot.adapters.qq import GroupMessageCreateEvent, MessageEvent, MessageSegment
from nonebot.adapters.qq.exception import ActionFailed
from nonebot.matcher import current_bot
from nonebot.params import CommandArg
from nonebot.rule import Rule

from changri.core import attrs, dice, dicefun, messaging, roles, trpg, trpglog
from changri.core.db import get_config, set_config
from changri.core import dicecard
from changri.core.qqsend import make_send, reply_long

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"
MD_MAX_CHARS = 1500                       # 卡片再长就按纯文字发
_md_off_until = 0.0                       # QQ 拒绝过 Markdown（多半是没权限）后，这个时间点之前不再试


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
    return _card_lookup(card, name)


def _card_lookup(card: dict, name: str):
    got = trpg.skill_value(card["id"], name)
    if got is None:
        have = "、".join(n for n, _ in trpg.list_attrs(card["id"])) or "（没有）"
        return f"角色「{card['name']}」还没有「{name}」这个属性。现有：{have}\n用 /st {name}60 记一个。"
    val, is_default = got
    if card["rule"] == "dg" and trpg.canon(name) in dice.DG_STATS:          # 绿色三角洲属性检定 = 属性×5
        return card["name"], val * 5, f"，{trpg.canon(name)}{val}×5"
    return (card["name"], val, "，初始值") if is_default else (card["name"], val)


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


# ---------- 先攻表（存在 config 里，重启不丢）----------

def _init_load(scope: str) -> dict:
    try:
        data = json.loads(get_config(f"initiative:{scope}", "") or "{}")
    except ValueError:
        return {}
    return {k: tuple(v) for k, v in data.items() if isinstance(v, (list, tuple)) and len(v) == 2} if isinstance(data, dict) else {}


def _init_save(scope: str, table: dict) -> None:
    set_config(f"initiative:{scope}", json.dumps(table, ensure_ascii=False))


# ---------- 发送：优先发 Markdown 小卡片，发不了就退回文字 ----------

async def _reply(matcher, text) -> None:
    """骰子结果。带 .card 的（core/dice.py 的 Rolled）先发 Markdown 小卡片：
    · QQ 明确拒绝（没开 Markdown 权限、内容不合法……）：消息一定没发出去，退回纯文字，一小时内不再试；
    · 其他异常（超时、回包出错）：消息可能已经发出去了，不补发，免得一条卡片 + 一条文字；
    后台「跑团骰子」里的 dice_card 设成 0 可以整个关掉卡片。log 里记的永远是纯文字。"""
    global _md_off_until
    card = getattr(text, "card", None)
    if card and get_config("dice_card", "1") == "1" and time.time() >= _md_off_until and len(card) <= MD_MAX_CHARS:
        try:
            await matcher.send(MessageSegment.markdown(card))
        except ActionFailed as e:
            _md_off_until = time.time() + 3600
            logger.warning("[骰子] Markdown 被 QQ 拒绝，一小时内改发纯文字：" + ascii(str(e))[:200])
        except Exception as e:  # noqa: BLE001
            logger.warning("[骰子] Markdown 发送出现异常（可能已发出，不再补发纯文字）：" + type(e).__name__ + " " + ascii(str(e))[:200])
            _tap(matcher, text)
            return
        else:
            _tap(matcher, text)
            return
    await reply_long(matcher, str(text))


def _tap(matcher, text) -> None:
    try:
        from changri.plugins._logtap import tap_reply
        tap_reply(matcher, str(text))
    except Exception as e:  # noqa: BLE001 记 log 出错不能影响回复
        logger.warning(f"[跑团log] 记录回复出错：{e}")


def _with_note(res, note: str):
    """给结果末尾加一行说明（文字和卡片都加）。"""
    card = (res.card + f"\n\n*{dicecard.md_escape(note)}*") if getattr(res, "card", None) else None
    return dice.Rolled(str(res) + "\n" + note, card)


def _write_attr(event: MessageEvent, name: str, value: int, source: str) -> bool:
    """把检定后的新数值写回当前角色：跑团模式写当前角色卡（带变动历史、记进正在录的 log），恋综模式写绑定的角色。写不了返回 False。"""
    value = max(0, min(trpg.MAX_VALUE, value))
    if not isinstance(event, GroupMessageCreateEvent):
        return False
    if trpg.get_mode(event.group_openid) == "trpg":
        pid = trpg.player_for(event.author.member_openid, event.group_openid, create=False)
        card = trpg.active_card(event.group_openid, pid) if pid else None
        if not card:
            return False
        ok, _ = trpg.apply_st(card["id"], [(trpg.canon(name), str(value))], source=source, log_id=trpglog.recording_id(event.group_openid))
        return ok
    role = _role_of(event)
    if not role:
        return False
    ok, _ = attrs.set_attrs(role["id"], [(attrs.norm(name), value)])
    return ok


# ---------- 掷骰 ----------

rd_cmd = _cmd("rd", {"r"}, short=True)
ra_cmd = _cmd("ra", {"检定"}, short=True)
rb_cmd = _cmd("rb", short=True)
rp_cmd = _cmd("rp", short=True)
sc_cmd = _cmd("sc", {"理智检定"}, short=True)
en_cmd = _cmd("en", {"成长"}, short=True)
rav_cmd = _cmd("rav", {"对抗"}, short=True)
dnd_cmd = _cmd("dnd", {"属性生成"}, short=True)
coc_cmd = _cmd("coc", {"制卡", "车卡"}, short=True)
dg_cmd = _cmd("dg", short=True)
ri_cmd = _cmd("ri", {"先攻"}, short=True)
at_cmd = _cmd("at", {"攻击"}, short=True)
rh_cmd = _cmd("rh", {"暗骰"}, short=True)
rah_cmd = _cmd("rah", {"暗检定"}, short=True)
ti_cmd = _cmd("ti", {"临时疯狂"}, short=True)
li_cmd = _cmd("li", {"总结疯狂"}, short=True)
phobia_cmd = _cmd("恐惧症状", {"phobia"})
mania_cmd = _cmd("狂躁症状", {"mania"})
setcoc_cmd = _cmd("setcoc", {"房规"}, short=True)


@rd_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _reply(rd_cmd, dice.rd(args.extract_plain_text()))


def _check_text(event: MessageEvent, raw: str, extra: int) -> str:
    if _rule(event) == "dnd":
        return dice.dnd_check(raw, (extra > 0) - (extra < 0), _lookup(event))
    if _rule(event) == "dg":
        return dice.dg_check(raw, extra, _lookup(event), proxy=bool(_proxy_openid(event)))
    return dice.coc_check(raw, extra, _lookup(event), proxy=bool(_proxy_openid(event)), house=_house(event))


def _trpg_card(event: MessageEvent, member: str | None = None) -> dict | None:
    """跑团群里这个人（member 不给就是发指令的人）当前的角色卡；不是跑团群或没有卡返回 None。"""
    if not _in_trpg(event):
        return None
    pid = trpg.player_for(member or event.author.member_openid, event.group_openid, create=False)
    return trpg.active_card(event.group_openid, pid) if pid else None


def _earns_mark(rule: str, res) -> bool:
    """这次检定该不该记成长标记：COC 是成功时记，绿色三角洲是失败时记，DND 不记。数值要取自角色卡。"""
    if not getattr(res, "label", "") or not getattr(res, "from_card", False) or rule == "dnd":
        return False
    ok = str(getattr(res, "verdict", "")).endswith("成功")
    return not ok if rule == "dg" else ok


def _mark_growth(event: MessageEvent, res):
    """跑团：该记成长标记的检定（见 _earns_mark），给（被代骰的人或自己的）当前卡上这项技能记一笔，第一次标上时在结果末尾说一声。"""
    label = getattr(res, "label", "")
    if not _earns_mark(_rule(event), res):
        return res
    card = _trpg_card(event, _proxy_openid(event))
    if card and trpg.mark_growth(card["id"], label):
        return _with_note(res, f"📌 {card['name']}的{trpg.canon(label)}记下成长标记，团后发 /en 一起掷")
    return res


async def _check(matcher, event: MessageEvent, raw: str, extra: int) -> None:
    await _reply(matcher, _mark_growth(event, _check_text(event, raw, extra)))


def _in_trpg(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageCreateEvent) and trpg.get_mode(event.group_openid) == "trpg"


async def _whisper(event: MessageEvent, text: str) -> bool:
    """把暗骰结果私下发给本人。恋综模式的群：先发到 TA 的个人群，没有角色就试官方的单聊；
    跑团模式的群：恋综的角色和个人群跟跑团没关系，只走官方单聊。都发不出去返回 False（结果不会在群里露出来）。"""
    role = None if _in_trpg(event) else _role_of(event)
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
    if _in_trpg(event):                                      # 跑团：称呼用当前角色卡的名字，没有就「有人」
        pid = trpg.player_for(event.author.member_openid, event.group_openid, create=False)
        card = trpg.active_card(event.group_openid, pid) if pid else None
        who = card["name"] if card else "有人"
        hint = ""
    else:                                                    # 恋综：有个人群的角色，也可以发到个人群
        role = _role_of(event)
        who = role["name"] if role else "有人"
        hint = "有个人群的话，结果会发到个人群；"
    if await _whisper(event, "🎲 暗骰结果（只有你看得到）\n" + text):
        await matcher.finish(f"🎲 {who} 掷了一次暗骰，结果已私下发给 TA。")
    await matcher.finish(f"🎲 {who} 想掷暗骰，但私下发不出去，所以没掷。{hint}官方机器人只能私聊白名单里的人：请把执行暗骰的人（KP）加进机器人的白名单，加好后就能收到私聊。")


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
    rule = _rule(event)
    if rule == "dnd":
        await sc_cmd.finish("理智检定是 COC 和绿色三角洲的规矩，DND 里没有。要用就 /切换规则 COC。")
    res = (dice.dg_sanity(args.extract_plain_text(), _lookup(event)) if rule == "dg"
           else dice.coc_sanity(args.extract_plain_text(), _lookup(event), _house(event)))
    raw_res = res                                        # 加注释后的新对象不带 san_left 等字段，崩溃点判断用这个
    if getattr(res, "from_lookup", False):            # 理智是从角色上读的：扣完写回去
        if _proxy_openid(event):
            res = _with_note(res, f"代骰不会自动写回，请 TA 自己 /st 理智{res.san_left}")
        elif res.loss and _write_attr(event, "SAN", res.san_left, "理智损失"):
            res = _with_note(res, f"已写回：{res.who}的理智 {res.san_before} → {res.san_left}")
        elif res.loss:
            res = _with_note(res, f"没能写回角色，请自己 /st 理智{res.san_left}")
        if rule == "dg" and raw_res.loss and not _proxy_openid(event):
            res = _breaking_point(event, raw_res, res)
    await _reply(sc_cmd, res)


def _breaking_point(event: MessageEvent, sc, res):
    """绿色三角洲：理智降到崩溃点（含）以下，提醒获得精神障碍，并把崩溃点重设为 理智 - 意志。"""
    card = _trpg_card(event)
    if not card:
        return res
    bp = trpg.skill_value(card["id"], "崩溃点")
    if not bp or sc.san_left > bp[0] or sc.san_before <= bp[0]:
        return res
    pow_ = trpg.get_attr(card["id"], "意志")
    note = f"⚠️ 理智 {sc.san_left} 到了崩溃点 {bp[0]}：获得一种精神障碍（由 Handler 决定）。"
    if pow_ is not None:
        new_bp = max(0, sc.san_left - pow_)
        _write_attr(event, "崩溃点", new_bp, "崩溃点重设")
        note += f"崩溃点重设为 {sc.san_left} - {pow_} = {new_bp}。"
    else:
        note += "卡上没有意志，请自己 /st 崩溃点 设新的崩溃点（理智 - 意志）。"
    return _with_note(res, note)


@en_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if _rule(event) == "dnd":
        await en_cmd.finish("技能成长是 COC 和绿色三角洲的规矩，DND 里没有。要用就 /切换规则 COC。")
    text = args.extract_plain_text().strip()
    if _in_trpg(event) and (not text or text[0] in "+-＋－" or text.lower() in EN_LIST | EN_CLEAR):
        await _en_marks(event, text)
        return
    if _rule(event) == "dg":
        await _reply(en_cmd, _dg_en_one(event, text))
        return
    res = dice.coc_growth(text, _lookup(event))
    if getattr(res, "from_lookup", False) and res.gain:       # 技能是从角色上读的：成长了就记上
        if _proxy_openid(event):
            res = _with_note(res, f"代骰不会自动写回，请 TA 自己 /st {res.label}{res.value + res.gain}")
        elif _write_attr(event, res.label, res.value + res.gain, "技能成长"):
            res = _with_note(res, f"已写回：{res.who}的{res.label} {res.value} → {res.value + res.gain}")
        else:
            res = _with_note(res, f"没能写回角色，请自己 /st {res.label}{res.value + res.gain}")
    await _reply(en_cmd, res)


def _dg_en_one(event: MessageEvent, text: str):
    """绿色三角洲 /en 技能 [数值]：直接 +1D4；数值取自角色卡时写回。"""
    m = re.match(r"^(.*?)\s*(\d{1,3})$", text)
    if m and m.group(1).strip():
        return dice.dg_improve(m.group(1).strip(), int(m.group(2)))
    if not text:
        return "格式：/en 技能名 [当前数值]，绿色三角洲的成长是直接 +1D4"
    got = _lookup(event)(text)
    if isinstance(got, str):
        return got
    res = dice.dg_improve(text, got[1])
    if _proxy_openid(event):
        return _with_note(res, f"代骰不会自动写回，请 TA 自己 /st {text}{res.value + res.gain}")
    if _write_attr(event, text, res.value + res.gain, "技能成长"):
        return _with_note(res, "已写回角色卡。")
    return _with_note(res, f"没能写回角色，请自己 /st {text}{res.value + res.gain}")


EN_LIST = {"list", "ls", "标记", "看"}
EN_CLEAR = {"clr", "clear", "清空"}
EN_USAGE = ("/en　一次掷完所有标了成长的技能\n/en+ 侦查 图书馆　手动加标记；/en- 侦查　去掉\n"
            "/en list　看标记；/en clr　清空；/en 侦查　只掷这一项")


async def _en_marks(event: MessageEvent, text: str) -> None:
    """跑团群的成长标记：/en（全部掷）、/en+ /en-（加减）、/en list、/en clr。"""
    card = _trpg_card(event)
    if not card:
        await en_cmd.finish("你还没有角色卡。先 /st 侦查60 录属性，检定成功会自动记成长标记。")
    cid, who = card["id"], card["name"]
    low = text.lower()
    if low in EN_CLEAR:
        n = trpg.clear_growth(cid)
        await en_cmd.finish(f"已清掉「{who}」的 {n} 个成长标记。" if n else f"「{who}」本来就没有成长标记。")
    if text and text[0] in "+-＋－":
        names = [n for n in re.split(r"[,，;；、\s]+", text[1:]) if n]
        if not names:
            await en_cmd.finish("要写技能名，例：/en+ 侦查 图书馆")
        add = text[0] in "+＋"
        done, miss = [], []
        for n in names:
            if add:
                if not trpg.can_grow(n):
                    miss.append(f"{n}（属性不能成长）")
                elif trpg.skill_value(cid, n) is None:
                    miss.append(f"{n}（卡上没有）")
                else:
                    trpg.mark_growth(cid, n)
                    done.append(trpg.canon(n))
            elif trpg.unmark_growth(cid, n):
                done.append(trpg.canon(n))
            else:
                miss.append(n)
        out = (f"「{who}」{'记上' if add else '去掉'}成长标记：{'、'.join(done)}" if done else "")
        if miss:
            out += ("\n" if out else "") + ("没标上：" if add else "本来就没标：") + "、".join(miss)
        await en_cmd.finish(out)
    marks = trpg.growth_marks(cid)
    if low in EN_LIST:
        await en_cmd.finish(f"「{who}」标了成长的技能：{'、'.join(marks)}\n发 /en 一次掷完。" if marks
                            else f"「{who}」还没有成长标记。\n{EN_USAGE}")
    if not marks:
        await en_cmd.finish(f"「{who}」还没有成长标记（{'技能检定失败' if _rule(event) == 'dg' else '技能检定成功'}会自动记）。\n{EN_USAGE}")
    dg = _rule(event) == "dg"
    lines, grown = [], 0
    for name in marks:
        value = min((trpg.skill_value(cid, name) or (0, False))[0], 100)
        res = dice.dg_improve(name, value) if dg else dice.coc_growth(f"{name} {value}")
        line = str(res)
        if res.gain:
            grown += 1
            if not _write_attr(event, name, value + res.gain, "技能成长"):
                line += f"（没能写回，请自己 /st {name}{value + res.gain}）"
        lines.append(line)
    trpg.clear_growth(cid)
    await _reply(en_cmd, dice.Rolled(f"「{who}」{'技能成长（每项 +1D4）' if dg else '成长检定'} ×{len(marks)}：\n" + "\n".join(lines)
                                     + f"\n成长了 {grown} 项，已写回角色卡；成长标记已清空。"))


@rav_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_coc(rav_cmd, event)
    await _reply(rav_cmd, dice.coc_oppose(args.extract_plain_text(), _lookup(event), _house(event)))


@rh_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _hidden(rh_cmd, event, dice.rd(args.extract_plain_text()))


@rah_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _hidden(rah_cmd, event, _check_text(event, args.extract_plain_text(), 0))


async def _need_coc(matcher, event: MessageEvent) -> None:
    rule = _rule(event)
    if rule != "coc":
        await matcher.finish(f"这是 COC 的指令（疯狂症状、房规、对抗检定、制卡……），{dice.RULE_TITLE[rule]}里没有。要用就 /切换规则 COC。")


@ti_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_coc(ti_cmd, event)
    await reply_long(ti_cmd, dice.madness(args.extract_plain_text(), False))


@li_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_coc(li_cmd, event)
    await reply_long(li_cmd, dice.madness(args.extract_plain_text(), True))


@phobia_cmd.handle()
async def _(event: MessageEvent):
    await _need_coc(phobia_cmd, event)
    await reply_long(phobia_cmd, dice.phobia())


@mania_cmd.handle()
async def _(event: MessageEvent):
    await _need_coc(mania_cmd, event)
    await reply_long(mania_cmd, dice.mania())


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


@coc_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    text = args.extract_plain_text().strip()
    if text and not text.isdigit():           # 「coc7 规则……」这类聊天不当指令
        await coc_cmd.finish()
    await _need_coc(coc_cmd, event)
    await reply_long(coc_cmd, dice.coc_stats(text), as_image=False)     # 制卡结果发文字，方便复制去录卡


@dg_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    text = args.extract_plain_text().strip()
    if text and not text.isdigit():           # 「dg 那个团……」这类聊天不当指令
        await dg_cmd.finish()
    if _rule(event) != "dg":
        await dg_cmd.finish("这是绿色三角洲的制卡指令，先 /切换规则 绿色三角洲。")
    await reply_long(dg_cmd, dice.dg_stats(text), as_image=False)


@dnd_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_dnd(dnd_cmd, event)
    await reply_long(dnd_cmd, dice.dnd_stats(args.extract_plain_text()), as_image=False)


@ri_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_dnd(ri_cmd, event)
    scope = _scope(event)
    table = _init_load(scope)
    text = dice.initiative(table, args.extract_plain_text())
    _init_save(scope, table)
    await reply_long(ri_cmd, text)


@at_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _need_dnd(at_cmd, event)
    await _reply(at_cmd, dice.dnd_attack(args.extract_plain_text()))


# ---------- 规则与帮助 ----------

rule_cmd = _cmd("切换规则", {"规则", "setrule"})
help_cmd = _cmd("骰子帮助", {"骰子", "dicehelp", "rdhelp"})


@rule_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    text = args.extract_plain_text().strip().lower()
    cur = _rule(event)
    if not text:
        await rule_cmd.finish(f"这里现在用 {dice.RULE_TITLE[cur]} 规则。\n切换：/切换规则 COC、/切换规则 DND、/切换规则 绿色三角洲\n"
                              "DND 下 /ra 掷 D20+修正对难度，/rb 优势，/rp 劣势；绿色三角洲是 D100，两位相同算大成功/大失败；/rd 都通用。")
    rule = dice.RULE_NAMES.get(text)
    if rule is None:
        await rule_cmd.finish("只认 COC、DND、绿色三角洲 三种，换哪个？")
    if rule == cur:
        await rule_cmd.finish(f"本来就是 {dice.RULE_TITLE[rule]}，不用折腾。")
    set_config(f"dice_rule:{_scope(event)}", rule)
    tail = {"dnd": "检定改成 D20+修正对难度（DC），/rb 优势、/rp 劣势；/sc 和 /en 是 COC 的东西，先收起来。",
            "dg": "检定是 D100 对技能值，掷出两位相同（11、22……）成功算大成功、失败算大失败；没有奖励/惩罚骰，用 +20/-20；"
                  "检定失败记成长标记，团后 /en 每项 +1D4。/骰子帮助 dg 看全部。"}.get(
        rule, "检定回到 D100 对技能值，/rb 奖励骰、/rp 惩罚骰，/sc /en 照常用。")
    await rule_cmd.finish(f"好，这里改用 {dice.RULE_TITLE[rule]} 规则。{tail}")


@help_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await reply_long(help_cmd, dice.help_text(args.extract_plain_text(), _rule(event)))


# ---------- 队伍 ----------

team_cmd = _cmd("team", {"队伍"})
TEAM_ROLLS = {"ra": 0, "rc": 0, "检定": 0, "rb": 1, "rp": -1}


def _mentioned(event: MessageEvent) -> list[str]:
    out = []
    for m in getattr(event, "mentions", None) or []:
        if getattr(m, "scope", None) == "single" and not getattr(m, "bot", False) and not getattr(m, "is_you", False):
            openid = getattr(m, "member_openid", "")
            if openid and openid not in out:
                out.append(openid)
    return out


@team_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not _in_trpg(event):
        await team_cmd.finish("队伍只在跑团模式的群里用（/模式 跑团）。")
    gid = event.group_openid
    parts = args.extract_plain_text().split()
    if not parts or parts[0].lower() in ("list", "ls", "列表"):
        teams = trpg.list_teams(gid)
        await team_cmd.finish("本群的队伍：\n" + "\n".join(f"· {t['name']}（{t['n']} 人）" for t in teams) if teams
                              else "本群还没有队伍。/team 名字 add @甲 @乙 建一个。\n" + dice.HELP_TEAM)
    name, sub, rest = parts[0], (parts[1].lower() if len(parts) > 1 else ""), parts[2:]
    if sub in ("add", "加", "加入"):
        pids = [trpg.player_for(o, gid) for o in (_mentioned(event) or [event.author.member_openid])]
        team, err = trpg.team_add(gid, name, pids)
        if not team:
            await team_cmd.finish(err)
        await team_cmd.finish(f"队伍「{name}」现在 {len(trpg.team_members(team['id']))} 人："
                              + "、".join(trpg.player_name(p) for p in trpg.team_members(team["id"])))
    team = trpg.find_team(gid, name)
    if not team:
        await team_cmd.finish(f"本群没有「{name}」这个队伍。/team {name} add @甲 @乙 建一个，/team list 看已有的。")
    members = trpg.team_members(team["id"])
    if sub in ("del", "rm", "删", "移出"):
        pids = [p for p in (trpg.player_for(o, gid, create=False) for o in (_mentioned(event) or [event.author.member_openid])) if p]
        n = trpg.team_remove(team["id"], pids)
        await team_cmd.finish(f"从「{name}」移出 {n} 人。" if n else "这几位本来就不在队伍里。")
    if sub in ("clear", "clr", "解散"):
        trpg.team_delete(team["id"])
        await team_cmd.finish(f"队伍「{name}」解散了。")
    if not members:
        await team_cmd.finish(f"「{name}」里还没有人。/team {name} add @甲 @乙")

    def label(pid: int, card) -> str:
        who = trpg.player_name(pid)
        return f"{who}（{card['name']}）" if card and card["name"] != who else who

    rows = [(pid, trpg.active_card(gid, pid)) for pid in members]
    if not sub:
        await team_cmd.finish(f"队伍「{name}」：\n" + "\n".join(f"· {label(p, c)}" + ("" if c else "　还没有角色卡") for p, c in rows))
    if sub in TEAM_ROLLS:
        raw = " ".join(rest)
        if not raw:
            await team_cmd.finish(f"要写检定什么，例：/team {name} ra 聆听")
        rule = _rule(event)
        lines = []
        for pid, card in rows:
            if not card:
                lines.append(f"{label(pid, card)}：还没有角色卡")
                continue
            lookup = lambda n, card=card: _card_lookup(card, n)  # noqa: E731
            if rule == "dnd":
                res = dice.dnd_check(raw, TEAM_ROLLS[sub], lookup)
            elif rule == "dg":
                res = dice.dg_check(raw, TEAM_ROLLS[sub], lookup)
            else:
                res = dice.coc_check(raw, TEAM_ROLLS[sub], lookup, house=_house(event))
            line = f"{label(pid, card)}：{res}"
            if _earns_mark(rule, res) and trpg.mark_growth(card["id"], res.label):
                line += "　📌成长标记"
            lines.append(line)
        await _reply(team_cmd, dice.Rolled(f"队伍「{name}」{sub} {raw}：\n" + "\n".join(lines)))
        return
    attrs_wanted = parts[1:]
    out = []
    for pid, card in rows:
        if not card:
            out.append((None, f"{label(pid, card)}：还没有角色卡"))
            continue
        got = [trpg.skill_value(card["id"], a) for a in attrs_wanted]
        cells = [f"{a} {'—' if g is None else g[0]}{'（初始）' if g and g[1] else ''}" for a, g in zip(attrs_wanted, got)]
        out.append((got[0][0] if got[0] else -1, f"{label(pid, card)}：" + "　".join(cells)))
    out.sort(key=lambda r: -2 if r[0] is None else r[0], reverse=True)       # 按第一项从高到低，没有卡的排最后
    await reply_long(team_cmd, f"队伍「{name}」的{'、'.join(attrs_wanted)}：\n" + "\n".join(t for _, t in out))


# ---------- 小工具 ----------

name_cmd = _cmd("name", {"随机名字", "取名"})
who_cmd = _cmd("who", {"随机顺序"})
draw_cmd = _cmd("draw", {"抽牌"})
jrrp_cmd = _cmd("jrrp", {"今日人品"})


@name_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await name_cmd.finish(dicefun.names(args.extract_plain_text()))


@who_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await who_cmd.finish(dicefun.who(args.extract_plain_text()))


@draw_cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await reply_long(draw_cmd, dicefun.draw(args.extract_plain_text()))


@jrrp_cmd.handle()
async def _(event: MessageEvent):
    await jrrp_cmd.finish(dicefun.jrrp(f"{_bid()}|{event.get_user_id()}"))


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
