"""物品、背包、商城、属性定义和地点的共用规则。

部分规则改编自「长日将尽」CC BY-NC-SA 协议作品，联系 3052553938。
"""
from __future__ import annotations

import json
import re
import time

from .attrs import norm
from .db import get_config, get_conn

CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
ROOM_RE = re.compile(r"^(.+?)的房间$")


def _rowdict(row):
    return dict(row) if row else None


DEFAULT_ATTRS = [("HP", 0, 100, 100, "生命值")]
DEFAULT_CURRENCY = ("GOLD", "金币")


def seed_defaults(force: bool = False) -> list[str]:
    """恋综里一直有的基础内容：HP（0–100）和金币。只在第一次自动补，之后你改、你删都算数；
    force=True（一键初始化）才会把缺的补回来，已有的不动。返回补了什么。"""
    from .db import get_config, set_config
    from . import specials
    made = [f"道具 {n}" for n in specials.ensure(force)]        # 特殊道具系统自带：老机器升级后也补一次
    if not force and get_config("rpg_seeded", "") == "1":
        return made
    with get_conn() as c:
        have_attr = {r["name"] for r in c.execute("SELECT name FROM rpg_attr_defs")}
        have_item = {r["code"] for r in c.execute("SELECT code FROM rpg_items")}
        have_name = {r["name"] for r in c.execute("SELECT name FROM rpg_items")}
    for name, lo, hi, dv, desc in DEFAULT_ATTRS:
        if name not in have_attr:
            save_attr_def(name, lo, hi, dv, desc)
            made.append(f"属性 {name}")
    code, nm = DEFAULT_CURRENCY
    if code not in have_item and nm not in have_name:
        if save_item(code, nm, "currency", "通用货币", None, -1, True)[0]:
            made.append(f"货币 {nm}")
    set_config("rpg_seeded", "1")
    return made


def attr_defs() -> list[dict]:
    seed_defaults()
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM rpg_attr_defs ORDER BY rowid")]


def save_attr_def(name: str, min_value=None, max_value=None, default_value=0, description="") -> tuple[bool, str]:
    name = norm(name)[:12]
    if not name:
        return False, "属性名不能为空。"
    try:
        lo = None if min_value in (None, "") else int(min_value)
        hi = None if max_value in (None, "") else int(max_value)
        default = int(default_value or 0)
    except (TypeError, ValueError):
        return False, "上下限和默认值要写整数。"
    if lo is not None and hi is not None and lo > hi:
        return False, "下限不能大于上限。"
    default = max(lo, default) if lo is not None else default
    default = min(hi, default) if hi is not None else default
    with get_conn() as c:
        c.execute("INSERT INTO rpg_attr_defs(name,min_value,max_value,default_value,description) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET min_value=excluded.min_value,max_value=excluded.max_value,"
                  "default_value=excluded.default_value,description=excluded.description",
                  (name, lo, hi, default, description.strip()[:200]))
    return True, name


def delete_attr_def(name: str) -> tuple[bool, str]:
    """有物品的效果用到这个属性就不让删：删了以后那些物品照样改这个属性，而且不再受上下限约束。"""
    n = norm(name)
    with get_conn() as c:
        users = [r["name"] for r in c.execute("SELECT name, attr_effects FROM rpg_items") if n in json.loads(r["attr_effects"] or "{}")]
        if users:
            return False, f"还有物品用到「{n}」：{'、'.join(users)}。先把这些物品的效果改掉再删。"
        c.execute("DELETE FROM rpg_attr_defs WHERE name=?", (n,))
    return True, ""


def clamp_attr(name: str, value: int, conn=None) -> int:
    own = conn is None
    c = conn or get_conn()
    try:
        row = c.execute("SELECT min_value,max_value FROM rpg_attr_defs WHERE name=?", (norm(name),)).fetchone()
        if not row:
            return int(value)
        value = int(value)
        if row["min_value"] is not None:
            value = max(value, row["min_value"])
        if row["max_value"] is not None:
            value = min(value, row["max_value"])
        return value
    finally:
        if own:
            c.close()


