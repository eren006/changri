"""跑团模块：群模式、玩家、角色卡、属性与变动历史（不依赖 QQ / NoneBot，可单独测试）。

和恋综的 roles / role_attrs 完全分开存。角色卡属于「玩家」，所有群通用；每个群只记「当前用哪张卡」。
设计见 docs/跑团模块设计.md。
"""
from __future__ import annotations

import re
import time

from . import dice
from .db import get_config, get_conn, set_config

MODES = {"romance": "恋综", "trpg": "跑团"}
MODE_ALIASES = {"恋综": "romance", "romance": "romance", "跑团": "trpg", "trpg": "trpg", "coc": "trpg"}
MAX_CARDS = 20
MAX_ATTRS = 80
MAX_NAME = 12
MAX_VALUE = 999
SOURCE_ST = "st"

# 常用属性的别名组：第一个是存的名字，其余只在查找时当同一个
ALIAS_GROUPS = [
    ("力量", "STR"), ("敏捷", "DEX"), ("体质", "CON"), ("体型", "SIZ"), ("外貌", "APP"),
    ("智力", "INT", "灵感"), ("意志", "POW"), ("教育", "EDU"), ("幸运", "LUCK", "运气"),
    ("理智", "SAN"), ("生命", "HP", "体力"), ("魔法", "MP"), ("移动", "MOV"),
    ("感知", "WIS"), ("魅力", "CHA"),                      # DND 的另两项属性
    ("意志力", "WP"), ("崩溃点", "BP"),                     # 绿色三角洲
]
RULES = ("coc", "dnd", "dg")
_ALIAS = {n.upper(): g[0] for g in ALIAS_GROUPS for n in g}
_GROUP_OF = {g[0]: g for g in ALIAS_GROUPS}

_NAME = r"[A-Za-z一-鿿_]{1,%d}?" % MAX_NAME
_FULL = str.maketrans("０１２３４５６７８９：＝＋－", "0123456789:=+-")
# 名字 + 可选符号 + 数值 / 增减（+5、-3、+1d6）
_ITEM = re.compile(r"(%s)\s*[:：=＝]?\s*([+-]\s*\d+(?:d\d+)?|\d{1,3})(?![\dd])" % _NAME)


def canon(name: str) -> str:
    n = name.strip()
    return _ALIAS.get(n.upper(), n.upper() if n.isascii() else n)


def alias_names(name: str) -> list[str]:
    """查属性时依次试的名字：存的名字在前。"""
    c = canon(name)
    return list(dict.fromkeys([c, *_GROUP_OF.get(c, ())]))


# ======================== 模式 ========================

def get_mode(group_openid: str) -> str:
    m = get_config(f"play_mode:{group_openid}", "") or get_config("play_mode_default", "romance")
    return m if m in MODES else "romance"


def set_mode(group_openid: str, mode: str) -> None:
    set_config(f"play_mode:{group_openid}", mode)


# ======================== 玩家与角色卡 ========================

def player_for(member_openid: str, group_openid: str = "", create: bool = True) -> int | None:
    with get_conn() as c:
        row = c.execute("SELECT player_id FROM trpg_identities WHERE member_openid=?", (member_openid,)).fetchone()
        if row:
            return row["player_id"]
        if not create:
            return None
        now = int(time.time())
        pid = c.execute("INSERT INTO trpg_players(name,created_ts) VALUES ('',?)", (now,)).lastrowid
        c.execute("INSERT INTO trpg_identities(member_openid,player_id,group_openid,created_ts) VALUES (?,?,?,?)",
                  (member_openid, pid, group_openid, now))
        return pid


def set_player_name(player_id: int, name: str) -> tuple[bool, str]:
    name = name.strip()
    if not name or len(name) > 16:
        return False, "名字要 1~16 个字。"
    with get_conn() as c:
        c.execute("UPDATE trpg_players SET name=? WHERE id=?", (name, player_id))
    return True, ""


