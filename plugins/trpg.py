"""跑团模块（第 1 步）：群模式切换、角色卡（st / pc / nn）、/我是谁。设计见 docs/跑团模块设计.md。

跑团模式的群里：恋综的指令不响应；st / pc / nn 管跑团角色卡；骰子（plugins/dice.py）读当前卡的属性。
恋综模式的群、单聊：什么都不变。
"""
import hashlib

from nonebot import on_command
from nonebot.adapters.qq import GroupMessageCreateEvent, MessageEvent
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher, current_bot
from nonebot.message import run_preprocessor
from nonebot.params import CommandArg
from nonebot.rule import Rule

from core import admins, roles, trpg, trpglog
from core.roles import bind_qq as roles_bind
from plugins._logtap import tap_reply
from plugins._long import reply_long

# 跑团模式的群里不响应的插件（恋综玩法）
ROMANCE_PLUGINS = {"play", "lovemail", "relations", "rpg", "places", "forum", "entrance", "social", "phone"}


def _in_trpg_group(event) -> bool:
    return isinstance(event, GroupMessageCreateEvent) and trpg.get_mode(event.group_openid) == "trpg"


@run_preprocessor
async def _mute_romance(matcher: Matcher, event):
    if matcher.plugin_name in ROMANCE_PLUGINS and _in_trpg_group(event):
        raise IgnoredException("跑团模式的群里不响应恋综指令")


async def _trpg_rule(event: MessageEvent) -> bool:
    return _in_trpg_group(event)


async def _group_rule(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageCreateEvent)


def _cmd(name: str, aliases: set[str] | None = None, rule=_trpg_rule):
    return on_command(name, aliases=aliases or set(), rule=Rule(rule), priority=15, block=True)


def _me(event: GroupMessageCreateEvent) -> int:
    return trpg.player_for(event.author.member_openid, event.group_openid)


# ---------- 模式 ----------

mode_cmd = _cmd("模式", {"切换模式"}, rule=_group_rule)


@mode_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    text = args.extract_plain_text().strip().lower()
    cur = trpg.get_mode(event.group_openid)
    if not text:
        await mode_cmd.finish(f"这个群现在是「{trpg.MODES[cur]}模式」。\n切换：/模式 跑团 或 /模式 恋综（需要机器人管理员）。\n"
                              "跑团模式：用 /st 录角色卡属性，恋综的指令（短信、礼物、私约……）不响应。")
    new = trpg.MODE_ALIASES.get(text)
    if new is None:
        await mode_cmd.finish("只有「跑团」和「恋综」两种。")
    if not admins.is_admin(current_bot.get().self_id, event.author.member_openid):
        await mode_cmd.finish("切换模式需要机器人管理员。")
    if new == cur:
        await mode_cmd.finish(f"本来就是「{trpg.MODES[cur]}模式」。")
    trpg.set_mode(event.group_openid, new)
    tail = ("用 /st 力量60 敏捷70 录属性，/ra 侦查 检定；恋综的指令在这个群里先收起来。" if new == "trpg"
            else "恋综的指令恢复；角色卡和属性都保留着，切回跑团还在。")
    await mode_cmd.finish(f"好，这个群改成「{trpg.MODES[new]}模式」。{tail}")


# ---------- 绑定 QQ 号（QQ 号监听听到的发言靠它对上人）----------

bindqq_cmd = _cmd("绑定qq", {"绑定QQ", "绑QQ"}, rule=_group_rule)


@bindqq_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        await bindqq_cmd.finish("格式：/绑定QQ 你的QQ号。绑定后，后台的 连接群号组听到你的发言，会算在你自己名下（跑团 log 记在你的名字下，房间群里说话算到场）。")
    done = []
    role = roles.find_role_for_sender(current_bot.get().self_id, event.group_openid, event.author.member_openid)
    if role:
        err = roles_bind(role["id"], text)
        if err:
            await bindqq_cmd.finish(err)
        done.append(f"角色「{role['name']}」")
    if _in_trpg_group(event):
        err = trpg.bind_qq(_me(event), text)
        if err:
            await bindqq_cmd.finish(err)
        done.append("跑团身份")
    await bindqq_cmd.finish(f"好，QQ {text} 认到你的" + "、".join(done) + "名下了。" if done else "这里认不出你是谁：恋综要先有角色，跑团的群要先 /st 或 /pc new 建过卡。")


# ---------- 我是谁（认人实验）----------

whoami_cmd = _cmd("我是谁", {"whoami"}, rule=_group_rule)


@whoami_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    mid = hashlib.sha1(event.author.member_openid.encode()).hexdigest()[:8]
    gid = hashlib.sha1(event.group_openid.encode()).hexdigest()[:8]
    await whoami_cmd.finish(f"你的标识指纹：{mid}\n这个群的指纹：{gid}\n在两个群各发一次，看「你的」是否一样。")