def next_code(kind: str = "item") -> str:
    """没写物品码时自动生成：普通物品 ITEM_001、货币 CUR_001……取第一个没被占用的编号。"""
    prefix = "CUR" if kind == "currency" else "ITEM"
    with get_conn() as c:
        used = {r["code"] for r in c.execute("SELECT code FROM rpg_items WHERE code LIKE ?", (prefix + "_%",))}
    n = 1
    while f"{prefix}_{n:03d}" in used:
        n += 1
    return f"{prefix}_{n:03d}"


def save_item(code: str, name: str, kind="item", description="", effects=None, max_uses=-1, can_give=True) -> tuple[bool, str]:
    code = code.strip().upper()
    name = name.strip()
    if not code and kind in ("item", "currency"):
        code = next_code(kind)
    if not CODE_RE.fullmatch(code):
        return False, "物品码要用 2–32 位大写字母、数字或下划线，且字母开头。"
    if not name or len(name) > 40:
        return False, "物品名要写 1–40 个字。"
    if kind not in ("item", "currency"):
        return False, "物品类型只能是普通物品或货币。"
    try:
        max_uses = int(max_uses)
    except (TypeError, ValueError):
        return False, "使用次数要写整数，-1 表示不消耗。"
    from .specials import CODES as _SPECIALS
    if code in _SPECIALS:
        max_uses = 1                                   # 特殊道具每件用一次，定死（后台编辑时这一栏是锁着的）
    parsed: dict[str, int] = {}
    if isinstance(effects, str):
        for part in re.split(r"[,，\s]+", effects.strip()):
            if not part:
                continue
            m = re.fullmatch(r"(.{1,12}?)([+-]\d+)", part)
            if not m:
                return False, f"属性效果「{part}」格式不对，写成 体力+2 理智-1。"
            parsed[norm(m[1])] = int(m[2])
    elif effects:
        parsed = {norm(str(k)): int(v) for k, v in dict(effects).items()}
    with get_conn() as c:
        missing = [n for n in parsed if not c.execute("SELECT 1 FROM rpg_attr_defs WHERE name=?", (n,)).fetchone()]
        if missing:
            return False, "请先建属性定义：" + "、".join(missing)
        try:
            c.execute("INSERT INTO rpg_items(code,name,kind,description,attr_effects,max_uses,can_give,created_ts) VALUES(?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(code) DO UPDATE SET name=excluded.name,kind=excluded.kind,description=excluded.description,"
                      "attr_effects=excluded.attr_effects,max_uses=excluded.max_uses,can_give=excluded.can_give",
                      (code, name, kind, description.strip()[:500], json.dumps(parsed, ensure_ascii=False), max_uses, int(bool(can_give)), int(time.time())))
        except Exception as e:  # sqlite unique name
            return False, "物品名或物品码已被其他物品占用。" if "UNIQUE" in str(e) else str(e)
    return True, code


def items() -> list[dict]:
    seed_defaults()
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM rpg_items ORDER BY kind, code")]


def find_item(value: str, conn=None):
    own = conn is None
    if own:
        seed_defaults()
    c = conn or get_conn()
    try:
        row = c.execute("SELECT * FROM rpg_items WHERE code=? COLLATE NOCASE OR name=? COLLATE NOCASE", (value.strip(), value.strip())).fetchone()
        return _rowdict(row)
    finally:
        if own:
            c.close()


def delete_item(code: str) -> tuple[bool, str]:
    with get_conn() as c:
        used = c.execute("SELECT COALESCE(SUM(count),0) FROM rpg_inventory WHERE item_code=?", (code,)).fetchone()[0]
        if used:
            return False, f"还有 {used} 件在玩家背包里，先调整为 0 才能删。"
        c.execute("DELETE FROM rpg_items WHERE code=?", (code,))
    return True, ""


