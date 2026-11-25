"""总览上的「快捷开关」：一季里常用、但散在各个页面里的开关，集中放在总览一处。

每个快捷开关就是原页面上那个开关本身（同一个设置）：在总览开关和在原页面开关完全一样，两边显示的状态也一样。
原页面的保存接口也调这里的 set_switch，两边不会各写各的。扩展没开的，对应的开关不列出来。
"""
from __future__ import annotations

from changri.core import extensions, godmode as god_core, season as season_core, windows
from changri.core.db import get_config, get_int, set_config


def _set_autoday(on: bool) -> None:
    set_config("auto_day", "1" if on else "0")
    if on:                                      # 从今天开始算，不会一打开就补加好几天
        set_config("auto_day_last", season_core.today())


def _gongzhi_note() -> str:
    from changri.core import deadlines
    if deadlines.passed("gongzhi"):
        return "公知已过截止时间，现在自动对所有人展示，这个开关关着也一样"
    dl = deadlines.label("gongzhi")
    return f"设了截止时间 {dl}，到点自动展示" if dl else ""


def _phone_on() -> bool:
    return extensions.is_enabled("phone") and get_config("phone_enabled", "0") == "1"


# key: (名字, 开着时的说明, 关着时的说明, 读状态, 写状态, 原页面 (endpoint, 参数), 什么时候列出来, 额外说明)
_SEASON = {
    "lovemail": ("心动信信箱", "玩家可以投信，每天按时派送", "玩家不能投信，也不派送",
                 lambda: get_config("lovemail_enabled", "1") != "0",
                 lambda on: set_config("lovemail_enabled", "1" if on else "0"),
                 ("lovemail_page", {}), lambda: extensions.is_enabled("lovemail"), None),
    "gongzhi": ("展示公知", "玩家能看所有人的公知", "玩家只能看自己的公知",
                lambda: get_config("gongzhi_public", "0") == "1",
                lambda on: set_config("gongzhi_public", "1" if on else "0"),
                ("relations_page", {"view": "sheets"}), lambda: extensions.is_enabled("relations"), _gongzhi_note),
    "autoday": ("每天 23:59 自动加一天", "季度天数每天自动往后推", "天数要管理员手动加",
                lambda: get_config("auto_day", "0") == "1", _set_autoday,
                ("season_page", {}), lambda: extensions.is_enabled("season"), None),
    "phone_paused": ("小手机暂停通讯", "玩家能看，不能发", "小手机正常收发",
                     lambda: get_config("phone_paused", "0") == "1",
                     lambda on: set_config("phone_paused", "1" if on else "0"),
                     ("phone_page", {}), _phone_on, None),
}


def _god(k: str, label: str) -> tuple:
    return (label, "所有人都能用", "所有人都用不了（全体关闭）",
            lambda: not god_core.global_off(k), lambda on: god_core.set_global(k, on),
            ("god_page", {"role": "all"}), lambda: k in god_core.shown_features(), None)


# ---------- 「功能与设置」里的设置项：开关直接改，要填字的点了跳到那一项 ----------

def _card_of(key: str) -> tuple[str | None, str]:
    """设置项属于哪张功能卡片：(扩展 id，核心卡片为 None；「功能与设置」的哪个标签)。"""
    from .common import SECTIONS, group_card
    group = next((g for g, items in SECTIONS if key in items), None)
    card = group_card(group) if group else None
    if not card or card.get("kind") == "core":
        return None, "core"
    return card["id"], "custom" if card.get("personal") else "aux"


def _shown(key: str, also: str = "") -> bool:
    ext, _ = _card_of(key)
    return (ext is None or extensions.is_enabled(ext)) and (not also or extensions.is_enabled(also))


def _page(key: str) -> tuple[str, dict]:
    """「功能与设置」里直接打开这一项的链接（extensions.html 认 ?open=设置项）。"""
    _, tab = _card_of(key)
    return "extensions_page", ({"open": key} if tab == "core" else {"tab": tab, "open": key})


def _default(key: str) -> str:
    from .common import SETTINGS
    return SETTINGS[key][1]


def _cfg(key: str, label: str, on_desc, off_desc: str) -> tuple:
    """一个「1 开 / 0 关」的设置项做成快捷开关（键是 cfg:设置项）。on_desc 可以是函数（要带概率之类的现值）。"""
    return (label, on_desc, off_desc,
            lambda: get_config(key, _default(key)) == "1",
            lambda on: set_config(key, "1" if on else "0"),
            _page(key), lambda: _shown(key), None)


# 机器人自己往群里发的消息（按发到哪里排）。只收开关类；频率、每日提示这种要填字的在 _auto_values 里
_AUTO = [
    ("gift_public_send", "礼物公开播报", lambda: f"送达后按 {get_int('gift_public_chance', 50)}% 概率发到公告群", "不发"),
    ("wish_public_send", "挂心愿同步到公告群", "有人挂心愿就发到公告群", "不发"),
    ("wish_stage_send", "挂心愿发到戏群", "有人挂心愿就发到戏群（只显示性别和昵称）", "不发"),
    ("lovemail_notice5", "心动信邮差提醒", "派送前 5 分钟在公告群提醒；季度进行中才发", "不发"),
    ("lovemail_notice10", "心动信截止预告", "派送前 10 分钟提醒各个人群；季度进行中才发", "不发"),
    ("lovemail_expose", "心动信曝光", lambda: f"派送时按 {get_int('lovemail_expose_chance', 10)}% 概率把信的内容公开到公告群", "不曝光"),
    ("letter_public_send", "短信出岔子公开播报", lambda: f"短信出了岔子时按 {get_int('letter_public_chance', 50)}% 概率发到公告群", "不发"),
    ("room_auto_review", "房间结束自动发复盘", "房间结束时把本场戏文发给每个参与者", "不发，要的人自己发「复盘」"),
    ("season_report", "季末报告", "结束季度时给每位玩家的个人群发结算海报", "不发"),
    ("season_save_reminder", "收尾前存档提醒", "结束季度时提醒每位玩家趁收尾前存戏", "不提醒"),
]


