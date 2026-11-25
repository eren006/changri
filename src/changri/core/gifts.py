"""礼品店（管理员在设置平台上架的礼物）、图鉴（逛礼品店收集到的礼物）和「我收到过的礼物」。
玩法（和老系统一样）：发「礼品店」逛一逛，货架上随机摆一件你还没有的，自动收进图鉴（有概率顺手再捡一件）；
货架每人每隔 gift_shop_refresh_hours 小时才换一次（冷却）；礼品店的礼物只能送图鉴里有的，送多少次都行。"""
import random
import re
import time

from .db import get_config, get_conn, get_int, set_config

_SEP = re.compile(r"[、,，\n]+")


def enabled() -> bool:
    from . import extensions
    return extensions.is_enabled("giftshop")


def _seed() -> None:
    """升级前礼品店是设置里一行字（顿号分开）；第一次用时搬进表里。"""
    if get_config("gift_shop_seeded", "0") == "1":
        return
    names = [x.strip() for x in _SEP.split(get_config("gift_catalog", "")) if x.strip()]
    if not names:
        return                  # 没有老数据：不打标记，以后设置里真有老清单时还能搬
    with get_conn() as c:
        for n in names:
            c.execute("INSERT OR IGNORE INTO gift_shop(name, created_ts) VALUES(?,?)", (n[:30], int(time.time())))
    set_config("gift_shop_seeded", "1")


def items() -> list[dict]:
    """礼品店全部礼物：{id(编号), name, note}，按编号排。"""
    _seed()
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT id, name, note FROM gift_shop ORDER BY id")]


def code(item_id: int) -> str:
    return f"#{int(item_id):03d}"


def parse_code(text: str) -> int | None:
    """「#003」「＃3」「3」「003」→ 3；不是编号写法返回 None。"""
    from .textutil import parse_id
    return parse_id(text)


def catalog(role_id: int | None = None) -> list[str]:
    """能送的礼品店礼物名。给了 role_id 就只算这个角色图鉴里有的。"""
    if not enabled():
        return []
    if role_id is None:
        return [x["name"] for x in items()]
    return [x["name"] for x in unlocked(role_id)]


def only_preset() -> bool:
    return enabled() and get_config("gift_only_preset", "0") == "1" and bool(items())


def add(name: str, note: str = "") -> tuple[bool, str]:
    name = (name or "").strip()
    if not name or len(name) > 30:
        return False, "礼物名要写，30 字以内。"
    if name.isdigit():
        return False, "礼物名不能是纯数字（数字是编号）。"
    _seed()
    with get_conn() as c:
        if c.execute("SELECT 1 FROM gift_shop WHERE name=?", (name,)).fetchone():
            return False, f"「{name}」已经在礼品店里了。"
        i = c.execute("INSERT INTO gift_shop(name, note, created_ts) VALUES(?,?,?)", (name, (note or "").strip()[:100], int(time.time()))).lastrowid
    return True, f"已上架：{i}. {name}"


def remove(item_id: int) -> bool:
    _seed()
    with get_conn() as c:
        return c.execute("DELETE FROM gift_shop WHERE id=?", (item_id,)).rowcount > 0


def clear() -> int:
    _seed()
    with get_conn() as c:
        return c.execute("DELETE FROM gift_shop").rowcount


def export_text() -> str:
    """每行「编号｜名字｜说明」，能原样粘回导入框。"""
    return "\n".join(f"{x['id']}｜{x['name']}｜{x['note']}" for x in items())


_LIST_MARK = re.compile(r"^(?:[-*•·]\s*|\d{1,3}\s*[.、)）]\s*)")   # AI 爱在行首加的「1. 」「- 」