def _log(c, role_id: int | None, action: str, code: str, count: int, detail=""):
    c.execute("INSERT INTO rpg_item_log(role_id,action,item_code,count,detail,created_ts) VALUES(?,?,?,?,?,?)",
              (role_id, action, code, count, detail[:200], int(time.time())))


def _change(c, role_id: int, item: dict, delta: int, action: str, detail="") -> tuple[bool, str]:
    """加：放进「全新的」那一叠（剩余次数 = 物品现在的次数上限）。
    扣：背包里所有没过期的都算（用过几次的、改次数上限以前发的也算），先扣剩余次数最少的，不消耗的（-1）最后扣。
    以前只认「全新的那一叠」：用过一次的药水、改过次数上限的旧存货背包里看得到，却扣不掉、不能当材料、物品也删不掉。"""
    if delta > 0:
        row = c.execute("SELECT * FROM rpg_inventory WHERE role_id=? AND item_code=? AND remaining_uses=? AND expires_ts IS NULL",
                        (role_id, item["code"], item["max_uses"])).fetchone()
        if row:
            c.execute("UPDATE rpg_inventory SET count=count+? WHERE id=?", (delta, row["id"]))
        else:
            c.execute("INSERT INTO rpg_inventory(role_id,item_code,count,remaining_uses) VALUES(?,?,?,?)",
                      (role_id, item["code"], delta, item["max_uses"]))
        _log(c, role_id, action, item["code"], delta, detail)
        return True, ""
    rows = c.execute("SELECT * FROM rpg_inventory WHERE role_id=? AND item_code=? AND (expires_ts IS NULL OR expires_ts>?) "
                     "ORDER BY remaining_uses=-1, remaining_uses, id", (role_id, item["code"], int(time.time()))).fetchall()
    have = sum(r["count"] for r in rows)
    if have + delta < 0:
        return False, f"「{item['name']}」只有 {have} 件。"
    left = -delta
    for row in rows:
        if not left:
            break
        take = min(left, row["count"])
        if take == row["count"]:
            c.execute("DELETE FROM rpg_inventory WHERE id=?", (row["id"],))
        else:
            c.execute("UPDATE rpg_inventory SET count=count-? WHERE id=?", (take, row["id"]))
        left -= take
    _log(c, role_id, action, item["code"], delta, detail)
    return True, ""


def adjust(role_id: int, item_value: str, delta: int, detail=""): 
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        return False, "数量要写整数。"
    if not delta:
        return False, "数量不能是 0。"
    with get_conn() as c:
        item = find_item(item_value, c)
        if not item:
            return False, "找不到这个物品。"
        return _change(c, role_id, item, delta, "adjust", detail)


def inventory(role_id: int) -> list[dict]:
    seed_defaults()
    with get_conn() as c:
        now = int(time.time())
        c.execute("DELETE FROM rpg_inventory WHERE expires_ts IS NOT NULL AND expires_ts<=?", (now,))
        return [dict(r) for r in c.execute(
            "SELECT v.*,i.name,i.kind,i.description,i.attr_effects,i.max_uses,i.can_give FROM rpg_inventory v "
            "JOIN rpg_items i ON i.code=v.item_code WHERE v.role_id=? ORDER BY i.kind,i.code", (role_id,))]


def set_listing(item_value: str, price: int, currency_value: str) -> tuple[bool, str]:
    try:
        price = int(price)
    except (TypeError, ValueError):
        return False, "价格要写正整数。"
    if price <= 0:
        return False, "价格要大于 0。"
    with get_conn() as c:
        item, currency = find_item(item_value, c), find_item(currency_value, c)
        if not item:
            return False, "找不到要上架的物品。"
        if not currency or currency["kind"] != "currency":
            return False, "计价物必须是货币类物品。"
        if item["code"] == currency["code"]:
            return False, "货币不能用自己买自己。"
        c.execute("INSERT INTO rpg_shop(item_code,price,currency_code) VALUES(?,?,?) ON CONFLICT(item_code) "
                  "DO UPDATE SET price=excluded.price,currency_code=excluded.currency_code", (item["code"], price, currency["code"]))
    return True, ""


