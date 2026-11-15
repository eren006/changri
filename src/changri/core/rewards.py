"""结戏奖励：房间正常结束时，按每人这一场的数据（戏文段数、总字数、平均每段字数、本场时长）匹配规则，发奖励。
奖励 = 固定的（物品/货币/属性）+ 可选的权重随机池（抽几次）。物品和货币都是「物品与商城」里的物品。
每场每人每条规则只发一次；发失败的留着，后台可以补发。管理员可以改某人某场的段数和字数（调整场次），再补发。"""
import json
import random
import time

from . import attrs, roomlog, rooms, rpg
from .db import get_conn

METRICS = [("segs", "戏文段数", "段"), ("chars", "总字数", "字"), ("avg", "平均每段字数", "字"), ("mins", "本场时长", "分钟")]


# ---------- 规则 ----------

def _rule(r) -> dict:
    d = dict(r)
    d["conds"] = json.loads(d["conds"] or "{}")
    d["fixed"] = json.loads(d["fixed"] or "[]")
    d["pool"] = json.loads(d["pool"] or "[]")
    d["kind_list"] = [k for k in d["kinds"].split(",") if k]
    return d


def rules(only_enabled: bool = False) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM reward_rules" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY id").fetchall()
    return [_rule(r) for r in rows]


def _row_ok(ref: str) -> str:
    """下拉框的值：i:物品代码 或 a:属性名。返回错误（空串 = 没问题）。"""
    kind, _, name = ref.partition(":")
    if kind == "i" and rpg.find_item(name):
        return ""
    if kind == "a" and name:
        return ""
    return "有一行没选好奖励。"


def _pct(v) -> int | None:
    try:
        p = int(str(v).strip() or "100")
    except ValueError:
        return None
    return p if 0 <= p <= 100 else None


def rows_fixed(refs: list[str], ns: list[str], ps: list[str] | None = None) -> tuple[list[dict], str]:
    """后台表单里的「奖励」行：每行 (下拉框的值, 数量, 概率%)。空行跳过；属性数量可以是负的；概率 100 = 一定发。"""
    out = []
    ps = ps or []
    for i, (ref, n) in enumerate(zip(refs, ns)):
        if not ref:
            continue
        err = _row_ok(ref)
        if err:
            return [], err
        p = _pct(ps[i]) if i < len(ps) else 100
        if p is None:
            return [], "概率要写 0 到 100 的整数。"
        try:
            n = int(str(n).strip() or "1")
        except ValueError:
            return [], "数量要写整数。"
        kind, _, name = ref.partition(":")
        if n == 0 or (kind == "i" and n < 0):
            return [], "数量要写正整数（属性可以写负数，表示扣）。"
        out.append({"type": "attr" if kind == "a" else "item", "ref": name, "n": n, "p": p})
    return out, ""


def rows_pool(refs: list[str], ns: list[str], stocks: list[str], ps: list[str]) -> tuple[list[dict], str]:
    """限量奖励：每行 (物品, 每次发几件, 还剩几份, 概率%)。发完（剩 0 份）就不再发。"""
    out = []
    for i, ref in enumerate(refs):
        if not ref:
            continue
        if not ref.startswith("i:") or _row_ok(ref):
            return [], "限量奖励里只能放物品，有一行没选好。"
        try:
            n, left = int(str(ns[i]).strip() or "1"), int(str(stocks[i]).strip() or "1")
        except ValueError:
            return [], "数量和份数要写整数。"
        p = _pct(ps[i]) if i < len(ps) else 100
        if p is None:
            return [], "概率要写 0 到 100 的整数。"
        if n <= 0 or left <= 0:
            return [], "数量和份数要写正整数。"
        out.append({"ref": ref[2:], "n": n, "stock": left, "p": p})
    return out, ""


def _bound(v) -> int | None:
    v = str(v or "").strip()
    return int(v) if v.isdigit() else None


def parse_conds(form) -> dict:
    out = {}
    for key, _, _ in METRICS:
        lo, hi = _bound(form.get(f"{key}_min")), _bound(form.get(f"{key}_max"))
        if lo is not None or hi is not None:
            out[key] = [lo, hi]
    return out