def ai_import_prompt() -> str:
    """后台「让 AI 帮忙编礼物」的提示词：管理员复制给 ChatGPT / 豆包 / DeepSeek，AI 写出来的整段贴进「批量导入」。
    规则和上限跟 add() 一致；已经上架的名字列进去，让 AI 别重名（重名的导入时也会自动跳过）。"""
    have = [x["name"] for x in items()]
    text = ("请帮我给一个恋综 RP 的「礼品店」编一批礼物，角色们会互相送。\n"
            "主题 / 风格：（换成你想要的，比如：民国上海、校园、古风宫廷、现代都市，或者不写）\n"
            "数量：（比如 20 件）\n\n"
            "要求：\n"
            "1. 一行一件，格式是「礼物名｜说明」，中间用竖线「｜」隔开。不要编号、不要分类标题、不要空行、不要别的话。\n"
            "2. 礼物名 30 字以内，不能是纯数字，彼此不重复。\n"
            "3. 说明 100 字以内，一两句话写它是什么样子、送它有什么意味；觉得不用说明的，只写礼物名也行。\n"
            "4. 礼物要具体、有画面感，便宜的小东西和贵重的都可以有，适合拿来暧昧、示好、道歉、试探。\n"
            "5. 全部放进一个代码块里输出，方便我整段复制。")
    if have:
        text += "\n\n下面这些已经有了，别重复：\n" + "、".join(have)
    return text


def import_text(text: str) -> tuple[int, list[str]]:
    """一行一件：「名字」或「名字｜说明」（带编号的导出格式「1｜名字｜说明」也认，编号忽略）。已有的跳过。返回 (新增几件, 跳过的说明)。
    AI 写的也直接认：代码块的 ``` 行跳过，行首的「1. 」「- 」去掉。"""
    n, skipped = 0, []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            continue
        if not re.match(r"^\d+\s*[｜|]", line):
            line = _LIST_MARK.sub("", line)
        parts = [x.strip() for x in re.split(r"[｜|]", line)]
        if parts and parts[0].isdigit() and len(parts) >= 2:
            parts = parts[1:]
        if not parts or not parts[0]:
            continue
        ok, msg = add(parts[0], parts[1] if len(parts) > 1 else "")
        n += ok
        if not ok:
            skipped.append(msg)
    return n, skipped


def resolve(text: str, by_name_ok: bool = False, role_id: int | None = None) -> tuple[str, str]:
    """玩家写的礼物 → (最终礼物名, 错误)。礼品店开着时，写编号（#003 / 3）就换成对应的名字，但只能送自己图鉴里有的；
    「只能送礼品店里的东西」开着时，只认编号（不能写自定义的）；手机里点选出来的名字用 by_name_ok=True。
    role_id 不给就不查图鉴（老调用）。"""
    text = (text or "").strip()
    if not enabled():
        return text, ""
    its = items()
    n = parse_code(text)
    hit = next((x for x in its if x["id"] == n), None) if n is not None else None
    if hit is None and by_name_ok:
        hit = next((x for x in its if x["name"] == text), None)
    if hit:
        if role_id is not None and not owns(role_id, hit["id"]):
            return "", f"🔒「{hit['name']}」还不在你的图鉴里，先发「礼品店」去逛逛、收集到了才能送。"
        return hit["name"], ""
    if n is not None and only_preset():
        return "", f"礼品店里没有 {code(n)}，发「图鉴」看你能送哪些。"
    if only_preset():
        return "", "现在只能送礼品店里的礼物，要写编号，发「图鉴」看你能送哪些。"
    return text, ""


# ---------- 图鉴：逛礼品店收集 ----------

def unlocked(role_id: int) -> list[dict]:
    """图鉴里的礼物（还在架上的）：{id, name, note, ts}，按编号排。"""
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT g.id, g.name, g.note, u.ts FROM gift_unlocks u JOIN gift_shop g ON g.id=u.item_id "
                                           "WHERE u.role_id=? ORDER BY g.id", (role_id,))]


def owns(role_id: int, item_id: int) -> bool:
    with get_conn() as c:
        return c.execute("SELECT 1 FROM gift_unlocks WHERE role_id=? AND item_id=?", (role_id, item_id)).fetchone() is not None