def listings() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT s.*,i.name,i.description,c.name AS currency_name FROM rpg_shop s "
                                           "JOIN rpg_items i ON i.code=s.item_code JOIN rpg_items c ON c.code=s.currency_code ORDER BY i.code")]


def buy(role_id: int, item_value: str, count=1) -> tuple[bool, str]:
    try:
        count = int(count)
    except (TypeError, ValueError):
        return False, "数量要写正整数。"
    if count <= 0 or count > 999:
        return False, "一次只能买 1–999 件。"
    with get_conn() as c:
        item = find_item(item_value, c)
        if not item:
            return False, "找不到这个物品。"
        listing = c.execute("SELECT * FROM rpg_shop WHERE item_code=?", (item["code"],)).fetchone()
        if not listing:
            return False, "这个物品没有在商城上架。"
        currency = find_item(listing["currency_code"], c)
        total = listing["price"] * count
        ok, why = _change(c, role_id, currency, -total, "buy_pay", item["code"])
        if not ok:
            return False, why
        _change(c, role_id, item, count, "buy", f"{total}{currency['name']}")
    return True, f"获得「{item['name']}」×{count}，花费 {total}{currency['name']}。"


def give(from_id: int, to_id: int, item_value: str, count=1) -> tuple[bool, str]:
    try:
        count = int(count)
    except (TypeError, ValueError):
        return False, "数量要写正整数。"
    if count <= 0 or from_id == to_id:
        return False, "数量要大于 0，且不能送给自己。"
    with get_conn() as c:
        item = find_item(item_value, c)
        if not item:
            return False, "找不到这个物品。"
        if not item["can_give"]:
            return False, "这个物品不能赠送。"
        rows = c.execute("SELECT * FROM rpg_inventory WHERE role_id=? AND item_code=? ORDER BY remaining_uses,id",
                         (from_id, item["code"])).fetchall()
        if sum(x["count"] for x in rows) < count:
            return False, f"「{item['name']}」数量不足。"
        left = count
        for row in rows:
            take = min(left, row["count"])
            if take == row["count"]:
                c.execute("DELETE FROM rpg_inventory WHERE id=?", (row["id"],))
            else:
                c.execute("UPDATE rpg_inventory SET count=count-? WHERE id=?", (take, row["id"]))
            dest = c.execute("SELECT id FROM rpg_inventory WHERE role_id=? AND item_code=? AND remaining_uses=? AND expires_ts IS ?",
                             (to_id, item["code"], row["remaining_uses"], row["expires_ts"])).fetchone()
            if dest:
                c.execute("UPDATE rpg_inventory SET count=count+? WHERE id=?", (take, dest["id"]))
            else:
                c.execute("INSERT INTO rpg_inventory(role_id,item_code,count,remaining_uses,expires_ts) VALUES(?,?,?,?,?)",
                          (to_id, item["code"], take, row["remaining_uses"], row["expires_ts"]))
            left -= take
            if not left:
                break
        _log(c, from_id, "give_out", item["code"], -count, str(to_id))
        _log(c, to_id, "give_in", item["code"], count, str(from_id))
    return True, f"已赠送「{item['name']}」×{count}。"


# ---------- 兑换 ----------

def exchanges(only_enabled: bool = False) -> list[dict]:
    seed_defaults()
    with get_conn() as c:
        rows = c.execute("SELECT e.*, f.name AS from_name, t.name AS to_name FROM rpg_exchanges e "
                         "JOIN rpg_items f ON f.code=e.from_code JOIN rpg_items t ON t.code=e.to_code "
                         + ("WHERE e.enabled=1 " if only_enabled else "") + "ORDER BY e.id").fetchall()
    return [dict(r) for r in rows]