def _auto() -> dict[str, tuple]:
    return {f"cfg:{k}": _cfg(k, label, on, off) for k, label, on, off in _AUTO}


def _all() -> dict[str, tuple]:
    out = dict(_SEASON)
    out.update(_auto())
    for k, label in god_core.shown_features().items():
        out[f"god:{k}"] = _god(k, label)
    return out


def get_switch(key: str) -> bool | None:
    s = _all().get(key)
    return None if s is None else bool(s[3]())


def set_switch(key: str, on: bool) -> bool:
    """改一个开关；key 不认识或者现在不列出来（扩展没开）返回 False。"""
    s = _all().get(key)
    if s is None or not s[6]():
        return False
    s[4](on)
    return True


def label(key: str) -> str:
    s = _all().get(key)
    return s[0] if s else key


def _value(key: str, label: str, value: str, desc: str, url_for) -> dict:
    """不是开关的一行：显示现在填的是什么，点「去改」跳到那一项。"""
    ep, kw = _page(key)
    return {"key": key, "label": label, "value": value, "desc": desc, "note": "", "page": url_for(ep, **kw)}


def _auto_values(url_for) -> list[dict]:
    out = []
    n = max(0, get_int("announce_frequency", 2))
    own = sum(1 for k in ("phone", "date", "official", "wish", "sms", "gift", "lovemail", "relation")
              if get_config(f"announce_frequency_{k}", "").strip())
    out.append(_value("announce_frequency", "「第 N 次记录」公告", f"每 {n} 次" if n else "不发",
                      "电话、约会、短信、礼物等各自计数，每满这么多次往公告群发一句「第 N 次××记录」"
                      + (f"；有 {own} 类单独设了频率" if own else ""), url_for))
    if _shown("daily_tip"):
        tip = get_config("daily_tip", "").strip()
        out.append(_value("daily_tip", "每日指令提示", "不发" if not tip or tip == "关闭" else "发",
                          f"心动信派送后发到公告群：{tip[:30]}{'…' if len(tip) > 30 else ''}" if tip and tip != "关闭" else "心动信派送后发到公告群，现在没填", url_for))
    return out


# 开放时段：功能名 → 另外要开着的扩展（论坛的时段放在「约会房间」组里，但论坛自己是个扩展）
_WINDOW_EXT = {"forum": "forum"}


def _window_rows(url_for) -> list[dict]:
    out = []
    for feat, (key, name) in windows.FEATURES.items():
        if not _shown(key, _WINDOW_EXT.get(feat, "")):
            continue
        rng = windows.parse(get_config(key, ""))
        if not rng:
            value, desc = "全天", "不限时段，什么时候都能用"
        else:
            ok, _ = windows.is_open(feat)
            span = f"{windows._fmt(rng[0])}–{windows._fmt(rng[1])}"
            value, desc = span, f"只在 {span} 开放，现在{'开着' if ok else '关着：玩家这会儿用不了'}"
        out.append(_value(key, name, value, desc, url_for))
    return out


# 功能卡片的总开关：改了要重启才生效，所以总览上只显示开没开，点了去「功能与设置」
_MASTER = [("relations", "关系线"), ("dice", "跑团骰子")]


def _master_rows(url_for) -> list[dict]:
    known = {e["id"]: e for e in extensions.OFFICIAL}
    out = []
    for ext, name in _MASTER:
        if ext not in known:
            continue
        on = extensions.is_enabled(ext)
        tab = "custom" if known[ext].get("personal") else "aux"
        out.append({"key": f"ext:{ext}", "label": name, "value": "开着" if on else "关着",
                    "desc": "整个功能（群指令和后台页面）的总开关；在「功能与设置」的卡片上改，改完要重启才生效", "note": "",
                    "page": url_for("extensions_page", tab=tab)})
    return out


def groups(url_for) -> list[tuple[str, list[dict], bool]]:
    """总览上要列的，每组 (标题, 行, 默认收起)：一季进度、全体功能（上帝模式）、群里会自动发什么、开放时段、功能总开关。
    开关行带 on；不是开关的行带 value（现在的值），只能点进去改。"""
    def row(key, s):
        on = bool(s[3]())
        ep, kw = s[5]
        d = s[1] if on else s[2]
        return {"key": key, "label": s[0], "on": on, "desc": d() if callable(d) else d,
                "note": s[7]() if s[7] else "", "page": url_for(ep, **kw)}
    season = [row(k, s) for k, s in _SEASON.items() if s[6]()]
    god = [row(f"god:{k}", _god(k, label)) for k, label in god_core.shown_features().items()]
    auto = [row(k, s) for k, s in _auto().items() if s[6]()] + _auto_values(url_for)
    out = [("一季进度", season, False), ("全体功能（上帝模式，对所有人生效）", god, False),
           ("群里会自动发什么", auto, True), ("开放时段（不在时段里玩家就用不了）", _window_rows(url_for), True),
           ("功能总开关", _master_rows(url_for), True)]
    return [g for g in out if g[1]]