def unlock(role_id: int, item_id: int) -> bool:
    with get_conn() as c:
        return c.execute("INSERT OR IGNORE INTO gift_unlocks(role_id, item_id, ts) VALUES(?,?,?)", (role_id, item_id, int(time.time()))).rowcount > 0


def heat() -> dict[str, int]:
    """礼物名 → 被送出去的次数（热度）。照片礼物不算（名字是玩家随便起的）。"""
    with get_conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT title, COUNT(*) FROM messages WHERE kind='gift' AND status='sent' AND photo=0 GROUP BY title")}


def refresh_hours() -> int:
    return max(0, get_int("gift_shop_refresh_hours", 24))


def visit(role_id: int, now: int | None = None, rng: random.Random | None = None) -> dict:
    """逛一次礼品店。返回 {"state", "text", "got": [新收进图鉴的礼物]}：
    state = empty 没上架 / complete 全收齐 / seen 货架上这件已经有了（冷却中） / new 收到新的。"""
    now = int(now or time.time())
    rng = rng or random
    its = items()
    if not its:
        return {"state": "empty", "got": [], "text": "你推开门，走了进去。\n货架上什么都没有，空气里只有淡淡的木头气味。\n也许过些时候再来。"}
    by_id = {x["id"]: x for x in its}
    owned = {x["id"] for x in unlocked(role_id)}
    total = len(its)
    if owned >= set(by_id):
        return {"state": "complete", "got": [], "text": f"你走遍了每一格货架，翻过了每一个角落。\n这里所有的 {total} 件礼物，都已经在你的图鉴里了。\n\n"
                                                         "货架暂时不会再换新东西了，等管理员上新再来看看。发「图鉴」看看你收藏的一切。"}
    gap = refresh_hours() * 3600
    with get_conn() as c:
        row = c.execute("SELECT item_id, refreshed_ts FROM gift_shop_visits WHERE role_id=?", (role_id,)).fetchone()
    if not row or now - row["refreshed_ts"] >= gap or row["item_id"] not in by_id:
        pick = rng.choice(sorted(set(by_id) - owned))
        with get_conn() as c:
            c.execute("INSERT INTO gift_shop_visits(role_id, item_id, refreshed_ts) VALUES(?,?,?) "
                      "ON CONFLICT(role_id) DO UPDATE SET item_id=excluded.item_id, refreshed_ts=excluded.refreshed_ts", (role_id, pick, now))
        cur, since = pick, now
    else:
        cur, since = row["item_id"], row["refreshed_ts"]
    wait_h = max(1, -(-(since + gap - now) // 3600)) if gap else 0
    wait_line = f"再等 {wait_h} 个小时，也许会有新的东西出现。" if gap else "再逛一次，也许会有新的东西出现。"
    g = by_id[cur]
    show = lambda x: f"{code(x['id'])}「{x['name']}」" + (f"\n{x['note']}" if x["note"] else "")  # noqa: E731
    if cur in owned:
        return {"state": "seen", "got": [], "text": f"你在货架上看见了它——\n\n{show(g)}\n\n这件已经在你的图鉴里了。\n{wait_line}\n\n📚 图鉴进度：{len(owned)}/{total}"}
    unlock(role_id, cur)
    got = [g]
    rest = sorted(set(by_id) - owned - {cur})
    if rest and rng.random() * 100 < get_int("gift_shop_bonus_pct", 50):
        b = by_id[rng.choice(rest)]
        unlock(role_id, b["id"])
        got.append(b)
    text = f"你在货架上发现了一件没见过的东西，拿起来看了看。\n\n{show(g)}"
    if len(got) > 1:
        text += f"\n\n转身要走，视线扫到角落里还有一件——\n\n{show(got[1])}\n\n✨ 两件都收入图鉴。"
    else:
        text += "\n\n✨ 收入图鉴。"
    text += f"📚 进度：{len(owned) + len(got)}/{total}\n" + (f"下次再来大约要等 {wait_h} 个小时。" if gap else "") + \
        f"\n\n想把它送给某人？发「送礼 对方名 {code(g['id'])}」"
    return {"state": "new", "got": got, "text": text}


def grant(role_id: int, n: int | None = 1) -> list[dict]:
    """管理员直接发放：随机给 n 件还没有的（None = 全部收齐）。不动货架和冷却。返回发出去的。"""
    owned = {x["id"] for x in unlocked(role_id)}
    left = [x for x in items() if x["id"] not in owned]
    random.shuffle(left)
    picked = left if n is None else left[:max(0, n)]
    for x in picked:
        unlock(role_id, x["id"])
    return sorted(picked, key=lambda x: x["id"])


def received(role_id: int) -> list:
    """我收到过的礼物（已送达的），按礼物名合并：名字、数量、最近一次是谁送的。照片礼物和同名的普通礼物分开算（photo=1）。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.title AS title, m.photo AS photo, COUNT(*) AS n, MAX(m.id) AS last_id FROM messages m "
            "WHERE m.to_role_id=? AND m.kind='gift' AND m.status='sent' AND m.error!='已撤回' GROUP BY m.title, m.photo ORDER BY last_id DESC",
            (role_id,)).fetchall()


# ---------- 偷取礼物（照老系统小手机礼品店的「偷取礼物」；群里「偷礼物 对方名」和小手机共用） ----------
# 从别人的图鉴里随机偷一件你还没有的：
#   · 有 fail_pct 失手（什么也没拿到）；得手后有 take_pct 把对方那件「带走」（对方图鉴里就没了），否则只是抄一份；
#   · 有 find_pct 被发现：被偷的人对每一次尝试（失手、抄走、带走）都会收到提醒，被发现只决定提醒里写不写名字；
#   · 冷却按小时；对方图鉴里少于 min_owned 件下不去手。默认：不冷却、不失手、不被发现、只抄一份（难度都是额外加的）。
STEAL_DEFAULTS = {"cooldown_hours": 0, "fail_pct": 0, "find_pct": 0, "take_pct": 0, "min_owned": 1}
STEAL_LIMITS = {"cooldown_hours": (0, 168), "fail_pct": (0, 100), "find_pct": (0, 100), "take_pct": (0, 100), "min_owned": (1, 99)}


def steal_enabled() -> bool:
    return enabled() and get_config("gift_steal_enabled", "0") == "1"


def steal_cfg() -> dict:
    out = {}
    for k, (lo, hi) in STEAL_LIMITS.items():
        out[k] = max(lo, min(hi, get_int(f"gift_steal_{k}", STEAL_DEFAULTS[k])))
    return out


def steal_wait_min(thief_id: int, now: int | None = None) -> int:
    """还要等几分钟才能再偷（0 = 现在就能）。"""
    cd = steal_cfg()["cooldown_hours"]
    if not cd:
        return 0
    with get_conn() as c:
        last = c.execute("SELECT created_ts FROM gift_steals WHERE thief_id=? ORDER BY id DESC LIMIT 1", (thief_id,)).fetchone()
    if not last:
        return 0
    return max(0, -(-(last["created_ts"] + cd * 3600 - int(now or time.time())) // 60))


def steal_text(st, viewer_id: int, names: dict | None = None) -> str | None:
    """一条偷盗记录在 viewer 眼里怎么写；不该让 viewer 看到就返回 None（照老系统的文案）。"""
    if names is None:
        with get_conn() as c:
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles WHERE id IN (?,?)", (st["thief_id"], st["victim_id"]))}
    thief, victim = names.get(st["thief_id"], "某人"), names.get(st["victim_id"], "某人")
    gift = f"「{st['item_name']}」"
    if viewer_id == st["thief_id"]:
        if not st["ok"]:
            return f"🕵️ 你想从「{victim}」的图鉴里偷东西，失手了，什么也没拿到。" + ("还被发现了！" if st["found"] else "但可能留下了痕迹。")
        out = f"🕵️ 你从「{victim}」的图鉴里偷到了{gift}，已收进你的图鉴。" + ("对方图鉴里的这件被你拿走了。" if st["taken"] else "对方图鉴里的还在，你只是抄了一份。")
        return out + ("不过……你被发现了！" if st["found"] else "没人发现。")
    if viewer_id == st["victim_id"]:
        if not st["ok"]:
            return f"有人发现「{thief}」想偷你的图鉴，没得手。" if st["found"] else "有人想偷你的图鉴，没得手，只留下了一点痕迹。"
        if st["taken"]:
            return (f"你发现「{thief}」偷走了你图鉴里的{gift}！它已经不在你的图鉴里了。" if st["found"]
                    else f"你图鉴里的{gift}不见了，被人偷走了，不知道是谁干的。")
        return (f"你发现「{thief}」偷偷抄走了你图鉴里的{gift}。你的图鉴里它还在。" if st["found"]
                else f"有人偷偷抄走了你图鉴里的{gift}，你的还在，不知道是谁干的。")
    return None


def steal(thief_id: int, victim_id: int, now: int | None = None, rng: random.Random | None = None) -> tuple[bool, str, dict | None]:
    """偷一次 → (这次尝试有没有发生, 给小偷看的话, 记录行)。尝试发生了（哪怕失手）才算冷却、才通知对方。"""
    import secrets
    rng = rng or secrets.SystemRandom()
    now = int(now or time.time())
    if not steal_enabled():
        return False, "偷取礼物没有开放。", None
    if victim_id == thief_id:
        return False, "选一个别人下手。", None
    wait = steal_wait_min(thief_id, now)
    if wait:
        return False, (f"手还没缓过来，{wait // 60} 小时 {wait % 60} 分钟后才能再偷。" if wait >= 60 else f"手还没缓过来，{wait} 分钟后才能再偷。"), None
    cfg = steal_cfg()
    with get_conn() as c:
        vname = (c.execute("SELECT name FROM roles WHERE id=?", (victim_id,)).fetchone() or {"name": "对方"})["name"]
    victim_has = {x["id"]: x for x in unlocked(victim_id)}
    if len(victim_has) < cfg["min_owned"]:
        return False, f"「{vname}」的图鉴里东西太少了（不到 {cfg['min_owned']} 件），下不去手。", None
    mine = {x["id"] for x in unlocked(thief_id)}
    cand = sorted(set(victim_has) - mine)
    if not cand:
        return False, f"「{vname}」图鉴里的东西你都有了，没什么可偷的。", None
    item = victim_has[rng.choice(cand)]
    fail = rng.random() * 100 < cfg["fail_pct"]
    found = rng.random() * 100 < cfg["find_pct"]
    taken = (not fail) and rng.random() * 100 < cfg["take_pct"]
    with get_conn() as c:
        if not fail:
            c.execute("INSERT OR IGNORE INTO gift_unlocks(role_id, item_id, ts) VALUES(?,?,?)", (thief_id, item["id"], now))
        if taken:
            c.execute("DELETE FROM gift_unlocks WHERE role_id=? AND item_id=?", (victim_id, item["id"]))
        sid = c.execute("INSERT INTO gift_steals(thief_id, victim_id, item_id, item_name, ok, taken, found, created_ts) VALUES(?,?,?,?,?,?,?,?)",
                        (thief_id, victim_id, item["id"], item["name"], 0 if fail else 1, 1 if taken else 0, 1 if found else 0, now)).lastrowid
        row = dict(c.execute("SELECT * FROM gift_steals WHERE id=?", (sid,)).fetchone())
    return True, steal_text(row, thief_id), row


def steal_log(role_id: int, limit: int = 30) -> list[dict]:
    """我偷别人的 + 别人偷我的（新的在前）：[{ts, text}]。"""
    with get_conn() as c:
        rows = c.execute("SELECT * FROM gift_steals WHERE thief_id=? OR victim_id=? ORDER BY id DESC LIMIT ?", (role_id, role_id, limit)).fetchall()
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
    return [{"ts": r["created_ts"], "text": steal_text(r, role_id, names)} for r in rows]
