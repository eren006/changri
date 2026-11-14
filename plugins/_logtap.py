"""跑团 log 的记录入口（下划线开头，不是插件）：玩家消息和机器人的骰子回复都从这里进 log。"""
from nonebot.adapters.qq import GroupMessageCreateEvent
from nonebot.matcher import current_event

from core import trpg, trpglog

# 玩家管理类指令不进 log（不是游戏内容）
_MANAGE = ("log", "说", "s", "ob", "模式", "切换模式", "我是谁", "whoami", "nn", "改名", "pc", "角色")
_last_line: dict[tuple[str, str], int] = {}        # (群, 人) -> 最近一条他发的 log 行 id，骰子回复靠它配对


def speaker_of(event: GroupMessageCreateEvent) -> tuple[int, str]:
    pid = trpg.player_for(event.author.member_openid, event.group_openid)
    name = trpg.player_name(pid)
    if name.startswith("玩家"):                          # 没设 /nn：用当前卡名更好认
        card = trpg.active_card(event.group_openid, pid)
        if card:
            name = card["name"]
    return pid, name


_KNOWN = set(_MANAGE) | {"r", "rd", "ra", "rb", "rp", "sc", "en", "dnd", "ri", "at", "st", "检定", "理智检定", "成长", "先攻", "攻击", "录卡", "录属性", "看卡", "角色卡"}


def _command_word(text: str) -> tuple[str, bool]:
    """是不是指令：带前缀（/ . 。）的都算；不带前缀时，第一个词是已知指令名才算（「指令必须带前缀」打开时，不带的一律是普通发言）。"""
    from core.db import get_config
    t = text.strip()
    prefixed = t[:1] in "/.。"
    body = t[1:].strip() if prefixed else t
    word = body.split(None, 1)[0].lower() if body else ""
    if prefixed:
        return word, True
    return word, word in _KNOWN and get_config("require_prefix", "0") != "1"


def record_incoming(event: GroupMessageCreateEvent) -> None:
    """群里一条消息进来：log 在记、群是跑团模式、没被规则跳过，就记一行。"""
    if trpg.get_mode(event.group_openid) != "trpg":
        return
    log_id = trpglog.recording_id(event.group_openid)
    if not log_id:
        return
    text = event.get_plaintext().strip()
    word, is_cmd = _command_word(text)
    if (is_cmd and word in _MANAGE) or trpglog.should_skip(text, is_cmd):
        return
    pid, name = speaker_of(event)
    if trpglog.is_observer(event.group_openid, pid):
        return
    dup = trpglog.find_duplicate(log_id, text, from_qq=False)       # QQ 号已经听到并记下了
    if dup:
        _last_line[(event.group_openid, event.author.member_openid)] = dup
        return
    lid = trpglog.add_line(log_id, event.author.member_openid, name, "say", text)
    if lid:
        _last_line[(event.group_openid, event.author.member_openid)] = lid
        card = trpg.active_card(event.group_openid, pid)
        trpglog.add_member(log_id, pid, card["id"] if card else None)


def record_say(event: GroupMessageCreateEvent, text: str) -> bool:
    """/说 台词：只记不回。返回是否记下了（没开 log / 被规则跳过 / 旁观者都是 False）。"""
    log_id = trpglog.recording_id(event.group_openid)
    if not log_id or trpglog.should_skip(text):
        return False
    pid, name = speaker_of(event)
    if trpglog.is_observer(event.group_openid, pid):
        return False
    lid = trpglog.add_line(log_id, event.author.member_openid, name, "say", text)
    if lid:
        card = trpg.active_card(event.group_openid, pid)
        trpglog.add_member(log_id, pid, card["id"] if card else None)
    return bool(lid)


def tap_reply(matcher, text: str) -> None:
    """机器人回的骰子结果 / st 结果：进 log，和触发它的那条消息配对。别的插件的回复不记。"""
    if getattr(matcher, "plugin_name", "") not in ("dice", "trpg"):
        return
    event = current_event.get(None)
    if not isinstance(event, GroupMessageCreateEvent) or trpg.get_mode(event.group_openid) != "trpg":
        return
    log_id = trpglog.recording_id(event.group_openid)
    if not log_id:
        return
    ref = _last_line.get((event.group_openid, event.author.member_openid))
    trpglog.add_line(log_id, "", "骰子", "dice" if matcher.plugin_name == "dice" else "bot", text, ref)


def record_listened(app_id: str, msg) -> None:
    """QQ 号听到的群消息（core/qqmanager 的挂钩，只会收到群号池里的群）：对上卡拉的群、是跑团模式、log 在记，就记一行。
    群号池里没填「关联群编号」的群对不上，什么都不做。"""
    from core import qqpool
    scope = qqpool.openid_of(msg.group_id)
    if not scope or trpg.get_mode(scope) != "trpg":
        return
    log_id = trpglog.recording_id(scope)
    if not log_id:
        return
    if msg.user_id in qqpool.skip_users() or msg.nickname in qqpool.skip_users():      # 官方机器人自己的发言（骰子回复由 tap_reply 记）
        return
    text = msg.text.strip()
    word, is_cmd = _command_word(text)
    if not text or (is_cmd and word in _MANAGE) or trpglog.should_skip(text, is_cmd):
        return
    key = "qq:" + msg.user_id
    pid = trpg.player_for(key, scope)
    name = trpg.player_name(pid)
    if name.startswith("玩家"):
        card = trpg.active_card(scope, pid)
        name = card["name"] if card else (msg.nickname or name)
    if trpglog.is_observer(scope, pid):
        return
    if trpglog.find_duplicate(log_id, text, from_qq=True):         # 卡拉那条路已经记过
        return
    if trpglog.add_line(log_id, key, name, "say", text):
        card = trpg.active_card(scope, pid)
        trpglog.add_member(log_id, pid, card["id"] if card else None)
