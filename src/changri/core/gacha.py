"""抽取池（额外）：管理员在后台建池子、放奖品（物品库里的物品，带权重、可标「稀有」）、发抽取次数；玩家用「抽取」消耗次数，奖品直接进背包。
保底：池子设「N 抽必出稀有」，连续 N-1 次没出稀有，第 N 次一定从稀有里出。次数和保底进度按「玩家 × 池子」分开记。"""
import random
import time

from . import rpg
from .db import get_conn

MAX_TIMES = 10


def pools(only_enabled: bool = False) -> list[dict]:
    with get_conn() as c:
        out = []
        for p in c.execute("SELECT * FROM gacha_pools" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY id"):
            d = dict(p)
            d["prizes"] = prizes(d["id"], c)
            out.append(d)
        return out


def prizes(pool_id: int, c=None) -> list[dict]:
    own = c is None
    c = c or get_conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT g.*, i.name FROM gacha_prizes g JOIN rpg_items i ON i.code=g.item_code WHERE g.pool_id=? ORDER BY g.id", (pool_id,))]
    finally:
        if own:
            c.close()


def save_pool(name: str, note: str = "", pity_n=0, enabled: bool = True, pool_id: int | None = None) -> tuple[bool, str]:
    name = name.strip()
    if not name or len(name) > 20:
        return False, "池子名要写，且不超过 20 字。"
    try:
        pity = max(0, int(pity_n or 0))
    except (TypeError, ValueError):
        return False, "保底次数要写整数（0 = 没有保底）。"
    with get_conn() as c:
        dup = c.execute("SELECT id FROM gacha_pools WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if dup and dup["id"] != pool_id:
            return False, f"已经有叫「{name}」的池子了。"
        if pool_id:
            c.execute("UPDATE gacha_pools SET name=?, note=?, pity_n=?, enabled=? WHERE id=?", (name, note.strip()[:200], pity, 1 if enabled else 0, pool_id))
            return True, "已保存"
        c.execute("INSERT INTO gacha_pools(name, note, pity_n, enabled, created_ts) VALUES(?,?,?,?,?)", (name, note.strip()[:200], pity, 1 if enabled else 0, int(time.time())))
    return True, f"已创建池子「{name}」"


def delete_pool(pool_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM gacha_pools WHERE id=?", (pool_id,))


def add_prize(pool_id: int, item_value: str, n=1, weight=1, rare: bool = False) -> tuple[bool, str]:
    try:
        n, weight = int(n), int(weight)
    except (TypeError, ValueError):
        return False, "数量和权重要写整数。"
    if n < 1 or weight < 1:
        return False, "数量和权重至少是 1。"
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM gacha_pools WHERE id=?", (pool_id,)).fetchone():
            return False, "找不到这个池子。"
        item = rpg.find_item(item_value, c)
        if not item:
            return False, "物品库里找不到这个物品（先在「物品与商城」里建好）。"
        if c.execute("SELECT 1 FROM gacha_prizes WHERE pool_id=? AND item_code=?", (pool_id, item["code"])).fetchone():
            return False, f"「{item['name']}」已经在这个池子里了，先删掉再重加。"
        c.execute("INSERT INTO gacha_prizes(pool_id, item_code, n, weight, rare) VALUES(?,?,?,?,?)", (pool_id, item["code"], n, weight, 1 if rare else 0))
    return True, f"已加入「{item['name']}」"


def remove_prize(prize_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM gacha_prizes WHERE id=?", (prize_id,))


def find_pool(key: str):
    """按编号或名字找；没写时，只有一个开着的池子就是它。返回 (池子行 or None, 提示)。"""
    with get_conn() as c:
        key = (key or "").strip()
        if key:
            row = c.execute("SELECT * FROM gacha_pools WHERE id=? OR name=? COLLATE NOCASE", (int(key) if key.isdigit() else -1, key)).fetchone()
            return (row, "") if row else (None, f"没有叫「{key}」的池子（发「查看池子」看有哪些）。")
        rows = c.execute("SELECT * FROM gacha_pools WHERE enabled=1").fetchall()
        if len(rows) == 1:
            return rows[0], ""
        return None, "现在没有开放的池子。" if not rows else "有好几个池子，要写池子编号或名字（发「查看池子」看有哪些）。"


def _state(c, role_id: int, pool_id: int):
    c.execute("INSERT OR IGNORE INTO gacha_state(role_id, pool_id) VALUES(?,?)", (role_id, pool_id))
    return c.execute("SELECT * FROM gacha_state WHERE role_id=? AND pool_id=?", (role_id, pool_id)).fetchone()


def grant(role_id: int, pool_key: str, n) -> tuple[bool, str]:
    """给某人发抽取次数（可以是负数来扣）。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return False, "次数要写整数。"
    if not n:
        return False, "次数不能是 0。"
    pool, err = find_pool(pool_key)
    if not pool:
        return False, err
    with get_conn() as c:
        st = _state(c, role_id, pool["id"])
        if st["tickets"] + n < 0:
            return False, f"对方在「{pool['name']}」只有 {st['tickets']} 次。"
        c.execute("UPDATE gacha_state SET tickets=tickets+? WHERE role_id=? AND pool_id=?", (n, role_id, pool["id"]))
        left = st["tickets"] + n
    return True, f"「{pool['name']}」{'+' if n > 0 else ''}{n} 次，现在有 {left} 次"


def tickets(role_id: int) -> list[dict]:
    """这个人手里有抽取次数的池子（角色卡用）。"""
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT p.id, p.name, s.tickets FROM gacha_state s JOIN gacha_pools p ON p.id=s.pool_id WHERE s.role_id=? AND s.tickets>0 ORDER BY p.id", (role_id,))]


def my_status(role_id: int) -> list[dict]:
    """每个开放池子：次数、已抽几次、离保底还差几抽。"""
    with get_conn() as c:
        out = []
        for p in c.execute("SELECT * FROM gacha_pools WHERE enabled=1 ORDER BY id"):
            st = c.execute("SELECT * FROM gacha_state WHERE role_id=? AND pool_id=?", (role_id, p["id"])).fetchone()
            since = st["since_rare"] if st else 0
            out.append({"id": p["id"], "name": p["name"], "tickets": st["tickets"] if st else 0, "total": st["total"] if st else 0, "since": since,
                        "pity_n": p["pity_n"], "left": max(1, p["pity_n"] - since) if p["pity_n"] else 0,
                        "has_rare": bool(c.execute("SELECT 1 FROM gacha_prizes WHERE pool_id=? AND rare=1", (p["id"],)).fetchone())})
        return out


def draw(role_id: int, pool_key: str, times: int = 1) -> tuple[bool, str]:
    if times < 1 or times > MAX_TIMES:
        return False, f"一次最多抽 {MAX_TIMES} 次，至少 1 次。"
    pool, err = find_pool(pool_key)
    if not pool:
        return False, err
    if not pool["enabled"]:
        return False, f"「{pool['name']}」现在没有开放。"
    with get_conn() as c:
        ps = prizes(pool["id"], c)
        if not ps:
            return False, f"「{pool['name']}」里还没有奖品。"
        st = _state(c, role_id, pool["id"])
        if st["tickets"] < times:
            return False, f"「{pool['name']}」你只有 {st['tickets']} 次抽取，不够 {times} 次。"
        rares = [p for p in ps if p["rare"]]
        since, lines, got = st["since_rare"], [], {}
        for i in range(times):
            since += 1
            pity = bool(pool["pity_n"] and rares and since >= pool["pity_n"])
            pick = random.choices(rares if pity else ps, weights=[p["weight"] for p in (rares if pity else ps)])[0]
            if pick["rare"]:
                since = 0
            item = rpg._rowdict(c.execute("SELECT * FROM rpg_items WHERE code=?", (pick["item_code"],)).fetchone())
            rpg._change(c, role_id, item, pick["n"], "gacha", f"抽取 {pool['name']}")
            c.execute("INSERT INTO gacha_log(role_id, pool_id, item_code, n, pity, ts) VALUES(?,?,?,?,?,?)", (role_id, pool["id"], pick["item_code"], pick["n"], 1 if pity else 0, int(time.time())))
            lines.append(f"{'✨' if pick['rare'] else '·'} {pick['name']}×{pick['n']}" + ("（保底）" if pity else ""))
            got[pick["name"]] = got.get(pick["name"], 0) + pick["n"]
        c.execute("UPDATE gacha_state SET tickets=tickets-?, total=total+?, since_rare=? WHERE role_id=? AND pool_id=?", (times, times, since, role_id, pool["id"]))
        left = st["tickets"] - times
    tail = f"\n剩余抽取次数 {left}。" + (f"保底：再 {max(1, pool['pity_n'] - since)} 抽必出稀有。" if pool["pity_n"] and rares else "")
    return True, f"🎰 「{pool['name']}」抽取 ×{times}\n" + "\n".join(lines) + tail


def pool_text(key: str = "") -> str:
    """查看池子：没写就列所有开放的池子，写了就看这个池子的奖品和概率。"""
    if not key.strip():
        ps = pools(True)
        if not ps:
            return "现在没有开放的抽取池。"
        return "🎰 抽取池\n" + "\n".join(f"{p['id']}. {p['name']}（{len(p['prizes'])} 种奖品" + (f"，{p['pity_n']} 抽保底" if p["pity_n"] else "") + "）" + (f"\n   {p['note']}" if p["note"] else "") for p in ps) \
            + "\n\n看奖品：查看池子 编号或名字；抽：抽取 [池子] [次数]"
    pool, err = find_pool(key)
    if not pool:
        return err
    ps = prizes(pool["id"])
    total = sum(p["weight"] for p in ps) or 1
    lines = [f"{'✨' if p['rare'] else '·'} {p['name']}×{p['n']}　{p['weight'] * 100 / total:.1f}%" for p in ps]
    head = f"🎰 「{pool['name']}」" + (f"　{pool['pity_n']} 抽保底必出稀有（✨）" if pool["pity_n"] else "")
    return head + (f"\n{pool['note']}" if pool["note"] else "") + "\n" + ("\n".join(lines) if lines else "（还没有奖品）")


def recent_log(limit: int = 30) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT l.*, r.name AS role_name, p.name AS pool_name, i.name AS item_name FROM gacha_log l JOIN roles r ON r.id=l.role_id "
            "JOIN gacha_pools p ON p.id=l.pool_id JOIN rpg_items i ON i.code=l.item_code ORDER BY l.id DESC LIMIT ?", (limit,))]


def history(role_id: int, limit: int = 20) -> list[dict]:
    """这个人最近的抽取结果（新的在前），小手机抽卡页用：name 是奖品名×数量，tier 稀有才有。"""
    with get_conn() as c:
        rows = c.execute(
            "SELECT l.*, p.name AS pool_name, i.name AS item_name, i.description AS item_desc, "
            "COALESCE((SELECT g.rare FROM gacha_prizes g WHERE g.pool_id=l.pool_id AND g.item_code=l.item_code), 0) AS rare "
            "FROM gacha_log l JOIN gacha_pools p ON p.id=l.pool_id JOIN rpg_items i ON i.code=l.item_code WHERE l.role_id=? ORDER BY l.id DESC LIMIT ?", (role_id, limit)).fetchall()
    return [{"id": r["id"], "pool": r["pool_name"], "ok": True, "done": True, "name": f"{r['item_name']}×{r['n']}", "desc": r["item_desc"] or "",
             "tier": "稀有" if r["rare"] else "", "pity": bool(r["pity"]), "msg": ""} for r in rows]