def save_rule(rule_id: int | None, name: str, kinds: list[str], conds: dict, fixed: list[dict], pool: list[dict], pool_draws: int, enabled: bool) -> tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "规则要有名字。"
    if not fixed and not pool:
        return False, "至少要有一项奖励（固定奖励或随机池）。"
    picked = [k for k in kinds if k in rooms.KINDS]
    if not picked:
        return False, "至少要选一种房间（或者点「全部」）。"
    kinds_s = "" if set(picked) == set(rooms.KINDS) else ",".join(picked)      # 全选 = 不限类型，以后新增的房间类型也算
    vals = (name, int(enabled), kinds_s, json.dumps(conds), json.dumps(fixed, ensure_ascii=False), json.dumps(pool, ensure_ascii=False), max(1, pool_draws))
    with get_conn() as c:
        if rule_id:
            c.execute("UPDATE reward_rules SET name=?,enabled=?,kinds=?,conds=?,fixed=?,pool=?,pool_draws=? WHERE id=?", vals + (rule_id,))
        else:
            c.execute("INSERT INTO reward_rules(name,enabled,kinds,conds,fixed,pool,pool_draws,created_ts) VALUES(?,?,?,?,?,?,?,?)", vals + (int(time.time()),))
    return True, ""


def delete_rule(rule_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM reward_rules WHERE id=?", (rule_id,))


def describe_conds(conds: dict) -> str:
    parts = []
    for key, label, unit in METRICS:
        if key in conds:
            lo, hi = conds[key]
            parts.append(f"{label} " + (f"{lo}–{hi}" if lo is not None and hi is not None else f"≥ {lo}" if lo is not None else f"≤ {hi}") + unit)
    return "、".join(parts) or "没有条件（参加就发）"


def describe_rewards(rule: dict) -> str:
    def nm(ref):
        it = rpg.find_item(ref)
        return it["name"] if it else ref
    bits = []
    for f in rule["fixed"]:
        base = f"{f['ref']} {f['n']:+d}" if f["type"] == "attr" else f"{nm(f['ref'])} ×{f['n']}"
        p = f.get("p", 100)
        bits.append(base if p >= 100 else f"{base}（{p}%）")
    if rule["pool"]:
        bits.append("限量：" + "、".join(f"{nm(x['ref'])} ×{x['n']}（{x.get('p', 100)}%，剩 {x['stock']} 份）" for x in rule["pool"]) + f"；每人最多拿 {rule['pool_draws']} 样")
    return "、".join(bits)


# ---------- 场次数据 ----------

def metrics(room_id: int) -> dict[int, dict]:
    """这一场每个参与者的数据；没说过话的也算（0 段）。管理员调整过的以调整为准。"""
    with get_conn() as c:
        members = [m["role_id"] for m in c.execute("SELECT role_id FROM room_members WHERE room_id=?", (room_id,))]
        lines = c.execute("SELECT role_id, SUM(is_rp) AS segs, SUM(chars) AS chars FROM room_lines WHERE room_id=? AND role_id IS NOT NULL GROUP BY role_id", (room_id,)).fetchall()
        span = c.execute("SELECT MIN(ts) AS a, MAX(ts) AS b FROM room_lines WHERE room_id=?", (room_id,)).fetchone()
        adj = {r["role_id"]: r for r in c.execute("SELECT * FROM reward_adjust WHERE room_id=?", (room_id,))}
    mins = int((span["b"] - span["a"]) // 60) if span["a"] else 0
    by = {r["role_id"]: r for r in lines}
    out = {}
    for rid in members:
        r = by.get(rid)
        segs, chars = (r["segs"] or 0, r["chars"] or 0) if r else (0, 0)
        adjusted = rid in adj
        if adjusted:
            segs, chars = adj[rid]["segs"], adj[rid]["chars"]
        out[rid] = {"segs": segs, "chars": chars, "avg": chars // segs if segs else 0, "mins": mins, "adjusted": adjusted}
    return out


def adjust(room_id: int, role_id: int, segs: int, chars: int) -> tuple[bool, str]:
    if segs < 0 or chars < 0:
        return False, "段数和字数不能是负数。"
    if not rooms.get(room_id):
        return False, f"没有 R{room_id} 这个房间。"
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM room_members WHERE room_id=? AND role_id=?", (room_id, role_id)).fetchone():
            return False, "这个人不在这个房间里。"
        c.execute("INSERT INTO reward_adjust(room_id,role_id,segs,chars) VALUES(?,?,?,?) ON CONFLICT(room_id,role_id) DO UPDATE SET segs=excluded.segs, chars=excluded.chars",
                  (room_id, role_id, segs, chars))
    return True, ""


def clear_adjust(room_id: int, role_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM reward_adjust WHERE room_id=? AND role_id=?", (room_id, role_id))


def matches(rule: dict, kind: str, m: dict) -> bool:
    if rule["kind_list"] and kind not in rule["kind_list"]:
        return False
    for key, (lo, hi) in rule["conds"].items():
        v = m[key]
        if lo is not None and v < lo or hi is not None and v > hi:
            return False
    return True


# ---------- 发放 ----------

def _take_stock(rule_id: int, ref: str, delta: int) -> bool:
    """限量奖励领一份（delta=-1）或退回一份（+1）。剩 0 份时领不到。每次都重新读库，免得同一场结算里重复领。"""
    with get_conn() as c:
        row = c.execute("SELECT pool FROM reward_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            return False
        pool = json.loads(row["pool"] or "[]")
        for x in pool:
            if x["ref"] == ref and (delta > 0 or x["stock"] > 0):
                x["stock"] += delta
                c.execute("UPDATE reward_rules SET pool=? WHERE id=?", (json.dumps(pool, ensure_ascii=False), rule_id))
                return True
    return False


def _grant(role_id: int, rule: dict, room_id: int) -> tuple[bool, list[str], str]:
    """发一条规则的奖励。返回 (是否全部成功, 给玩家看的奖励清单, 失败原因)。每行按自己的概率各抽一次；限量的发完为止。"""
    got, errs = [], []
    detail = f"结戏奖励 R{room_id}「{rule['name']}」"
    for f in rule["fixed"]:
        if random.random() * 100 >= f.get("p", 100):
            continue
        if f["type"] == "attr":
            cur = attrs.get_attr(role_id, f["ref"]) or 0
            ok, info = attrs.set_attrs(role_id, [(f["ref"], cur + f["n"])])
            (got if ok else errs).append(f"{f['ref']} {f['n']:+d}" if ok else info)
        else:
            ok, info = rpg.adjust(role_id, f["ref"], f["n"], detail)
            (got if ok else errs).append(f"{f['ref']} ×{f['n']}" if ok else info)
    taken = 0
    pool = list(rule["pool"])
    random.shuffle(pool)                                           # 每人拿的顺序随机，免得总是排前面的先抢光
    for e in pool:
        if taken >= rule["pool_draws"]:
            break
        if random.random() * 100 >= e.get("p", 100) or not _take_stock(rule["id"], e["ref"], -1):
            continue
        ok, info = rpg.adjust(role_id, e["ref"], e["n"], detail + "（限量）")
        if ok:
            taken += 1
            got.append(f"{e['ref']} ×{e['n']}")
        else:
            _take_stock(rule["id"], e["ref"], +1)
            errs.append(info)
    return not errs, got, "；".join(errs)


def settle(room_id: int, only_role: int | None = None) -> dict[int, list[str]]:
    """结算一个房间：每个人每条匹配的规则发一次（已经发成功的跳过，上次失败的重试）。
    返回 {role_id: [本次新发出的奖励文字]}，用来通知玩家；没有新发的人不在里面。"""
    room = rooms.get(room_id)
    if not room:
        return {}
    rs = rules(True)
    notes: dict[int, list[str]] = {}
    if not rs:
        return notes
    for rid, m in metrics(room_id).items():
        if only_role and rid != only_role:
            continue
        for rule in rs:
            if not matches(rule, room["kind"], m):
                continue
            with get_conn() as c:
                done = c.execute("SELECT ok FROM reward_log WHERE room_id=? AND role_id=? AND rule_id=?", (room_id, rid, rule["id"])).fetchone()
            if done and done["ok"]:
                continue
            ok, got, err = _grant(rid, rule, room_id)
            with get_conn() as c:
                c.execute("INSERT INTO reward_log(room_id,role_id,rule_id,rule_name,ok,detail,ts) VALUES(?,?,?,?,?,?,?) "
                          "ON CONFLICT(room_id,role_id,rule_id) DO UPDATE SET ok=excluded.ok, detail=excluded.detail, ts=excluded.ts",
                          (room_id, rid, rule["id"], rule["name"], int(ok), "、".join(got) if ok else err, int(time.time())))
            if got:
                notes.setdefault(rid, []).append(f"「{rule['name']}」：" + "、".join(got))
    return notes


def room_status(room_id: int) -> list[dict]:
    """场次状态：每个参与者的数据、能匹配哪些规则、已经发了什么。"""
    room = rooms.get(room_id)
    if not room:
        return []
    rs = rules(True)
    names = {m["role_id"]: m["name"] for m in rooms.members(room_id)}
    with get_conn() as c:
        logs = c.execute("SELECT * FROM reward_log WHERE room_id=?", (room_id,)).fetchall()
    out = []
    for rid, m in metrics(room_id).items():
        mine = {r["rule_id"]: r for r in logs if r["role_id"] == rid}
        out.append({"role_id": rid, "name": names.get(rid, "?"), **m,
                    "rules": [{"name": r["name"], "log": mine.get(r["id"])} for r in rs if matches(r, room["kind"], m)]})
    return out


def recent_rooms(limit: int = 30) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT id, kind, day, status, ended_ts FROM rooms WHERE status='ended' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def message(room_id: int, items: list[str]) -> str:
    return f"🎁 R{room_id} 结戏奖励\n" + "\n".join(items)