def save_exchange(from_value: str, from_n, to_value: str, to_n, enabled: bool = True, ex_id: int | None = None) -> tuple[bool, str]:
    try:
        from_n, to_n = int(from_n), int(to_n)
    except (TypeError, ValueError):
        return False, "数量要写正整数。"
    if from_n <= 0 or to_n <= 0:
        return False, "数量要写正整数。"
    with get_conn() as c:
        a, b = find_item(from_value, c), find_item(to_value, c)
        if not a or not b:
            return False, "找不到物品，先在上面建好。"
        if a["code"] == b["code"]:
            return False, "换出和换入不能是同一个。"
        if ex_id:
            c.execute("UPDATE rpg_exchanges SET from_code=?,from_n=?,to_code=?,to_n=?,enabled=? WHERE id=?", (a["code"], from_n, b["code"], to_n, int(enabled), ex_id))
        else:
            c.execute("INSERT INTO rpg_exchanges(from_code,from_n,to_code,to_n,enabled,created_ts) VALUES(?,?,?,?,?,?)", (a["code"], from_n, b["code"], to_n, int(enabled), int(time.time())))
    return True, ""


def delete_exchange(ex_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM rpg_exchanges WHERE id=?", (ex_id,))


def exchange(role_id: int, ex_id: int, times: int = 1) -> tuple[bool, str]:
    """按编号兑换 times 次：先扣、再给，一个事务里完成；材料不够什么都不动。"""
    if times <= 0:
        return False, "次数要写正整数。"
    seed_defaults()
    with get_conn() as c:
        e = c.execute("SELECT * FROM rpg_exchanges WHERE id=? AND enabled=1", (ex_id,)).fetchone()
        if not e:
            return False, "没有这条兑换（发「兑换」看有哪些）。"
        a = c.execute("SELECT * FROM rpg_items WHERE code=?", (e["from_code"],)).fetchone()
        b = c.execute("SELECT * FROM rpg_items WHERE code=?", (e["to_code"],)).fetchone()
        a, b = _rowdict(a), _rowdict(b)
        ok, msg = _change(c, role_id, a, -e["from_n"] * times, "exchange_out", f"换 {b['code']}")
        if not ok:
            return False, msg
        _change(c, role_id, b, e["to_n"] * times, "exchange_in", f"用 {a['code']}")
    return True, f"已兑换：{a['name']}×{e['from_n'] * times} → {b['name']}×{e['to_n'] * times}。"


class _Short(Exception):
    pass


def recipes(only_enabled: bool = False) -> list[dict]:
    """合成配方：每条带材料列表 mats=[{code,name,n}] 和产物 out_name。"""
    seed_defaults()
    with get_conn() as c:
        rows = c.execute("SELECT r.*, o.name AS out_name FROM rpg_recipes r JOIN rpg_items o ON o.code=r.out_code "
                         + ("WHERE r.enabled=1 " if only_enabled else "") + "ORDER BY r.id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["mats"] = [dict(m) for m in c.execute("SELECT i.item_code AS code, t.name, i.n FROM rpg_recipe_items i JOIN rpg_items t ON t.code=i.item_code WHERE i.recipe_id=? ORDER BY t.name", (r["id"],))]
            out.append(d)
    return out


def save_recipe(name: str, mats: list[tuple[str, int]], out_value: str, out_n) -> tuple[bool, str]:
    name = (name or "").strip()
    try:
        out_n = int(out_n)
    except (TypeError, ValueError):
        return False, "产物数量要写正整数。"
    if not name or len(name) > 20:
        return False, "配方名要写，20 字以内。"
    if out_n <= 0:
        return False, "产物数量要写正整数。"
    with get_conn() as c:
        out = find_item(out_value, c)
        if not out:
            return False, "找不到产物物品，先在上面建好。"
        merged: dict[str, int] = {}
        for v, n in mats:
            it = find_item(v, c)
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 0
            if it and n > 0:
                merged[it["code"]] = merged.get(it["code"], 0) + n
        if not merged:
            return False, "至少要有一种材料。"
        if out["code"] in merged:
            return False, "产物不能同时是材料。"
        rid = c.execute("INSERT INTO rpg_recipes(name,out_code,out_n,created_ts) VALUES(?,?,?,?)", (name, out["code"], out_n, int(time.time()))).lastrowid
        for code, n in merged.items():
            c.execute("INSERT INTO rpg_recipe_items(recipe_id,item_code,n) VALUES(?,?,?)", (rid, code, n))
    return True, f"配方「{name}」已保存。"


def delete_recipe(recipe_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM rpg_recipes WHERE id=?", (recipe_id,))


def craft(role_id: int, key: str, times: int = 1) -> tuple[bool, str]:
    """按编号或配方名合成 times 次：材料全扣、产物入包，一个事务里；缺一样就什么都不动。"""
    if times <= 0:
        return False, "次数要写正整数。"
    rs = recipes(True)
    r = next((x for x in rs if str(x["id"]) == key or x["name"] == key), None)
    if not r:
        return False, "没有这个配方（发「合成」看有哪些）。"
    try:
        with get_conn() as c:
            for m in r["mats"]:
                it = _rowdict(c.execute("SELECT * FROM rpg_items WHERE code=?", (m["code"],)).fetchone())
                ok, msg = _change(c, role_id, it, -m["n"] * times, "craft_out", f"合成 {r['name']}")
                if not ok:
                    raise _Short(msg)
            out = _rowdict(c.execute("SELECT * FROM rpg_items WHERE code=?", (r["out_code"],)).fetchone())
            _change(c, role_id, out, r["out_n"] * times, "craft_in", f"合成 {r['name']}")
    except _Short as e:
        return False, str(e)
    return True, f"合成完成：{'、'.join(m['name'] + '×' + str(m['n'] * times) for m in r['mats'])} → {r['out_name']}×{r['out_n'] * times}。"


def _consume_uses(c, role_id: int, item: dict, count: int) -> tuple[bool, str]:
    """使用 count 次；有多次数的物品用一次后会从整叠拆出一件，避免整叠共用次数。"""
    rows = c.execute("SELECT count,remaining_uses FROM rpg_inventory WHERE role_id=? AND item_code=?", (role_id, item["code"])).fetchall()
    charges = sum(x["count"] * x["remaining_uses"] for x in rows if x["remaining_uses"] >= 0)
    if not rows or (not any(x["remaining_uses"] == -1 for x in rows) and charges < count):
        return False, f"背包里的「{item['name']}」剩余使用次数不足。"
    for _ in range(count):
        row = c.execute("SELECT * FROM rpg_inventory WHERE role_id=? AND item_code=? ORDER BY remaining_uses,id LIMIT 1",
                        (role_id, item["code"])).fetchone()
        if not row:
            return False, f"背包里没有足够的「{item['name']}」。"
        remain = row["remaining_uses"]
        if remain == -1:
            continue
        if row["count"] > 1:
            c.execute("UPDATE rpg_inventory SET count=count-1 WHERE id=?", (row["id"],))
            if remain > 1:
                dest = c.execute("SELECT id FROM rpg_inventory WHERE role_id=? AND item_code=? AND remaining_uses=? AND expires_ts IS NULL",
                                 (role_id, item["code"], remain - 1)).fetchone()
                if dest:
                    c.execute("UPDATE rpg_inventory SET count=count+1 WHERE id=?", (dest["id"],))
                else:
                    c.execute("INSERT INTO rpg_inventory(role_id,item_code,count,remaining_uses) VALUES(?,?,1,?)",
                              (role_id, item["code"], remain - 1))
        elif remain <= 1:
            c.execute("DELETE FROM rpg_inventory WHERE id=?", (row["id"],))
        else:
            c.execute("UPDATE rpg_inventory SET remaining_uses=remaining_uses-1 WHERE id=?", (row["id"],))
    return True, ""


def use_item(role_id: int, item_value: str, count=1) -> tuple[bool, str]:
    try:
        count = int(count)
    except (TypeError, ValueError):
        return False, "数量要写正整数。"
    if count <= 0 or count > 99:
        return False, "一次只能使用 1–99 件。"
    with get_conn() as c:
        item = find_item(item_value, c)
        if not item or item["kind"] == "currency":
            return False, "找不到这个可使用的物品。"
        effects = json.loads(item["attr_effects"] or "{}")
        if not effects:
            return False, "这个物品没有可执行的效果。"
        ok, why = _consume_uses(c, role_id, item, count)
        if not ok:
            return False, why
        changes = []
        for name, delta in effects.items():
            row = c.execute("SELECT value FROM role_attrs WHERE role_id=? AND name=?", (role_id, name)).fetchone()
            d = c.execute("SELECT default_value FROM rpg_attr_defs WHERE name=?", (name,)).fetchone()
            before = row["value"] if row else (d["default_value"] if d else 0)
            after = clamp_attr(name, before + int(delta) * count, c)
            c.execute("INSERT INTO role_attrs(role_id,name,value) VALUES(?,?,?) ON CONFLICT(role_id,name) DO UPDATE SET value=excluded.value",
                      (role_id, name, after))
            changes.append(f"{name} {before}→{after}")
        _log(c, role_id, "use", item["code"], count, "、".join(changes))      # 以前只记「不消耗」的，消耗型的用掉了查不到
    return True, f"使用了「{item['name']}」×{count}；" + "、".join(changes) + "。"


def places() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM rpg_places ORDER BY id")]


def save_place(name: str, description="", locked=False) -> tuple[bool, str]:
    name = name.strip()
    if not name or len(name) > 40:
        return False, "地点名要写 1–40 个字。"
    with get_conn() as c:
        c.execute("INSERT INTO rpg_places(name,description,locked,created_ts) VALUES(?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET description=excluded.description,locked=excluded.locked",
                  (name, description.strip()[:300], int(bool(locked)), int(time.time())))
    return True, ""


def set_key(role_id: int, place_id: int, on: bool) -> None:
    with get_conn() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO rpg_place_keys(role_id,place_id) VALUES(?,?)", (role_id, place_id))
        else:
            c.execute("DELETE FROM rpg_place_keys WHERE role_id=? AND place_id=?", (role_id, place_id))


def keys(role_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT p.* FROM rpg_places p JOIN rpg_place_keys k ON k.place_id=p.id WHERE k.role_id=? ORDER BY p.id", (role_id,))]


def check_place(role_id: int, name: str) -> tuple[bool, str]:
    if get_config("place_system_enabled", "0") != "1":
        return True, ""
    name = name.strip()
    with get_conn() as c:
        place = c.execute("SELECT * FROM rpg_places WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if place:
            if not place["locked"] or c.execute("SELECT 1 FROM rpg_place_keys WHERE role_id=? AND place_id=?", (role_id, place["id"])).fetchone():
                return True, ""
            return False, f"地点「{name}」需要钥匙。"
        m = ROOM_RE.fullmatch(name)
        if m and get_config("allow_private_rooms", "1") == "1" and c.execute("SELECT 1 FROM roles WHERE name=? COLLATE NOCASE", (m[1],)).fetchone():
            return True, ""
    return False, f"地点「{name}」不可用，发「地点 查看」看列表。"


# ---------- 批量导入（和 RP 端的写法一致）----------

_TAG_RE = re.compile(r"【([^】]*)】")
_USES_RE = re.compile(r"^(\d+)\s*次$")
_EFFECT_RE = re.compile(r"^[^\s,，+\-]+[+\-]\d+(?:[,，\s]+[^\s,，+\-]+[+\-]\d+)*$")


def parse_item_line(line: str) -> dict | str:
    """一行一件：物品名后接若干【标签】。【描述】【N次】/【无限】【体力+10,理智-1】【货币】【不可赠送】。解析失败返回原因。"""
    name = _TAG_RE.sub("", line).strip()
    if not name:
        return "没写物品名"
    kind, uses, effects, desc, give = "item", None, "", "", True
    for tag in (t.strip() for t in _TAG_RE.findall(line)):
        if not tag:
            continue
        if tag == "货币":
            kind = "currency"
        elif tag == "无限":
            uses = -1
        elif tag in ("不可赠送", "不能赠送"):
            give = False
        elif _USES_RE.match(tag):
            uses = int(_USES_RE.match(tag).group(1))
        elif _EFFECT_RE.match(tag):
            effects = re.sub(r"[,，]+", " ", tag)
        elif not desc:
            desc = tag
    if uses is None:
        uses = -1 if (kind == "currency" or not effects) else 1      # 有属性效果的默认用 1 次，货币和纯摆设默认不限
    return {"name": name, "kind": kind, "uses": uses, "effects": effects, "desc": desc, "give": give}


def bulk_items(text: str) -> tuple[list[str], list[str], list[str]]:
    """批量录入物品。返回 (新建的, 已存在跳过的, 出错的说明)；物品码自动生成，重名跳过。"""
    created, skipped, errors = [], [], []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        p = parse_item_line(line)
        if isinstance(p, str):
            errors.append(f"「{line[:16]}」{p}")
            continue
        if p["name"] in seen or find_item(p["name"]):
            skipped.append(p["name"])
            continue
        seen.add(p["name"])
        ok, msg = save_item("", p["name"], p["kind"], p["desc"], p["effects"], p["uses"], p["give"])
        (created if ok else errors).append(f"{p['name']}" if ok else f"「{p['name']}」{msg}")
    return created, skipped, errors


def parse_place_line(line: str) -> dict | str:
    """一行一处：地点名和描述用冒号、逗号、竖线或空格隔开；带【钥匙】或【锁】就是需要钥匙才能进。"""
    locked = bool(re.search(r"【\s*(需要钥匙|钥匙|锁)\s*】", line))
    line = re.sub(r"【\s*(需要钥匙|钥匙|锁)\s*】", "", line).strip()
    if not line:
        return "没写地点名"
    name, desc = line, ""
    m = re.search(r"[：:，,|｜\t]", line)
    if m:
        name, desc = line[:m.start()], line[m.end():]
    else:
        m = re.search(r"\s", line)
        if m:
            name, desc = line[:m.start()], line[m.end():]
    name, desc = name.strip()[:30], desc.strip()[:100]
    return {"name": name, "desc": desc, "locked": locked} if name else "没写地点名"


def bulk_places(text: str) -> tuple[list[str], list[str], list[str]]:
    created, skipped, errors = [], [], []
    have = {p["name"].lower() for p in places()}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        p = parse_place_line(line)
        if isinstance(p, str):
            errors.append(f"「{line[:16]}」{p}")
            continue
        if p["name"].lower() in have:
            skipped.append(p["name"])
            continue
        have.add(p["name"].lower())
        ok, msg = save_place(p["name"], p["desc"], p["locked"])
        (created if ok else errors).append(p["name"] if ok else f"「{p['name']}」{msg}")
    return created, skipped, errors


def bulk_summary(created: list[str], skipped: list[str], errors: list[str]) -> str:
    parts = []
    if created:
        parts.append(f"已添加 {len(created)} 个：{'、'.join(created[:12])}{'…' if len(created) > 12 else ''}")
    if skipped:
        parts.append(f"已存在，跳过 {len(skipped)} 个：{'、'.join(skipped[:8])}{'…' if len(skipped) > 8 else ''}")
    if errors:
        parts.append(f"没能录入 {len(errors)} 行：{'；'.join(errors[:4])}{'…' if len(errors) > 4 else ''}")
    return "　".join(parts) or "没有识别到内容，一行写一个。"