def bind_qq(player_id: int, qq: str) -> str:
    """把一个 QQ 号认到这个玩家名下（QQ 号监听听到的发言按它对上人）。返回空串表示成功。"""
    import re
    qq = qq.strip()
    if not re.fullmatch(r"\d{5,12}", qq):
        return "QQ 号是 5 到 12 位数字"
    key, now = "qq:" + qq, int(time.time())
    with get_conn() as c:
        row = c.execute("SELECT player_id FROM trpg_identities WHERE member_openid=?", (key,)).fetchone()
        if row:
            c.execute("UPDATE trpg_identities SET player_id=? WHERE member_openid=?", (player_id, key))
        else:
            c.execute("INSERT INTO trpg_identities(member_openid,player_id,group_openid,created_ts) VALUES (?,?,?,?)", (key, player_id, "", now))
    return ""


def player_name(player_id: int) -> str:
    with get_conn() as c:
        row = c.execute("SELECT name FROM trpg_players WHERE id=?", (player_id,)).fetchone()
    return (row["name"] if row else "") or f"玩家{player_id}"


def list_cards(player_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM trpg_cards WHERE player_id=? ORDER BY id", (player_id,))]


def get_card(card_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM trpg_cards WHERE id=?", (card_id,)).fetchone()
    return dict(row) if row else None


def find_card(player_id: int, name: str) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM trpg_cards WHERE player_id=? AND name=?", (player_id, name.strip())).fetchone()
    return dict(row) if row else None


def new_card(player_id: int, name: str, rule: str = "coc") -> tuple[dict | None, str]:
    name = name.strip()
    if not name or len(name) > MAX_NAME or re.search(r"\s", name):
        return None, f"卡名要 1~{MAX_NAME} 个字，不能有空格。"
    if len(list_cards(player_id)) >= MAX_CARDS:
        return None, f"一个人最多 {MAX_CARDS} 张卡。"
    if find_card(player_id, name):
        return None, f"已经有一张叫「{name}」的卡了。"
    with get_conn() as c:
        cid = c.execute("INSERT INTO trpg_cards(player_id,name,rule,created_ts) VALUES (?,?,?,?)",
                        (player_id, name, rule if rule in RULES else "coc", int(time.time()))).lastrowid
    return get_card(cid), ""


def delete_card(card_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM trpg_cards WHERE id=?", (card_id,))


def set_active(group_openid: str, player_id: int, card_id: int) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO trpg_active(group_openid,player_id,card_id) VALUES (?,?,?) "
                  "ON CONFLICT(group_openid,player_id) DO UPDATE SET card_id=excluded.card_id", (group_openid, player_id, card_id))


def active_card(group_openid: str, player_id: int, create: bool = False, rule: str = "coc") -> dict | None:
    """这个群里这个人当前的卡：先看本群选的，没选就沿用最近建的那张；create=True 且一张都没有时自动建一张。"""
    with get_conn() as c:
        row = c.execute("SELECT k.* FROM trpg_active a JOIN trpg_cards k ON k.id=a.card_id WHERE a.group_openid=? AND a.player_id=?",
                        (group_openid, player_id)).fetchone()
        if row:
            return dict(row)
        row = c.execute("SELECT * FROM trpg_cards WHERE player_id=? ORDER BY id DESC LIMIT 1", (player_id,)).fetchone()
    if row:
        return dict(row)
    if not create:
        return None
    card, _ = new_card(player_id, "角色1", rule)
    if card:
        set_active(group_openid, player_id, card["id"])
    return card


# ======================== 属性 ========================

def list_attrs(card_id: int) -> list[tuple[str, int]]:
    with get_conn() as c:
        return [(r["name"], r["value"]) for r in c.execute("SELECT name,value FROM trpg_card_attrs WHERE card_id=? ORDER BY rowid", (card_id,))]


def get_attr(card_id: int, name: str) -> int | None:
    with get_conn() as c:
        for n in alias_names(name):
            row = c.execute("SELECT value FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, n)).fetchone()
            if row:
                return row["value"]
    return None


def _write(c, card_id: int, name: str, value: int | None, source: str, log_id: int | None = None) -> None:
    row = c.execute("SELECT value FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, name)).fetchone()
    old = row["value"] if row else None
    if old == value:
        return
    if value is None:
        c.execute("DELETE FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, name))
    else:
        c.execute("INSERT INTO trpg_card_attrs(card_id,name,value) VALUES (?,?,?) "
                  "ON CONFLICT(card_id,name) DO UPDATE SET value=excluded.value", (card_id, name, value))
    c.execute("INSERT INTO trpg_attr_history(card_id,log_id,ts,name,old_value,new_value,source) VALUES (?,?,?,?,?,?,?)",
              (card_id, log_id, int(time.time()), name, old, value, source))


def parse_st(text: str) -> list[tuple[str, str]]:
    """「力量60 敏捷+5 SAN-1d6」→ [("力量","60"),("敏捷","+5"),("SAN","-1d6")]；同名取最后一个。"""
    out: dict[str, str] = {}
    for m in _ITEM.finditer(text.translate(_FULL)):
        out[canon(m.group(1))] = re.sub(r"\s", "", m.group(2))
    return list(out.items())


def apply_st(card_id: int, items: list[tuple[str, str]], source: str = SOURCE_ST, log_id: int | None = None) -> tuple[bool, list[str]]:
    """应用一组 (名字, 数值或增减)。返回 (是否成功, 每项的说明；失败时只有一条错误)。"""
    lines: list[str] = []
    with get_conn() as c:
        have = {r["name"] for r in c.execute("SELECT name FROM trpg_card_attrs WHERE card_id=?", (card_id,))}
        if len(have | {n for n, _ in items}) > MAX_ATTRS:
            return False, [f"属性最多 {MAX_ATTRS} 项。"]
        rule = _rule_of(c, card_id)
        capped = CAPPED_BY_RULE.get(rule, ())
        items = sorted(items, key=lambda it: it[0] in capped)      # 有上限的放最后，同一条里先录的属性能用上
        for name, raw in items:
            old = c.execute("SELECT value FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, name)).fetchone()
            old_v = old["value"] if old else None
            if old_v is None and raw[0] in "+-":                    # 没录过的技能按初始值增减
                old_v = _default(_attr_map(c, card_id), name, rule)
            if raw[0] in "+-":
                if old_v is None:
                    return False, [f"「{name}」还没有数值，不能增减。先 /st {name}60 记一个。"]
                body = raw[1:]
                if "d" in body:
                    node = dice.parse_expr(body)
                    if not node or not dice.terms_valid(node):
                        return False, [f"「{raw}」看不懂。"]
                    proc, delta = dice.roll_terms(node)
                    desc = f"{raw[0]}{proc}"
                else:
                    delta, desc = int(body), raw
                new_v = old_v + (delta if raw[0] == "+" else -delta)
                head = f"{name}：{old_v} {desc} → "
            else:
                new_v = int(raw)
                head = f"{name}：{'' if old_v is None else f'{old_v} → '}"
            new_v = max(0, min(MAX_VALUE, new_v))
            cap = _derived(_attr_map(c, card_id), rule).get(name) if name in capped else None
            if cap is not None and new_v > cap:
                new_v = cap
                lines.append(f"{head}{new_v}（上限 {cap}）")
            else:
                lines.append(f"{head}{new_v}")
            _write(c, card_id, name, new_v, source, log_id)
    return True, lines


def delete_attr(card_id: int, name: str, source: str = SOURCE_ST) -> bool:
    with get_conn() as c:
        for n in alias_names(name):
            if c.execute("SELECT 1 FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, n)).fetchone():
                _write(c, card_id, n, None, source)
                return True
    return False


def clear_attrs(card_id: int, source: str = SOURCE_ST) -> int:
    n = 0
    with get_conn() as c:
        for r in c.execute("SELECT name FROM trpg_card_attrs WHERE card_id=?", (card_id,)).fetchall():
            _write(c, card_id, r["name"], None, source)
            n += 1
    return n


# ======================== COC 技能初始值、派生值、上限 ========================
# 没录过的技能按 COC7 规则书的初始值算（检定时注明「初始值」），录过就以录的为准。

COC_SKILL_DEFAULTS = {
    "会计": 5, "人类学": 1, "估价": 5, "考古学": 1, "魅惑": 15, "取悦": 15, "攀爬": 20, "计算机使用": 5, "计算机": 5,
    "信用评级": 0, "信用": 0, "克苏鲁神话": 0, "乔装": 5, "汽车驾驶": 20, "驾驶": 1, "电气维修": 10, "电子学": 1,
    "话术": 5, "格斗": 25, "斗殴": 25, "射击": 20, "手枪": 20, "步枪": 25, "霰弹枪": 25, "步霰": 25, "急救": 30,
    "历史": 5, "恐吓": 15, "跳跃": 20, "外语": 1, "法律": 5, "图书馆使用": 20, "图书馆": 20, "聆听": 20, "锁匠": 1,
    "机械维修": 10, "医学": 1, "博物学": 10, "自然学": 10, "领航": 10, "导航": 10, "神秘学": 5, "操作重型机械": 1,
    "说服": 10, "精神分析": 1, "心理学": 10, "骑术": 5, "妙手": 10, "侦查": 25, "潜行": 20, "生存": 10, "游泳": 20,
    "投掷": 20, "追踪": 10, "驯兽": 5, "动物驯养": 5, "潜水": 1, "爆破": 1, "读唇": 1, "催眠": 1, "炮术": 1,
    "艺术与手艺": 5, "技艺": 5, "科学": 1,
}
# 绿色三角洲（Delta Green）技能初始值，同样是自己整理的表
DG_SKILL_DEFAULTS = {
    "会计": 10, "警觉": 20, "人类学": 0, "考古学": 0, "艺术": 0, "炮术": 0, "运动": 30, "官僚": 10, "计算机科学": 0,
    "手艺": 0, "犯罪学": 10, "爆破": 0, "伪装": 10, "闪避": 30, "驾驶": 20, "枪械": 20, "急救": 10, "法证": 0,
    "重型机械": 10, "重武器": 0, "历史": 10, "人际情报": 10, "HUMINT": 10, "法律": 0, "医学": 0, "近战武器": 30,
    "军事科学": 0, "导航": 10, "神秘学": 10, "说服": 20, "药学": 0, "飞行驾驶": 0, "操纵": 0, "心理治疗": 10,
    "骑术": 10, "科学": 0, "搜索": 20, "信号情报": 0, "SIGINT": 0, "潜行": 10, "外科": 0, "生存": 10, "游泳": 20,
    "徒手格斗": 40, "格斗": 40, "非自然": 0, "外语": 0,
}
SKILL_DEFAULTS = {"coc": COC_SKILL_DEFAULTS, "dg": DG_SKILL_DEFAULTS}
# 有上限的几项：录入、增减、理智检定写回时都按上限夹住
CAPPED_BY_RULE = {"coc": ("生命", "魔法", "理智"), "dg": ("生命", "意志力", "理智")}
NO_GROW = {"克苏鲁神话", "非自然"}                    # 这两项不能靠检定成长


def _attr_map(c, card_id: int) -> dict[str, int]:
    return {r["name"]: r["value"] for r in c.execute("SELECT name,value FROM trpg_card_attrs WHERE card_id=?", (card_id,))}


def _rule_of(c, card_id: int) -> str:
    row = c.execute("SELECT rule FROM trpg_cards WHERE id=?", (card_id,)).fetchone()
    return row["rule"] if row else ""


def _derived(a: dict[str, int], rule: str = "coc") -> dict:
    """由属性算出来的上限等（缺哪项属性就不算哪项）。COC：生命/魔法/理智上限、DB、体格；绿色三角洲：生命/意志力/理智上限。"""
    d = {}
    if rule == "dg":
        if "力量" in a and "体质" in a:
            d["生命"] = dice.dg_hp_max(a["力量"], a["体质"])
        if "意志" in a:
            d["意志力"] = a["意志"]
        d["理智"] = max(0, 99 - a.get("非自然", 0))
        return d
    if "体质" in a and "体型" in a:
        d["生命"] = dice.coc_hp_max(a["体质"], a["体型"])
    if "意志" in a:
        d["魔法"] = a["意志"] // 5
    d["理智"] = max(0, 99 - a.get("克苏鲁神话", 0))
    if "力量" in a and "体型" in a:
        d["DB"], d["体格"] = dice.coc_db_build(a["力量"], a["体型"])
    return d


def derived(card_id: int) -> dict:
    with get_conn() as c:
        rule = _rule_of(c, card_id)
        return _derived(_attr_map(c, card_id), rule) if rule in CAPPED_BY_RULE else {}


def _default(a: dict[str, int], name: str, rule: str) -> int | None:
    """没录过的属性/技能按规则算的初始值；不知道就 None。"""
    n = canon(name)
    if rule == "dg":
        if n == "理智":
            return a["意志"] * 5 if "意志" in a else None
        if n == "崩溃点":
            return a["意志"] * 4 if "意志" in a else None        # 初始理智 - 意志
        if n in ("生命", "意志力"):
            return _derived(a, "dg").get(n)
        return DG_SKILL_DEFAULTS.get(n)
    if rule != "coc":
        return None
    if n == "闪避":
        return a["敏捷"] // 2 if "敏捷" in a else None
    if n in ("母语", "语言"):
        return a.get("教育")
    if n == "理智":
        return a.get("意志")                            # 初始理智 = 意志
    if n in ("生命", "魔法"):
        return _derived(a).get(n)
    return COC_SKILL_DEFAULTS.get(n)


def _coc_default(a: dict[str, int], name: str) -> int | None:
    return _default(a, name, "coc")


def skill_value(card_id: int, name: str) -> tuple[int, bool] | None:
    """(数值, 是不是初始值)。卡上录了就用录的；COC / 绿色三角洲卡没录就按初始值；都没有返回 None。"""
    v = get_attr(card_id, name)
    if v is not None:
        return v, False
    with get_conn() as c:
        d = _default(_attr_map(c, card_id), name, _rule_of(c, card_id))
    return (d, True) if d is not None else None


# ======================== 成长标记（COC）========================
# 技能检定成功就给这项技能打个标记，团后 /en 不带参数一次把标了的都掷完。
# 属性（力量……）、幸运、理智、生命、魔法、移动不是技能，按规则书不能成长，不标。

NOT_SKILLS = {g[0] for g in ALIAS_GROUPS}


def can_grow(name: str) -> bool:
    return canon(name) not in NOT_SKILLS | NO_GROW


def mark_growth(card_id: int, name: str) -> bool:
    """给卡上的技能（没录过但有 COC 初始值的也算）打成长标记。新打上返回 True；已经标过、不是技能、没有这项都返回 False。"""
    if not can_grow(name) or skill_value(card_id, name) is None:
        return False
    with get_conn() as c:
        n = next((n for n in alias_names(name)
                  if c.execute("SELECT 1 FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, n)).fetchone()), canon(name))
        return c.execute("INSERT OR IGNORE INTO trpg_growth_marks(card_id,name,ts) VALUES (?,?,?)",
                         (card_id, n, int(time.time()))).rowcount > 0


def unmark_growth(card_id: int, name: str) -> bool:
    with get_conn() as c:
        return c.execute(f"DELETE FROM trpg_growth_marks WHERE card_id=? AND name IN ({','.join('?' * len(alias_names(name)))})",
                         (card_id, *alias_names(name))).rowcount > 0


def clear_growth(card_id: int) -> int:
    with get_conn() as c:
        return c.execute("DELETE FROM trpg_growth_marks WHERE card_id=?", (card_id,)).rowcount


def growth_marks(card_id: int) -> list[str]:
    """标了成长的技能（卡上已经删掉、又没有初始值的不算），按标记先后。"""
    with get_conn() as c:
        names = [r["name"] for r in c.execute("SELECT name FROM trpg_growth_marks WHERE card_id=? ORDER BY ts, rowid", (card_id,))]
    return [n for n in names if skill_value(card_id, n) is not None]


def format_card(card: dict, with_derived: bool = True) -> str:
    items = list_attrs(card["id"])
    if not items:
        return f"「{card['name']}」还没有属性。用 /st 力量60 敏捷70 记一下。"
    rule = card["rule"]

    def note(n: str, v: int) -> str:
        if not with_derived:
            return ""
        if rule == "coc" and v > 9 and n not in ("HP", "生命", "MP", "魔法", "SAN", "理智", "移动"):
            return f"　（半 {v // 2} / 五分之一 {v // 5}）"
        if rule == "dg" and n in dice.DG_STATS:
            return f"　（×5 = {v * 5}）"
        return ""
    body = f"「{card['name']}」（{dice.RULE_TITLE.get(rule, rule)}）\n" + "\n".join(f"{n}　{v}{note(n, v)}" for n, v in items)
    d = derived(card["id"]) if with_derived else {}
    extra = [f"{k}上限 {d[k]}" for k in CAPPED_BY_RULE.get(rule, ()) if k in d] + [f"{k} {d[k]}" for k in ("DB", "体格") if k in d]
    if with_derived and rule == "dg" and get_attr(card["id"], "崩溃点") is None:
        bp = skill_value(card["id"], "崩溃点")
        if bp:
            extra.append(f"崩溃点 {bp[0]}（按 意志×4 算）")
    return body + ("\n" + "　".join(extra) if extra else "")


# ======================== 队伍（/team）========================

MAX_TEAMS = 20
MAX_TEAM_MEMBERS = 20


def list_teams(group_openid: str) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM trpg_team_members WHERE team_id=t.id) AS n FROM trpg_teams t "
            "WHERE group_openid=? ORDER BY id", (group_openid,))]


def find_team(group_openid: str, name: str) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM trpg_teams WHERE group_openid=? AND name=?", (group_openid, name.strip())).fetchone()
    return dict(row) if row else None


def team_add(group_openid: str, name: str, player_ids: list[int]) -> tuple[dict | None, str]:
    """把人加进队伍，没有这个队伍就建。返回 (队伍, 错误)。"""
    name = name.strip()
    if not name or len(name) > MAX_NAME or re.search(r"\s", name):
        return None, f"队伍名要 1~{MAX_NAME} 个字，不能有空格。"
    team = find_team(group_openid, name)
    with get_conn() as c:
        if not team:
            if len(list_teams(group_openid)) >= MAX_TEAMS:
                return None, f"一个群最多 {MAX_TEAMS} 个队伍。"
            c.execute("INSERT INTO trpg_teams(group_openid,name,created_ts) VALUES (?,?,?)", (group_openid, name, int(time.time())))
            team = dict(c.execute("SELECT * FROM trpg_teams WHERE group_openid=? AND name=?", (group_openid, name)).fetchone())
        have = {r["player_id"] for r in c.execute("SELECT player_id FROM trpg_team_members WHERE team_id=?", (team["id"],))}
        if len(have | set(player_ids)) > MAX_TEAM_MEMBERS:
            return None, f"一个队伍最多 {MAX_TEAM_MEMBERS} 人。"
        for pid in player_ids:
            c.execute("INSERT OR IGNORE INTO trpg_team_members(team_id,player_id,added_ts) VALUES (?,?,?)", (team["id"], pid, int(time.time())))
    return team, ""


def team_remove(team_id: int, player_ids: list[int]) -> int:
    with get_conn() as c:
        return sum(c.execute("DELETE FROM trpg_team_members WHERE team_id=? AND player_id=?", (team_id, pid)).rowcount for pid in player_ids)


def team_delete(team_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM trpg_team_members WHERE team_id=?", (team_id,))
        c.execute("DELETE FROM trpg_teams WHERE id=?", (team_id,))


def team_members(team_id: int) -> list[int]:
    with get_conn() as c:
        return [r["player_id"] for r in c.execute("SELECT player_id FROM trpg_team_members WHERE team_id=? ORDER BY added_ts, rowid", (team_id,))]


# ======================== 后台用 ========================

def players_overview() -> list[dict]:
    """每个玩家一行，带名下所有卡（含属性数和变动数）。"""
    with get_conn() as c:
        players = [dict(r) for r in c.execute("SELECT * FROM trpg_players ORDER BY id")]
        for p in players:
            p["display"] = p["name"] or f"玩家{p['id']}"
            p["openids"] = [dict(r) for r in c.execute("SELECT member_openid,group_openid FROM trpg_identities WHERE player_id=?", (p["id"],))]
            p["cards"] = [dict(r) for r in c.execute(
                "SELECT k.*, (SELECT COUNT(*) FROM trpg_card_attrs WHERE card_id=k.id) AS n_attrs, "
                "(SELECT COUNT(*) FROM trpg_attr_history WHERE card_id=k.id) AS n_changes, "
                "(SELECT MAX(ts) FROM trpg_attr_history WHERE card_id=k.id) AS last_ts FROM trpg_cards k WHERE player_id=? ORDER BY id", (p["id"],))]
    return players


def card_history(card_id: int, log_id: int | None = None, limit: int = 300) -> list[dict]:
    with get_conn() as c:
        if log_id is None:
            rows = c.execute("SELECT * FROM trpg_attr_history WHERE card_id=? ORDER BY id DESC LIMIT ?", (card_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM trpg_attr_history WHERE card_id=? AND log_id=? ORDER BY id DESC LIMIT ?", (card_id, log_id, limit)).fetchall()
    return [dict(r) for r in rows]


def card_owner(card_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT p.* FROM trpg_players p JOIN trpg_cards k ON k.player_id=p.id WHERE k.id=?", (card_id,)).fetchone()
    return dict(row) if row else None
