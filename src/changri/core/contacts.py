"""好友列表：先选人、再选做什么（作者定，2026-11-23）。「玩家名单」并进来。

· 「好友列表」：名单照旧，下面一人一个按钮，点了直接发「好友列表 张三」。
· 「好友列表 张三」：张三的小卡片（档案、你们的关系线、最近一次来往），下面是能对 TA 做的事：
  短信、送礼、心动信、电话、拉线 → 把对象已经写好的格式填进输入框，补完再发；
  约会 → 有预设地点就直接进选地点（datepick.place_card），没有就填进输入框；
  看关系线、看公知、戳一下 → 直接发。功能关着、截止锁上、不是邻座的，不出那个按钮。
· 按钮上的人名照 charprofile.btn_name（简称优先、最多 4 个字）；指令里用全名。
"""
from __future__ import annotations

import time

from .db import get_conn

LIST_MAX = 24             # 25 个按钮 - 「帮助」


def list_card(me_id: int | None) -> tuple[str, list[tuple[str, str, bool]]]:
    """「好友列表」：(名单正文, 按钮)。不知道是谁（没角色）就只有名单、没有按钮。"""
    from . import charprofile, datepick
    text = charprofile.roster()
    if not me_id:
        return text, []
    shown, more = datepick.people(me_id, LIST_MAX)
    btns = [(charprofile.btn_name(r["name"], r["short"]), f"好友列表 {r['name']}", True) for r in shown]
    hint = "👇 点名字，看能和 TA 做什么（发短信、送礼、约会……）"
    if more:
        hint += f"\n还有 {more} 位没放下：发「好友列表 名字」"
    return text + "\n\n" + hint, btns + [("帮助", "帮助", True)]


def _last_contact(me_id: int, other_id: int) -> str:
    with get_conn() as c:
        r = c.execute("SELECT kind, from_role_id, created_ts FROM messages WHERE (from_role_id=? AND to_role_id=?) OR (from_role_id=? AND to_role_id=?) "
                      "ORDER BY id DESC LIMIT 1", (me_id, other_id, other_id, me_id)).fetchone()
    if not r:
        return ""
    what = "短信" if r["kind"] == "sms" else "礼物"
    who = "你给 TA 发了" if r["from_role_id"] == me_id else "TA 给你发了"
    when = time.strftime("%m-%d %H:%M", time.localtime(r["created_ts"]))
    return f"最近：{who}{what}（{when}）"


def person_card(me, target) -> tuple[str, list[tuple[str, str, bool]]]:
    """「好友列表 张三」：(卡片正文, 按钮)。me、target 是角色行。"""
    from . import charprofile, datepick, dinner, formats, relations, sheets
    name = target["name"]
    p = charprofile.get(target["id"])
    head = f"{p['short']}（{name}）" if p["short"] else name
    lines = [f"📇 {head}", f"{p['gender']} · {p['age']} 岁 · 皮相 {p['look']}"]
    if p["bio"]:
        lines.append(f"签名：{p['bio']}")
    rel = relations.get_rel(me["id"], target["id"]) if formats._relations() else None
    if rel:
        it = next((x for x in relations.list_for(me["id"]) if x["rel_id"] == rel["id"]), None)
        if it:
            lines.append(f"关系线：{it['count']} 段" + ("，轮到你回" if it["pending"] else "") + ("（已确认完成）" if it["confirmed"] else ""))
    last = _last_contact(me["id"], target["id"])
    if last:
        lines.append(last)
    lines.append("\n点下面的事，格式会填进输入框，对象已经写好，补完再发")

    btns: list[tuple[str, str, bool]] = [("短信", formats.form_to("短信", name), False), ("送礼", formats.form_to("送礼", name), False)]
    if formats._lovemail():
        btns.append(("心动信", formats.form_to("心动信", name), False))
    if datepick.places_for(me["id"]):
        btns.append(("约会", f"约会 {name}", True))                       # 下一步选地点
    else:
        btns.append(("约会", datepick.fill_for("约会", name), False))
    btns.append(("电话", datepick.fill_for("电话", name), False))
    if formats._relations():
        if rel:
            btns.append(("看关系线", f"查看关系线 {name}", True))
        elif not relations.closed_note():
            btns.append(("拉线", f"拉线 {name} ", False))
        if sheets.public() and sheets.get(target["id"], "gongzhi"):
            btns.append(("看公知", f"查看公知 {name}", True))
    if name in dinner.neighbors(me["name"]):
        btns.append(("戳一下", f"戳你一下 {name}", True))
    btns.append(("换个人", "好友列表", True))
    return "\n".join(lines), btns