# ---------- 角色卡 ----------

st_cmd = _cmd("st", {"录卡", "录属性", "设置属性", "看卡", "角色卡"})


@st_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    text = args.extract_plain_text().strip()
    pid = _me(event)
    low = text.lower()
    if low in ("", "show", "查看", "看"):
        card = trpg.active_card(event.group_openid, pid)
        if not card:
            await st_cmd.finish("你还没有角色卡。用 /st 力量60 敏捷70 录属性（会自动建一张），或 /pc new 名字 先建卡。")
        await reply_long(st_cmd, trpg.format_card(card))
        return
    card = trpg.active_card(event.group_openid, pid, create=True)
    if not card:
        await st_cmd.finish("建卡失败了，请稍后再试。")
    if low in ("clr", "clear", "清空"):
        await st_cmd.finish(f"已清空「{card['name']}」的 {trpg.clear_attrs(card['id'])} 项属性。")
    for key in ("del ", "rm ", "删 ", "删除 "):
        if low.startswith(key):
            name = text[len(key):].strip()
            ok = bool(name) and trpg.delete_attr(card["id"], name)
            await st_cmd.finish(f"已删掉「{name}」。" if ok else f"没有「{name}」这个属性。")
    items = trpg.parse_st(text)
    if not items:
        name = text.strip()
        val = trpg.get_attr(card["id"], name) if name else None
        if val is not None:
            await st_cmd.finish(f"「{card['name']}」的{trpg.canon(name)}：{val}")
        await st_cmd.finish("格式：/st 力量60 敏捷70 SAN55\n增减：/st 生命-3 SAN-1d6\n查看：/st　删除：/st del 力量　清空：/st clr")
    log_id = trpglog.recording_id(event.group_openid)
    ok, lines = trpg.apply_st(card["id"], items, log_id=log_id)
    if ok and log_id:
        trpglog.add_member(log_id, pid, card["id"])
    text_out = f"「{card['name']}」\n" + "\n".join(lines) if ok else lines[0]
    if ok:
        tap_reply(st_cmd, text_out)                       # 开着 log 时，属性变化也记进去
    await st_cmd.finish(text_out)


pc_cmd = _cmd("pc")


@pc_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    parts = args.extract_plain_text().split()
    pid = _me(event)
    sub = parts[0].lower() if parts else "list"
    cards = trpg.list_cards(pid)
    if sub in ("list", "列表", "ls"):
        if not cards:
            await pc_cmd.finish("你还没有角色卡。/pc new 名字 建一张。")
        cur = trpg.active_card(event.group_openid, pid)
        await reply_long(pc_cmd, "你的角色卡：\n" + "\n".join(
            f"{'▶ ' if cur and cur['id'] == c['id'] else '　'}{c['name']}（{trpg.dice.RULE_TITLE.get(c['rule'], c['rule'])}，{len(trpg.list_attrs(c['id']))} 项属性）" for c in cards))
        return
    if sub in ("new", "新建", "建"):
        if len(parts) < 2:
            await pc_cmd.finish("格式：/pc new 名字 [coc|dnd]")
        rule = trpg.dice.RULE_NAMES.get(parts[2].lower(), "") if len(parts) > 2 else ""
        card, err = trpg.new_card(pid, parts[1], rule or "coc")
        if not card:
            await pc_cmd.finish(err)
        trpg.set_active(event.group_openid, pid, card["id"])
        await pc_cmd.finish(f"已建卡「{card['name']}」，并在这个群里切到它。用 /st 力量60 …… 录属性。")
    if sub in ("切换", "switch", "tag", "选"):
        card = trpg.find_card(pid, parts[1]) if len(parts) > 1 else None
        if not card:
            await pc_cmd.finish("没有这张卡。看 /pc list。")
        trpg.set_active(event.group_openid, pid, card["id"])
        await pc_cmd.finish(f"这个群里改用「{card['name']}」。")
    if sub in ("del", "删除", "删"):
        card = trpg.find_card(pid, parts[1]) if len(parts) > 1 else None
        if not card:
            await pc_cmd.finish("没有这张卡。看 /pc list。")
        trpg.delete_card(card["id"])
        await pc_cmd.finish(f"已删掉「{card['name']}」。")
    await pc_cmd.finish("格式：/pc list　/pc new 名字 [coc|dnd]　/pc 切换 名字　/pc del 名字")


nn_cmd = _cmd("nn", {"改名"})


@nn_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    name = args.extract_plain_text().strip()
    pid = _me(event)
    if not name:
        await nn_cmd.finish(f"你现在的名字是「{trpg.player_name(pid)}」。改名：/nn 新名字")
    ok, err = trpg.set_player_name(pid, name)
    await nn_cmd.finish(f"好，以后叫你「{name}」。" if ok else err)
