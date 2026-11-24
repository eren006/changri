"""「约会」「电话」后面什么都不写：出一张选人卡片。时间玩家自己填（作者定，2026-11-23）。

· 电话、以及没有预设地点的约会：点名字把「电话 时间 张三」「约会 时间 地点 张三」填进输入框，把占位字换掉再发。
· 有预设地点的约会（「地点」扩展开着、后台录了地点）分两步：点名字直接发「约会 张三」，机器人回一张地点卡片，
  点地点填「约会 时间 咖啡厅 张三」，只剩时间要写。上了锁、自己没钥匙的地点不列；没开「只能使用预设地点」时多一个「别的地方」。
· 占位字忘了换会被 rooms.create 拦下来提醒。
· 常联系的人在前：有关系线的、跟自己互发过短信礼物的；然后是其他嘉宾，NPC 在最后。
· 一条消息最多 25 个按钮，留两个给底下的按钮，放不下的在正文里说。
"""
from __future__ import annotations

from .db import get_config, get_conn

PLACE_HOLDER = "地点"
TIME_HOLDER = "时间"
MAX_ITEMS = 23            # 25 个按钮 - 底下两个


def _contacts(me_id: int) -> set[int]:
    with get_conn() as c:
        rows = c.execute("SELECT role_lo AS a, role_hi AS b FROM relations WHERE role_lo=? OR role_hi=? "
                         "UNION SELECT from_role_id AS a, to_role_id AS b FROM messages WHERE from_role_id=? OR to_role_id=?",
                         (me_id, me_id, me_id, me_id)).fetchall()
    return {x for r in rows for x in (r["a"], r["b"]) if x != me_id}


def places_for(role_id: int) -> list[str]:
    """这个人能约的预设地点：「地点」扩展开着才算；上锁的要有钥匙。没有就是空列表（约会就一步填好）。"""
    from . import extensions, rpg
    if not extensions.is_enabled("places"):
        return []
    have = {p["id"] for p in rpg.keys(role_id)}
    return [p["name"] for p in rpg.places() if not p["locked"] or p["id"] in have]


def only_preset() -> bool:
    """后台开了「只能使用预设地点约会」：地点卡片上不给「别的地方」。"""
    return get_config("place_system_enabled", "0") == "1"


def fill_for(kind: str, name: str, place: str = PLACE_HOLDER) -> str:
    return f"约会 {TIME_HOLDER} {place} {name}" if kind == "约会" else f"电话 {TIME_HOLDER} {name}"


def people(me_id: int, limit: int = MAX_ITEMS) -> tuple[list[dict], int]:
    """选人按钮的顺序：玩家在前、NPC 在后；各自里面常联系的（有关系线、互发过短信礼物）在前。不含自己。返回 (放得下的, 没放下的个数)。"""
    from . import charprofile
    contacts = _contacts(me_id)
    rows = [(bool(r["is_npc"]), r["id"] not in contacts, i, r) for i, r in enumerate(charprofile._all()) if r["id"] != me_id]   # NPC 一律在玩家后面（作者定，2026-11-23）
    rows.sort(key=lambda x: x[:3])
    return [x[3] for x in rows[:limit]], max(0, len(rows) - limit)


def form_card(kind: str, me_id: int) -> tuple[str, list[tuple[str, str, bool]]]:
    """只发「短信」「送礼」「心动信」：选人卡片，点名字把竖着写的格式填进输入框，对象已经写好（formats.form_to）。"""
    from . import charprofile, formats
    shown, more = people(me_id)
    btns = [(charprofile.btn_name(r["name"], r["short"]), formats.form_to(kind, r["name"]), False) for r in shown]
    rest = {"短信": "写上内容再发", "送礼": "写上礼物（留言选填）再发", "心动信": "写上内容再发；署名不写就是匿名"}[kind]
    lines = [f"{kind} · 选一个人", f"点名字会把格式填进输入框，对象已经写好，{rest}"]
    if more:
        lines.append(f"还有 {more} 位没放下：点「自己写」，对象那里填名字")
    btns += [("自己写", formats.fill(kind), False), ("帮助", "帮助", True)]
    return "\n".join(lines), btns


def cmd_card(title: str, hint: str, me_id: int, prefix: str) -> tuple[str, list[tuple[str, str, bool]]]:
    """通用选人卡片：点名字把「prefix 名字 」填进输入框（拉线这类）。"""
    from . import charprofile
    shown, more = people(me_id)
    btns = [(charprofile.btn_name(r["name"], r["short"]), f"{prefix} {r['name']} ", False) for r in shown]
    lines = [title, hint] + ([f"还有 {more} 位没放下：点「自己写」，后面接名字"] if more else [])
    btns += [("自己写", f"{prefix} ", False), ("帮助", "帮助", True)]
    return "\n".join(lines), btns


def card(kind: str, me_id: int) -> tuple[str, list[tuple[str, str, bool]]]:
    """选人卡片：(正文, [(按钮上的字, 指令, 点了是不是直接发出)])。"""
    from . import charprofile, formats, storycal
    two_step = kind == "约会" and bool(places_for(me_id))
    shown, more = people(me_id)
    if two_step:
        btns = [(charprofile.btn_name(r["name"], r["short"]), f"约会 {r['name']}", True) for r in shown]
        lines = [f"{kind} · 选一个人", "点名字，下一步选地点"]
    else:
        btns = [(charprofile.btn_name(r["name"], r["short"]), fill_for(kind, r["name"]), False) for r in shown]
        holders = "「时间」「地点」" if kind == "约会" else "「时间」"
        lines = [f"{kind} · 选一个人", f"点名字会把指令填进输入框，把{holders}换成你要的再发",
                 f"时间这样写：{storycal.example_time()}"]
    lines.append("想约好几个人：名字后面接 /李四")
    if more > 0:
        lines.append(f"还有 {more} 位没放下，直接发「{fill_for(kind, '名字')}」")
    btns += [("自己写", formats.fill(kind), False), ("帮助", "帮助", True)]
    return "\n".join(lines), btns


def place_card(me_id: int, names: str, label: str) -> tuple[str, list[tuple[str, str, bool]]]:
    """约会第二步：和 names（「张三」或「张三/李四」）去哪儿。label 是给人看的对方称呼。"""
    from . import storycal
    ps = places_for(me_id)
    shown, more = ps[:MAX_ITEMS], len(ps) - MAX_ITEMS
    btns = [(p, fill_for("约会", names, p), False) for p in shown]
    lines = [f"约会 · 和 {label} 去哪儿？", "点地点会把指令填进输入框，把「时间」换掉再发",
             f"时间这样写：{storycal.example_time()}"]
    if more > 0:
        lines.append(f"还有 {more} 个地点没放下，发「地点 查看」看全部")
    if not only_preset():
        btns.append(("别的地方", fill_for("约会", names), False))
    btns.append(("换个人", "约会", True))
    return "\n".join(lines), btns
