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
]
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
                        (player_id, name, rule if rule in ("coc", "dnd") else "coc", int(time.time()))).lastrowid
    return get_card(cid), ""


def delete_card(card_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM trpg_cards WHERE id=?", (card_id,))


def set_active(group_openid: str, player_id: int, card_id: int) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO trpg_active(group_openid,player_id,card_id) VALUES (?,?,?) "
                  "ON CONFLICT(group_openid,player_id) DO UPDATE SET card_id=excluded.card_id", (group_openid, player_id, card_id))


def active_card(group_openid: str, player_id: int, create: bool = False) -> dict | None:
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
    card, _ = new_card(player_id, "角色1")
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
        for name, raw in items:
            old = c.execute("SELECT value FROM trpg_card_attrs WHERE card_id=? AND name=?", (card_id, name)).fetchone()
            old_v = old["value"] if old else None
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
                new_v = max(0, min(MAX_VALUE, new_v))
                lines.append(f"{name}：{old_v} {desc} → {new_v}")
            else:
                new_v = max(0, min(MAX_VALUE, int(raw)))
                lines.append(f"{name}：{'' if old_v is None else f'{old_v} → '}{new_v}")
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


def format_card(card: dict, with_derived: bool = True) -> str:
    items = list_attrs(card["id"])
    if not items:
        return f"「{card['name']}」还没有属性。用 /st 力量60 敏捷70 记一下。"
    return f"「{card['name']}」（{dice.RULE_TITLE.get(card['rule'], card['rule'])}）\n" + "\n".join(
        f"{n}　{v}" + (f"　（半 {v // 2} / 五分之一 {v // 5}）" if with_derived and card["rule"] == "coc" and v > 9 and n not in ("HP", "生命", "MP", "魔法", "SAN", "理智", "移动") else "")
        for n, v in items)


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
