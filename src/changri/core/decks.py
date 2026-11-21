"""牌堆（核心功能，一直开着）：大家自己写抽取的内容，机器人负责抽。群里发「抽卡 牌堆名 [张数]」，只发「抽卡」列出能抽的牌堆。
牌堆只在后台「跑团 → 牌堆」页管理（跟小手机没有关系）：管理员把照模板写好的内容贴进去或上传文件，保存后全群就能抽；不合适的直接删。

长日牌堆的写法（TEMPLATE 就是一份完整示例）：
- 【牌堆名】 开始一个牌堆，下面每一行是一张牌；空行不算，# 开头的行是说明，机器人不读。
- 牌堆名以 _ 开头（【_饮料】）的只给别的牌堆用：不能直接抽，也不出现在列表里。
- 行首写「数字*」是权重：3*火锅 被抽中的机会是普通牌的 3 倍（不写就是 1）。
- {牌堆名}：先从那个牌堆抽一张填进来（这份里的、别人上传的都行；内置的塔罗、扑克不行）。
- [1d6]、[2d10+3]：在这里掷骰，换成掷出来的数。
- 一行里想换行就写 \\n。
也能直接导入海豹（SealDice）的 json / toml 牌堆：::3::权重、{%名字} 和 {名字}、_ 开头的辅助牌堆都认，导入后整理成上面的写法。
内置的塔罗、扑克、随机遭遇（core/dicefun）也能用「抽卡 塔罗 3」抽。

防护（牌堆是玩家写、管理员上传的，内容不可信）：
- {牌堆} 嵌套会指数增长（每张 10 个引用套 4 层 = 1 万次查库），抽卡又跑在机器人主循环里，会卡住所有群。
  所以一次抽卡最多展开 MAX_EXPAND 次 {…}，超了剩下的原样留着；保存时也按最坏情况算一遍，超了直接拒绝。
- 保存时每张牌过一遍违禁词（core/phonegate，跟小手机同一份词表），命中就拒绝并说是哪个牌堆。
AI_PROMPT 是给玩家复制去问 AI 的提示词，后台牌堆页有「复制 AI 提示词」按钮。"""
import json
import random
import re
import time

from .db import get_conn

TEMPLATE = """# 长日牌堆模板：照着改【】里的名字和下面的内容就行，# 开头的行是说明，机器人不读。
# 写好以后交给管理员，在后台「跑团 → 牌堆」里贴进去或上传这个文件，群里发「抽卡 今晚吃什么」就能抽。
# 文件要存成 UTF-8 编码（记事本「另存为」里能选），后缀 .txt。

【今晚吃什么】
# 一行一张牌
白米饭
番茄牛腩面
# 行首写「数字*」是权重：火锅被抽中的机会是普通牌的 3 倍
3*火锅
# {牌堆名} 会先从那个牌堆抽一张填进来；[1d3] 会掷骰换成数字
{_饮料}，再加 [1d3] 个肉包

【_饮料】
# 名字以 _ 开头的牌堆只给别的牌堆用，不能直接抽，也不出现在列表里
冰可乐
桂花酒酿
热牛奶
"""

MAX_FILES_PER_ROLE = 10
MAX_DECKS_PER_FILE = 50
MAX_CARDS = 2000              # 一份文件里所有牌加起来
MAX_RAW = 80_000              # 一份文件多少字
MAX_CARD_LEN = 300
MAX_NAME = 20
MAX_WEIGHT = 100
MAX_DRAW = 10
MAX_DEPTH = 8                 # {牌堆} 套 {牌堆} 最多套几层
MAX_OUT = 1500                # 一次抽出来的文字最多多长
MAX_EXPAND = 200              # 一次抽卡（几张加起来）最多展开几次 {牌堆}

