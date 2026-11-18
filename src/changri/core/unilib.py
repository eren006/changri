"""通用物品库（照长日存档站的「通用物品库」）：把常用的物品、货币存一份模板，换季度、换机器人都还在，要用时再导入到当前机器人的物品库。
模板存在全局库里（所有机器人共用一份）；入库时不带物品码，导入时按当前物品库重新编号，同名的跳过。
系统自带的特殊道具（SPEC_ 开头）和写信币不入库：它们每个机器人都会自动建好。"""
import json
import time

from . import rpg
from .db import get_global_conn

MAX_ITEMS = 500
SKIP_NAMES = {"写信币"}


def _skip(item: dict) -> bool:
    return str(item.get("code", "")).upper().startswith("SPEC_") or item.get("name") in SKIP_NAMES


def items() -> list[dict]:
    with get_global_conn() as c:
        rows = c.execute("SELECT * FROM universal_items ORDER BY kind, id").fetchall()
    out = []
    for r in rows:
        e = json.loads(r["entry_json"] or "{}")
        eff = e.get("effects") or {}
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "description": e.get("description", ""),
                    "max_uses": e.get("max_uses", -1), "can_give": bool(e.get("can_give", True)),
                    "eff_text": " ".join(f"{k}{v:+d}" for k, v in eff.items()), "effects": eff})
    return out


def candidates() -> list[dict]:
    """当前机器人物品库里能存进通用库的（系统自带的不算），带一个「已经在库里」的标记。"""
    have = {x["name"].lower() for x in items()}
    return [{**x, "in_library": x["name"].lower() in have} for x in rpg.items() if not _skip(x)]


def add_from_bot(codes: list[str]) -> tuple[int, int, str]:
    """把当前机器人的这些物品存进通用库。返回 (存了几件, 跳过几件, 提示)。同名的、系统自带的跳过；库满了就停。"""
    mine = {x["code"]: x for x in rpg.items()}
    added = skipped = 0
    with get_global_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM universal_items").fetchone()[0]
        have = {r["name"].lower() for r in c.execute("SELECT name FROM universal_items")}
        for code in codes:
            x = mine.get(code)
            if not x or _skip(x) or x["name"].lower() in have:
                skipped += 1
                continue
            if n >= MAX_ITEMS:
                return added, skipped, f"通用库已满（{MAX_ITEMS} 件），已存入 {added} 件，其余没存。请先删掉一些再继续。"
            try:
                eff = json.loads(x.get("attr_effects") or "{}")
            except ValueError:
                eff = {}
            entry = {"description": x.get("description") or "", "effects": eff, "max_uses": x["max_uses"], "can_give": bool(x["can_give"])}
            c.execute("INSERT INTO universal_items(name, kind, entry_json, created_ts) VALUES(?,?,?,?)",
                      (x["name"], x["kind"], json.dumps(entry, ensure_ascii=False), int(time.time())))
            have.add(x["name"].lower()); n += 1; added += 1
    return added, skipped, ""


def import_to_bot(ids: list[int]) -> tuple[int, list[str]]:
    """把通用库里选中的导入当前机器人的物品库：重新编号，同名的跳过。返回 (导入几件, 没导入的说明)。
    带属性效果、但这个机器人还没定义那个属性的，不导入，并说明缺哪个属性。"""
    lib = {x["id"]: x for x in items()}
    have = {x["name"].lower() for x in rpg.items()}
    added, notes = 0, []
    for i in ids:
        x = lib.get(i)
        if not x:
            continue
        if x["name"].lower() in have:
            notes.append(f"{x['name']}：已经有同名的物品")
            continue
        ok, msg = rpg.save_item("", x["name"], x["kind"], x["description"], x["effects"], x["max_uses"], x["can_give"])
        if ok:
            have.add(x["name"].lower()); added += 1
        else:
            notes.append(f"{x['name']}：{msg}")
    return added, notes


def delete(item_id: int) -> None:
    with get_global_conn() as c:
        c.execute("DELETE FROM universal_items WHERE id=?", (item_id,))