AI_PROMPT = f"""请帮我写一份「长日牌堆」文件，内容是：（在这里写你想要什么，比如「今晚吃什么，30 道家常菜，火锅抽到的机会大一点」）

严格按下面的规则写，只输出文件内容本身，不要解释，不要加代码块标记：
1. 每个牌堆用单独一行【牌堆名】开头，下面一行一张牌。牌堆名最多 {MAX_NAME} 个字，不能有空格和 {{ }} [ ] # * 【 】 这些符号；不能叫塔罗、扑克、遭遇、随机遭遇（机器人自带）。
2. 一张牌写在一行里，最多 {MAX_CARD_LEN} 个字；牌里要换行就写 \\n。
3. 想让某张牌更容易抽到，在行首写「数字*」，比如 3*火锅（数字 1 到 {MAX_WEIGHT}，不写就是 1）。
4. {{牌堆名}} 表示从那个牌堆再抽一张填进来；[1d6]、[2d10+3] 表示掷骰换成数字。只给别的牌堆用的牌堆，名字以 _ 开头。一张牌里的 {{…}} 不要超过 3 个，最多套 3 层，太多机器人会拒收。
5. # 开头的行是说明，机器人不读，可以用来写注释。
6. 一份最多 {MAX_DECKS_PER_FILE} 个牌堆、{MAX_CARDS} 张牌。
7. 不要写网址和联系方式；也不要写色情、血腥暴力、政治敏感、骂人的内容，机器人会拒收。

下面是一份示例，照这个格式写：

{TEMPLATE}"""

# 海豹牌堆文件头的说明字段（作者、版本、更新地址……），不是牌堆。以前当成辅助牌堆存了，
# 两份海豹牌堆都带 _author 就撞名存不进去；现在导入时丢掉，撞名也不算它们（老数据里存过的留着无害）。
SEAL_META = {"_author", "_title", "_date", "_updatedate", "_update_date", "_brief", "_version", "_license",
             "_updateurls", "_etag", "_keys", "_exports", "_help", "_description", "_desc", "_meta", "_sign"}


def _is_meta(name: str) -> bool:
    return str(name).strip().lower() in SEAL_META


_NAME_RE = re.compile(r"^_?[^\s{}\[\]【】#*]{1,%d}$" % MAX_NAME)
_HEAD_RE = re.compile(r"^【\s*(.+?)\s*】$")
_WEIGHT_RE = re.compile(r"^(\d{1,3})\s*[*＊×]\s*(.+)$")
_REF_RE = re.compile(r"\{%?\s*([^{}]{1,30}?)\s*\}")
_DICE_RE = re.compile(r"\[([0-9dDkKlLhH+\-*×x()!\s]{1,40})\]")


# ---------------- 解析 ----------------

def parse(raw: str) -> tuple[dict[str, list[tuple[int, str]]], list[str]]:
    """长日格式 → ({牌堆名: [(权重, 内容)]}, [问题])。有问题时第一个返回值不可信。"""
    decks: dict[str, list[tuple[int, str]]] = {}
    errs: list[str] = []
    cur = None
    for i, line in enumerate(raw.replace("\r\n", "\n").split("\n"), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _HEAD_RE.match(s)
        if m:
            name = m.group(1)
            if not _NAME_RE.match(name):
                errs.append(f"第 {i} 行：牌堆名「{name}」不行（最多 {MAX_NAME} 个字，不能有空格和 {{ }} [ ] # * 这些符号）。")
                cur = None
                continue
            if name.lower() in (k.lower() for k in decks):
                errs.append(f"第 {i} 行：牌堆「{name}」在这份里写了两次。")
            cur = name
            decks.setdefault(name, [])
            continue
        if cur is None:
            errs.append(f"第 {i} 行：这一行前面还没有写【牌堆名】。")
            continue
        w, text = 1, s
        wm = _WEIGHT_RE.match(s)
        if wm:
            w, text = int(wm.group(1)), wm.group(2).strip()
            if not 1 <= w <= MAX_WEIGHT:
                errs.append(f"第 {i} 行：权重要在 1 到 {MAX_WEIGHT} 之间。")
                w = max(1, min(w, MAX_WEIGHT))
        if len(text) > MAX_CARD_LEN:
            errs.append(f"第 {i} 行：一张牌最多 {MAX_CARD_LEN} 个字。")
        decks[cur].append((w, text))
    for name, cards in decks.items():
        if not cards:
            errs.append(f"牌堆「{name}」下面一张牌都没有。")
    if not decks and not errs:
        errs.append("没有找到牌堆：每个牌堆要用一行【牌堆名】开头。")
    if len(decks) > MAX_DECKS_PER_FILE:
        errs.append(f"一份里最多 {MAX_DECKS_PER_FILE} 个牌堆。")
    if sum(len(c) for c in decks.values()) > MAX_CARDS:
        errs.append(f"一份里最多 {MAX_CARDS} 张牌。")
    return decks, errs


def to_text(decks: dict[str, list[tuple[int, str]]]) -> str:
    """解析结果 → 长日格式原文（导入海豹牌堆后存这个）。"""
    out = []
    for name, cards in decks.items():
        out.append(f"【{name}】")
        out += [(f"{w}*{t}" if w != 1 else t) for w, t in cards]
        out.append("")
    return "\n".join(out).strip() + "\n"


def _seal_card(item) -> tuple[int, str] | None:
    if not isinstance(item, (str, int, float)):
        return None
    s = str(item).strip()
    w = 1
    m = re.match(r"^::(\d+)::(.*)$", s, re.S)
    if m:
        w, s = max(1, min(int(m.group(1)), MAX_WEIGHT)), m.group(2).strip()
    s = s.replace("\r\n", "\n").replace("\n", "\\n")
    s = re.sub(r"\{%\s*", "{", s)
    return (w, s) if s else None


def from_sealdice(raw: str) -> tuple[dict[str, list[tuple[int, str]]] | None, str]:
    """海豹 json / toml 牌堆 → 解析结果。看不出是海豹格式返回 (None, "")；是但读不了返回 (None, 原因)。"""
    s = raw.lstrip("﻿").strip()
    data = None
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except ValueError as e:
            return None, f"json 读不了：{e}"
    elif "[decks]" in s or re.search(r'^\s*\[\s*"', s, re.M) or re.search(r"^\s*\[meta\]", s, re.M):
        import tomllib
        try:
            data = tomllib.loads(s)
        except tomllib.TOMLDecodeError as e:
            return None, f"toml 读不了：{e}"
    if not isinstance(data, dict):
        return None, ""
    pool = data.get("decks") if isinstance(data.get("decks"), dict) else {}
    pool = {**pool, **{k: v for k, v in data.items() if k not in ("decks", "meta", "_meta")}}
    decks: dict[str, list[tuple[int, str]]] = {}
    for key, val in pool.items():
        if _is_meta(key):
            continue
        if isinstance(val, dict):                      # toml 的复杂牌组：options 是牌，export=false 当辅助牌堆
            hidden = val.get("export") is False or val.get("visible") is False
            val = val.get("options")
            key = ("_" + key) if hidden and not key.startswith("_") else key
        if not isinstance(val, list):
            continue
        name = re.sub(r"^_+", "_", str(key).strip())
        name = re.sub(r"[\s{}\[\]【】#*]", "", name)[:MAX_NAME + 1]
        cards = [c for c in (_seal_card(x) for x in val) if c]
        if name and cards:
            decks[name] = cards
    if not decks:
        return None, "文件里没有找到牌堆（要有「名字: [一张张牌]」这样的内容）。"
    # 海豹里引用别的牌组写 {__名字} / {_名字}，名字收成一个下划线后，引用也跟着改
    fixed = {}
    for name, cards in decks.items():
        fixed[name] = [(w, re.sub(r"\{_+", "{_", t)) for w, t in cards]
    return fixed, ""


def read_any(raw: str) -> tuple[dict[str, list[tuple[int, str]]], list[str], str]:
    """贴进来的东西：海豹 json/toml 就转换，否则按长日格式读。返回 (牌堆, 问题, 存库的原文)。"""
    if len(raw) > MAX_RAW:
        return {}, [f"太长了，一份最多 {MAX_RAW} 个字，拆成几份上传吧。"], raw
    seal, why = from_sealdice(raw)
    if why:
        return {}, [why], raw
    if seal is not None:
        text = to_text(seal)
        decks, errs = parse(text)
        return decks, errs, text
    decks, errs = parse(raw)
    meta = [n for n in decks if _is_meta(n)]
    if meta and len(meta) < len(decks):        # 以前从海豹导入、存下来的原文里还有【_author】这种，再编辑时顺手去掉
        for n in meta:
            decks.pop(n)
        raw = to_text(decks)
    return decks, errs, raw


# ---------------- 存取 ----------------

def _builtin_names() -> set[str]:
    from .dicefun import DECKS
    return {k.lower() for k in DECKS}


def save(owner_id: int | None, raw: str, file_id: int = 0, title: str = "") -> tuple[bool, str, int]:
    """新建或改一份牌堆文件。owner_id=None 是管理员。改别人的（玩家改别人的）不行。返回 (成功, 提示, 文件编号)。"""
    decks, errs, text = read_any(raw)
    if errs:
        return False, "\n".join(errs[:8]) + (f"\n……还有 {len(errs) - 8} 处" if len(errs) > 8 else ""), 0
    builtin = _builtin_names()
    clash = [n for n in decks if n.lower() in builtin]
    if clash:
        return False, f"「{clash[0]}」是内置牌堆的名字，换一个吧。", 0
    bad = _blocked(decks, owner_id)
    if bad:
        return False, bad, 0
    heavy = _too_nested(decks)
    if heavy:
        return False, heavy, 0
    now = int(time.time())
    with get_conn() as c:
        if file_id:
            f = c.execute("SELECT * FROM deck_files WHERE id=?", (file_id,)).fetchone()
            if not f:
                return False, "这份牌堆已经被删掉了。", 0
            if owner_id is not None and f["owner_id"] != owner_id:
                return False, "只能改自己上传的牌堆。", 0
        elif owner_id is not None:
            n = c.execute("SELECT COUNT(*) FROM deck_files WHERE owner_id=?", (owner_id,)).fetchone()[0]
            if n >= MAX_FILES_PER_ROLE:
                return False, f"每人最多上传 {MAX_FILES_PER_ROLE} 份牌堆，先删掉一份不用的。", 0
        names = [n for n in decks if not _is_meta(n)] or list(decks)
        taken = c.execute(f"SELECT d.name, f.title FROM decks d JOIN deck_files f ON f.id=d.file_id WHERE d.file_id!=? AND d.name IN ({','.join('?' * len(names))})",
                          (file_id, *names)).fetchall()
        if taken:
            return False, f"已经有叫「{taken[0]['name']}」的牌堆了（在「{taken[0]['title']}」那份里），换个名字吧。", 0
        title = (title or next((n for n in names if not n.startswith("_")), names[0]))[:30]
        if file_id:
            c.execute("UPDATE deck_files SET raw=?, title=?, updated_ts=? WHERE id=?", (text, title, now, file_id))
            c.execute("DELETE FROM decks WHERE file_id=?", (file_id,))
        else:
            file_id = c.execute("INSERT INTO deck_files(owner_id, title, raw, created_ts, updated_ts) VALUES(?,?,?,?,?)",
                                (owner_id, title, text, now, now)).lastrowid
        for n, cards in decks.items():
            c.execute("INSERT INTO decks(file_id, name, hidden, cards) VALUES(?,?,?,?)",
                      (file_id, n, 1 if n.startswith("_") else 0, json.dumps(cards, ensure_ascii=False)))
    shown = [n for n in names if not n.startswith("_")]
    return True, f"保存好了：{('、'.join(shown) or '（只有辅助牌堆）')}，共 {sum(len(v) for v in decks.values())} 张牌。", file_id


def _blocked(decks: dict[str, list[tuple[int, str]]], owner_id: int | None) -> str:
    """违禁词：跟小手机同一份词表（data/blocklist.txt，没有就用随包带的）。命中返回要告诉管理员的话。"""
    from . import phonegate
    who = "管理员" if owner_id is None else f"role#{owner_id}"
    for name, cards in decks.items():
        if phonegate.blocked_hit(who, "牌堆", name):
            return f"牌堆名「{name}」里有不能发的词，改一改再保存。"
        for i, (_w, t) in enumerate(cards, 1):
            if phonegate.blocked_hit(who, "牌堆", t):
                return f"牌堆「{name}」第 {i} 张牌里有不能发的词，改一改再保存（违禁词表在小手机设置里）。"
    return ""


def _too_nested(decks: dict[str, list[tuple[int, str]]]) -> str:
    """按最坏情况算抽一张要展开几次 {牌堆}（这份里的优先，其次别的文件里的），超过 MAX_EXPAND 返回原因。"""
    memo: dict[tuple[str, int], int] = {}
    others: dict[str, list[tuple[int, str]] | None] = {}

    def cards_of(name):
        if name in decks:
            return decks[name]
        if name not in others:
            others[name] = _cards(name)
        return others[name]

    def worst(name, depth):
        if depth >= MAX_DEPTH:
            return 0
        key = (name, depth)
        if key not in memo:
            memo[key] = 0                    # 先占位：自己套自己时按 0 算，深度上限兜底
            best = 0
            for _w, t in cards_of(name) or []:
                cost = 0
                for m in _REF_RE.finditer(t):
                    cost += 1 + (worst(m.group(1), depth + 1) if cards_of(m.group(1)) else 0)
                    if cost > MAX_EXPAND:
                        break
                best = max(best, cost)
                if best > MAX_EXPAND:
                    break
            memo[key] = best
        return memo[key]

    for name in decks:
        n = worst(name, 0)
        if n > MAX_EXPAND:
            return (f"牌堆「{name}」套得太多：最坏情况下抽一张要展开 {MAX_EXPAND} 次以上 {{…}}（上限 {MAX_EXPAND}）。"
                    "少写几个 {…}，或者少套几层。")         # 算到超过上限就停了，具体数字没意义
    return ""


def delete(file_id: int, owner_id: int | None = None) -> bool:
    """删一份牌堆文件（连同里面的牌堆）。owner_id 给了就只能删自己的；None = 管理员，什么都能删。"""
    with get_conn() as c:
        f = c.execute("SELECT owner_id FROM deck_files WHERE id=?", (file_id,)).fetchone()
        if not f or (owner_id is not None and f["owner_id"] != owner_id):
            return False
        c.execute("DELETE FROM decks WHERE file_id=?", (file_id,))
        c.execute("DELETE FROM deck_files WHERE id=?", (file_id,))
    return True


def files(owner_id: int | None = -1) -> list[dict]:
    """牌堆文件列表（新的在前）。owner_id=-1 全部；None 只要管理员建的；数字 = 这个人的。"""
    sql = "SELECT f.*, r.name AS owner_name FROM deck_files f LEFT JOIN roles r ON r.id=f.owner_id"
    args: tuple = ()
    if owner_id is None:
        sql += " WHERE f.owner_id IS NULL"
    elif owner_id != -1:
        sql += " WHERE f.owner_id=?"
        args = (owner_id,)
    with get_conn() as c:
        out = [dict(r) for r in c.execute(sql + " ORDER BY f.updated_ts DESC, f.id DESC", args)]
        for f in out:
            f["decks"] = [{"name": d["name"], "hidden": d["hidden"], "n": len(json.loads(d["cards"]))}
                          for d in c.execute("SELECT name, hidden, cards FROM decks WHERE file_id=? ORDER BY id", (f["id"],))]
    return out


def get_file(file_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM deck_files WHERE id=?", (file_id,)).fetchone()


def listing() -> list[dict]:
    """能抽的牌堆：[{name, n, owner}]，按名字排。不含 _ 开头的辅助牌堆。"""
    with get_conn() as c:
        rows = c.execute("SELECT d.name, d.cards, r.name AS owner FROM decks d JOIN deck_files f ON f.id=d.file_id "
                         "LEFT JOIN roles r ON r.id=f.owner_id WHERE d.hidden=0 ORDER BY d.name").fetchall()
    return [{"name": r["name"], "n": len(json.loads(r["cards"])), "owner": r["owner"] or "管理员"} for r in rows]


def _cards(name: str) -> list[tuple[int, str]] | None:
    with get_conn() as c:
        r = c.execute("SELECT cards FROM decks WHERE name=?", (name.strip(),)).fetchone()
    return [tuple(x) for x in json.loads(r["cards"])] if r else None


# ---------------- 抽 ----------------

def _pick(cards: list[tuple[int, str]], n: int, rng: random.Random) -> list[str]:
    """按权重抽 n 张，同一次里不重复（权重决定先抽到谁）。"""
    pool = list(cards)
    out = []
    for _ in range(min(n, len(pool))):
        total = sum(w for w, _t in pool)
        x = rng.uniform(0, total)
        for i, (w, t) in enumerate(pool):
            x -= w
            if x <= 0 or i == len(pool) - 1:
                out.append(t)
                pool.pop(i)
                break
    return out


def _roll(expr: str) -> str | None:
    from . import dice
    node = dice.parse_expr(expr.replace("×", "*").replace("x", "*").replace("X", "*"))
    if node is None or not dice.terms_valid(node):
        return None
    return str(dice.roll_terms(node)[1])


def expand(text: str, rng: random.Random, depth: int = 0, budget: list[int] | None = None, dice_need_d: bool = False, keep: tuple[str, ...] = ()) -> str:
    """把一张牌里的 {牌堆} 和 [骰子] 换掉。套太深、找不到的牌堆、超过展开次数的原样留着，不报错。
    budget = [还能展开几次]，同一次抽卡的几张共用一个（默认 MAX_EXPAND）。
    dice_need_d：只把带 d 的 [..] 当骰子（[1d6] 算，[1]、[2+3] 不算）。自定义回复用：旧回复里本来写的方括号不能被算掉。
    keep：这些名字的 {…} 不当牌堆，原样留着（自定义回复的 {角色}）。"""
    budget = [MAX_EXPAND] if budget is None else budget

    def ref(m):
        if depth >= MAX_DEPTH or budget[0] <= 0 or m.group(1).strip() in keep:
            return m.group(0)
        budget[0] -= 1
        cards = _cards(m.group(1))
        if not cards:
            return m.group(0)
        return expand(_pick(cards, 1, rng)[0], rng, depth + 1, budget, dice_need_d, keep)
    text = _REF_RE.sub(ref, text)
    text = _DICE_RE.sub(lambda m: m.group(0) if dice_need_d and not re.search(r"[dD]", m.group(1)) else _roll(m.group(1)) or m.group(0), text)
    return text.replace("\\n", "\n")


def draw(raw: str, rng: random.Random | None = None) -> str:
    """群指令「抽卡」：空 = 列表；「名字 [张数]」= 抽。"""
    from . import dicefun
    rng = rng or random.Random()
    parts = raw.split()
    if not parts:
        ls = listing()
        head = "🃏 能抽的牌堆（发「抽卡 名字」，一次抽几张写「抽卡 名字 3」）：\n"
        body = "\n".join(f"· {d['name']}（{d['n']} 张）" for d in ls[:60])
        more = f"\n……还有 {len(ls) - 60} 个。" if len(ls) > 60 else ""
        builtin = "\n内置：" + "、".join(t.split("（")[0] for t in dicefun.DECK_TITLES.values())
        return head + (body or "（还没有牌堆，管理员在后台「跑团 → 牌堆」里添加）") + more + builtin
    n = 1
    if len(parts) > 1 and re.fullmatch(r"\d{1,3}", parts[-1]):
        n = int(parts.pop())
    name = " ".join(parts)
    if name.lower() in _builtin_names():
        return dicefun.draw(f"{name} {n}")
    if not 1 <= n <= MAX_DRAW:
        return f"一次最多抽 {MAX_DRAW} 张。"
    if name.startswith("_"):
        return f"「{name}」是给别的牌堆用的辅助牌堆，不能直接抽。"
    cards = _cards(name)
    if not cards:
        near = [d["name"] for d in listing() if name.lower() in d["name"].lower() or d["name"].lower() in name.lower()][:5]
        return f"没有叫「{name}」的牌堆。" + (f"是不是：{'、'.join(near)}？" if near else "发「抽卡」看看有哪些。")
    budget = [MAX_EXPAND]
    picks = [expand(t, rng, budget=budget) for t in _pick(cards, n, rng)]
    if len(picks) == 1:
        text = f"🃏 抽卡「{name}」：{picks[0]}"
    else:
        text = f"🃏 抽卡「{name}」×{len(picks)}：\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(picks, 1))
        if len(picks) < n:
            text += f"\n（这个牌堆只有 {len(cards)} 张）"
    return text[:MAX_OUT] + ("……" if len(text) > MAX_OUT else "")
