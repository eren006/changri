"""跑团骰子的规则与文案（不依赖 QQ / NoneBot，可单独测试）。

规则照索罗的做法：rd 通用掷骰；COC7 的 ra / rb / rp / sc / en；DND5e 的 ra / rb / rp（D20 对 DC）、dnd / ri / at。
和索罗不同的两处：①口吻是中性的（索罗那套是拿破仑·索罗的台词，不搬）；②属性在这个系统自己的角色上存，不走存档站。
每个函数返回要回给玩家的一段文字。lookup(name) -> (角色名, 数值) | 提示文字，由调用方提供（读角色属性）。
"""
from __future__ import annotations

import random
import re
from typing import Callable

MAX_COUNT = 100
MAX_SIDES = 1000
MAX_TIMES = 10
MAX_TERMS = 8
MAX_EXTRA_DICE = 5
MAX_TARGET = 999
MAX_DEPTH = 4
MAX_TOKENS = 60
MAX_EXPLODE = 10

Lookup = Callable[[str], "tuple[str, int] | str"]

# ======================== 表达式解析 ========================

# 中文输入法打出的全角符号，进解析前换成半角
FULLWIDTH = str.maketrans("＊＋－（）！＃ｄＤｋＫｈＨｌＬ０１２３４５６７８９", "*+-()!#dDkKhHlL0123456789")
_EXPR_CHARS_RE = re.compile(r"^(?:(\d+)#)?([0-9dDkKhHlL!+\-*×()]+)$")
_TOKEN_RE = re.compile(r"\d+|[dD]|[kK][hHlL]?|!|[-+*×()]")
MOD_RE = re.compile(r"^[+-]\d{1,2}$")

# 语法树节点：("num", n) / ("dice", 个数, 面数, 取高低 None|"h"|"l", 取几个, 是否爆骰) / ("paren", 节点) / ("neg", 节点) / ("bin", op, 左, 右)


class _Parser:
    """递归下降：expr = term (+|- term)*；term = factor (*|× factor)*；factor = 括号 | 骰子 | 数字。"""

    def __init__(self, tokens: list[str]):
        self.t, self.i, self.leaves, self.depth = tokens, 0, 0, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self):
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.factor()
        while self.peek() in ("*", "×"):
            self.take()
            node = ("bin", "*", node, self.factor())
        return node

    def factor(self):
        tok = self.peek()
        if tok == "-":
            self.take()
            return ("neg", self.factor())
        if tok == "(":
            self.take()
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise ValueError
            node = self.expr()
            if self.take() != ")":
                raise ValueError
            self.depth -= 1
            return ("paren", node)
        self.leaves += 1
        if self.leaves > MAX_TERMS:
            raise ValueError
        count = None
        if tok and tok.isdigit():
            self.take()
            count = int(tok)
            if self.peek() not in ("d", "D"):
                return ("num", count)
        elif tok not in ("d", "D"):
            raise ValueError
        self.take()
        sides_tok = self.take()
        if not (sides_tok and sides_tok.isdigit()):
            raise ValueError
        mode, keepn = None, 0
        tok = self.peek()
        if tok and tok[0] in "kK":
            self.take()
            mode = "l" if tok[-1] in "lL" else "h"
            keep_tok = self.take()
            if not (keep_tok and keep_tok.isdigit()):
                raise ValueError
            keepn = int(keep_tok)
        explode = self.peek() == "!"
        if explode:
            self.take()
        return ("dice", 1 if count is None else count, int(sides_tok), mode, keepn, explode)


def parse_expr(expr: str):
    """解析 3d6+2d4-1、(1d6+2)*3、4d6k3、2d20kl1、3d6! 这类表达式，返回语法树；看不懂返回 None。"""
    expr = expr.strip()
    if not expr or not _EXPR_CHARS_RE.match(expr):
        return None
    tokens, pos = [], 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            return None
        tokens.append(m.group())
        pos = m.end()
    if len(tokens) > MAX_TOKENS:
        return None
    parser = _Parser(tokens)
    try:
        node = parser.expr()
    except ValueError:
        return None
    return node if parser.i == len(tokens) else None


def terms_valid(node) -> bool:
    kind = node[0]
    if kind == "num":
        return node[1] <= 100000
    if kind in ("paren", "neg"):
        return terms_valid(node[1])
    if kind == "bin":
        return terms_valid(node[2]) and terms_valid(node[3])
    _, count, sides, mode, keepn, explode = node
    if not (1 <= count <= MAX_COUNT and 1 <= sides <= MAX_SIDES):
        return False
    if mode and not 1 <= keepn <= count:
        return False
    return not explode or sides >= 2


def roll_terms(node) -> tuple[str, int]:
    """掷整个表达式，返回 (过程文字, 结果)。"""
    kind = node[0]
    if kind == "num":
        return str(node[1]), node[1]
    if kind == "paren":
        text, value = roll_terms(node[1])
        return f"({text})", value
    if kind == "neg":
        text, value = roll_terms(node[1])
        return f"-{text}", -value
    if kind == "bin":
        _, op, left, right = node
        lt, lv = roll_terms(left)
        rt, rv = roll_terms(right)
        if op == "+":
            return f"{lt}+{rt}", lv + rv
        if op == "-":
            return f"{lt}-{rt}", lv - rv
        return f"{lt}×{rt}", lv * rv
    _, count, sides, mode, keepn, explode = node
    rolls: list[int] = []
    for _ in range(count):
        r = random.randint(1, sides)
        rolls.append(r)
        extra = 0
        while explode and r == sides and extra < MAX_EXPLODE:
            r = random.randint(1, sides)
            rolls.append(r)
            extra += 1
    kept, dropped = rolls, []
    if mode:
        order = sorted(rolls, reverse=(mode == "h"))
        kept, dropped = order[:keepn], order[keepn:]
    body = "+".join(map(str, kept))
    if dropped:
        body += f"｜弃{','.join(map(str, dropped))}"
    return (f"({body})" if len(rolls) > 1 else body), sum(kept)


def expr_text(node) -> str:
    kind = node[0]
    if kind == "num":
        return str(node[1])
    if kind == "paren":
        return f"({expr_text(node[1])})"
    if kind == "neg":
        return f"-{expr_text(node[1])}"
    if kind == "bin":
        op = "×" if node[1] == "*" else node[1]
        return f"{expr_text(node[2])}{op}{expr_text(node[3])}"
    _, count, sides, mode, keepn, explode = node
    keep = f"K{mode.upper()}{keepn}" if mode else ""
    return f"{count}D{sides}{keep}{'!' if explode else ''}"


def expr_range(node) -> tuple[int, int]:
    """表达式可能取到的 (最小, 最大)；爆骰按每颗最多连爆 MAX_EXPLODE 次估。"""
    kind = node[0]
    if kind == "num":
        return node[1], node[1]
    if kind == "paren":
        return expr_range(node[1])
    if kind == "neg":
        lo, hi = expr_range(node[1])
        return -hi, -lo
    if kind == "bin":
        _, op, left, right = node
        ll, lh = expr_range(left)
        rl, rh = expr_range(right)
        if op == "+":
            return ll + rl, lh + rh
        if op == "-":
            return ll - rh, lh - rl
        products = (ll * rl, ll * rh, lh * rl, lh * rh)
        return min(products), max(products)
    _, count, sides, mode, keepn, explode = node
    n = keepn if mode else count
    return n, n * sides * (MAX_EXPLODE + 1 if explode else 1)


def _plain_sum(node) -> bool:
    kind = node[0]
    if kind == "num":
        return True
    if kind == "paren":
        return _plain_sum(node[1])
    if kind == "bin":
        return node[1] == "+" and _plain_sum(node[2]) and _plain_sum(node[3])
    return kind == "dice" and not node[5]


def double_dice(node):
    """暴击：骰子个数翻倍，固定加值不变。"""
    kind = node[0]
    if kind in ("paren", "neg"):
        return (kind, double_dice(node[1]))
    if kind == "bin":
        return ("bin", node[1], double_dice(node[2]), double_dice(node[3]))
    if kind == "dice":
        _, count, sides, mode, keepn, explode = node
        return ("dice", count * 2, sides, mode, keepn * 2 if mode else 0, explode)
    return node


# ======================== 点评（中性口吻，可以整块换成自己的） ========================

RD_REMARKS = {
    "max": ["满点，漂亮。", "顶格了。", "最大值，运气全在这儿。"],
    "high": ["数字不错。", "偏高，挺好。", "这把站在你这边。"],
    "mid": ["中规中矩。", "不高不低。", "正常发挥。"],
    "low": ["偏低了点。", "不太理想。", "这把有点薄。"],
    "min": ["触底了。", "最小值，下一把会好的。", "碰到谷底，往上就是了。"],
}
LEVEL_REMARKS = {
    "大成功": ["漂亮，干净利落。", "完美。", "无可挑剔。"],
    "极难成功": ["这也行？极其漂亮。", "几乎不可能的事，办到了。", "稳得惊人。"],
    "困难成功": ["险险过关。", "有点勉强，但成了。", "过了，别回头看。"],
    "成功": ["顺利。", "按计划进行。", "稳妥。"],
    "失败": ["没成，还有别的办法。", "差了一点。", "换个思路再来。"],
    "大失败": ["糟了，这下麻烦了。", "彻底搞砸。", "运气到头了，先稳住。"],
}
UPSET = {
    "high_fail": ["这么高的数值还失手，可惜了。", "本来以为稳了。"],
    "low_success": ["底子这么低还成功，全靠运气。", "逆风翻盘。"],
}
MULTI_ROLL = {
    "spread_big": ["起伏很大。", "忽高忽低。"],
    "spread_small": ["数字很接近，很稳定。", "几轮差不多。"],
    "all_same": ["每轮结果一样，罕见。", "同一个数连着出现。"],
}
DND_REMARKS = {
    "nat20": ["自然 20，漂亮。", "二十点满，这把写进回忆里。"],
    "nat1": ["自然 1，运气不在这边。", "一点，命运开了个玩笑。"],
    "pass": ["够了，往前走。", "数字站在你这边。"],
    "fail": ["差了一口气。", "没够上，想想别的办法。"],
}


def _pick(pool: dict, key: str) -> str:
    return random.choice(pool[key])


def _rd_remark(node, total: int) -> str:
    if not _plain_sum(node):
        return ""
    lo, hi = expr_range(node)
    if hi <= lo:
        return ""
    ratio = (total - lo) / (hi - lo)
    key = "max" if total >= hi else "min" if total <= lo else "high" if ratio >= 0.8 else "low" if ratio <= 0.2 else "mid"
    return _pick(RD_REMARKS, key)


# ======================== rd：通用掷骰 ========================

def rd(text: str) -> str:
    text = text.strip()
    first, _, rest = text.partition(" ")
    first = first.translate(FULLWIDTH)
    times, terms, reason = 1, None, text
    m = _EXPR_CHARS_RE.match(first) if first else None
    if m and (first[0].isdigit() or first[0] in "dD("):
        parsed = parse_expr(m.group(2))
        if parsed is not None:
            if first.isdigit():          # 裸数字按面数处理：/rd 100 = D100
                parsed = ("dice", 1, int(first), None, 0, False)
            terms, reason = parsed, rest.strip()
            times = int(m.group(1)) if m.group(1) else 1
    if terms is None:
        terms = ("dice", 1, 100, None, 0, False)
    if not terms_valid(terms):
        return f"数目超出范围：骰子 1~{MAX_COUNT} 个，面数 1~{MAX_SIDES}。"
    if not 1 <= times <= MAX_TIMES:
        return f"一次最多掷 {MAX_TIMES} 轮。"
    et = expr_text(terms)
    head = f"{reason}：" if reason else ""
    if times == 1:
        detail, total = roll_terms(terms)
        simple = terms[0] == "dice" and terms[1] == 1 and not terms[3] and not terms[5]
        body = f"D{terms[2]} = {total}" if simple else f"{et} = {detail} = {total}"
        remark = _rd_remark(terms, total)
        return f"{head}{body}" + (f"。{remark}" if remark else "")
    lines, totals = [f"{head}{et} 掷 {times} 轮："], []
    for i in range(1, times + 1):
        detail, total = roll_terms(terms)
        totals.append(total)
        lines.append(f"  第{i}轮 {total}" if detail == str(total) else f"  第{i}轮 {detail} = {total}")
    tail = (_pick(MULTI_ROLL, "all_same") if len(set(totals)) == 1
            else _pick(MULTI_ROLL, "spread_big") if max(totals) - min(totals) >= 15 else _pick(MULTI_ROLL, "spread_small"))
    lines.append(f"最高 {max(totals)}，最低 {min(totals)}，合计 {sum(totals)}。{tail}")
    return "\n".join(lines)


# ======================== COC7 ========================

LEVEL_RANK = {"大失败": 0, "失败": 1, "成功": 2, "困难成功": 3, "极难成功": 4, "大成功": 5}
_DIFFICULTY = {"困难": ("困难成功", 2), "极难": ("极难成功", 5)}
_BP_TOKEN_RE = re.compile(r"^([bBpP])(\d?)$")


# 房规（.setcoc）：大成功 / 大失败的范围。键是房规编号，值说明在 HOUSE_RULES；默认 1（和以前一致）。
HOUSE_RULES = {
    "0": "规则书：出1大成功；不满50出96-100大失败，满50出100大失败",
    "1": "不满50出1大成功、满50出1-5大成功；不满50出96-100大失败，满50出100大失败（默认）",
    "2": "出1-5且≤成功率大成功；出100，或出96-99且>成功率大失败",
    "3": "出1-5大成功；出96-100大失败",
    "4": "出1-5且≤成功率/10大成功；不满50出≥96+成功率/10大失败，满50出100大失败",
    "5": "出1-2且≤成功率/5大成功；不满50出96-100大失败，满50出99-100大失败",
}
DEFAULT_HOUSE = "1"


def is_crit(roll: int, target: int, house: str = DEFAULT_HOUSE) -> bool:
    if house == "0":
        return roll == 1
    if house == "2":
        return roll <= 5 and roll <= target
    if house == "3":
        return roll <= 5
    if house == "4":
        return roll <= 5 and roll <= target // 10
    if house == "5":
        return roll <= 2 and roll <= target // 5
    return roll == 1 or (target >= 50 and roll <= 5)


def is_fumble(roll: int, target: int, house: str = DEFAULT_HOUSE) -> bool:
    if house == "2":
        return roll == 100 or (roll >= 96 and roll > target)
    if house == "3":
        return roll >= 96
    if house == "4":
        return roll == 100 if target >= 50 else roll >= min(96 + target // 10, 100)
    if house == "5":
        return roll >= 99 if target >= 50 else roll >= 96
    return (target < 50 and roll >= 96) or (target >= 50 and roll == 100)      # 0 和 1 的大失败相同


def judge_success(roll: int, target: int, house: str = DEFAULT_HOUSE) -> str:
    """COC7 成功等级；大成功 / 大失败的范围按房规（默认 1：属性<50 时 1 大成功、96-100 大失败；>=50 时 1-5 大成功、100 大失败）。"""
    if is_crit(roll, target, house):
        return "大成功"
    if is_fumble(roll, target, house):
        return "大失败"
    if roll <= target // 5:
        return "极难成功"
    if roll <= target // 2:
        return "困难成功"
    return "成功" if roll <= target else "失败"


def house_text(house: str) -> str:
    lines = [f"{k}　{v}" for k, v in HOUSE_RULES.items()]
    return f"当前房规 {house}：{HOUSE_RULES.get(house, '')}\n\n可选：\n" + "\n".join(lines)


def roll_d100(extra: int) -> tuple[int, str]:
    """extra>0 奖励骰（取小），<0 惩罚骰（取大）。返回 (结果, 说明)。"""
    units = random.randint(0, 9)
    tens = [random.randint(0, 9) for _ in range(abs(extra) + 1)]
    values = [(t * 10 + units) or 100 for t in tens]
    if extra == 0:
        return values[0], ""
    chosen = min(values) if extra > 0 else max(values)
    kind = "奖励骰" if extra > 0 else "惩罚骰"
    return chosen, f"（{kind}×{abs(extra)}：{'/'.join(map(str, values))} 取{chosen}）"


COC_USAGE = ("格式：/ra [b奖励/p惩罚] [困难|极难] [技能名] 数值\n例：/ra 侦查 70、/ra b2 困难 射击 60、/rb 70、/rp 45\n"
             "也可以直接写角色属性名，例如 /ra 力量（先 /录属性 力量60）")


def coc_check(raw: str, extra: int, lookup: Lookup, proxy: bool = False, house: str = DEFAULT_HOUSE) -> str:
    tokens = raw.strip().split()
    difficulty = None
    while tokens:
        t = tokens[0]
        bp = _BP_TOKEN_RE.match(t)
        if bp:
            n = int(bp.group(2)) if bp.group(2) else 1
            extra += n if bp.group(1) in "bB" else -n
            tokens.pop(0)
        elif t in _DIFFICULTY:
            difficulty = t
            tokens.pop(0)
        else:
            break
    rest = " ".join(tokens)
    m = re.match(r"^(.*?)\s*(\d{1,3})$", rest)
    note_cap = ""
    if m:
        label, target = m.group(1).strip(), int(m.group(2))
    else:
        if not rest:
            return COC_USAGE
        got = lookup(rest)
        if isinstance(got, str):
            return got
        role, val = got
        label, target = rest, min(val, 100)
        if target < 1:
            return f"「{role}」的{label}是 {val}，掷不出成功。"
        note_cap = f"（{role}的{label}{val}，按100算）" if val > 100 else (f"（代{role}投，{label}{val}）" if proxy else f"（{role}的{label}{val}）")
    if not 1 <= target <= MAX_TARGET:
        return "数值有误，请再核对一下。"
    if abs(extra) > MAX_EXTRA_DICE:
        return f"奖励/惩罚骰最多 {MAX_EXTRA_DICE} 颗。"
    roll, note = roll_d100(extra)
    level = judge_success(roll, target, house)
    shown, verdict, key = str(target), level, level
    if difficulty:
        need_level, div = _DIFFICULTY[difficulty]
        shown = f"{target}（{difficulty}要求≤{target // div}）"
        if level not in ("大成功", "大失败"):
            met = LEVEL_RANK[level] >= LEVEL_RANK[need_level]
            verdict, key = f"{difficulty}{'成功' if met else '失败'}", "成功" if met else "失败"
    remark = _pick(LEVEL_REMARKS, key)
    ok = LEVEL_RANK[level] >= LEVEL_RANK["成功"]
    if (not ok and target >= 70) and random.random() < 0.6:
        remark = _pick(UPSET, "high_fail")
    elif (ok and target <= 30) and random.random() < 0.6:
        remark = _pick(UPSET, "low_success")
    prefix = f"{label} " if label else ""
    return f"{prefix}检定：D100={roll}/{shown}{note_cap}{note} —— {verdict}。{remark}"


def _roll_loss(expr: str):
    terms = parse_expr(expr)
    if terms is None or not terms_valid(terms):
        return None
    detail, total = roll_terms(terms)
    return max(total, 0), max(expr_range(terms)[1], 0), detail


def coc_sanity(raw: str, lookup: Lookup, house: str = DEFAULT_HOUSE) -> str:
    m = re.match(r"^(\S+?)/(\S+?)(?:\s+(\d{1,3}))?$", raw.strip())
    if not m:
        return "格式：/sc 成功损失/失败损失 [当前理智]\n例：/sc 1/1d6 60；不写理智就取你角色的 SAN（先 /录属性 SAN60）"
    note = ""
    if m.group(3):
        san = int(m.group(3))
    else:
        got = lookup("SAN")
        if isinstance(got, str):
            return got
        san = got[1]
        note = f"（取自{got[0]}的 SAN，扣完不会自动写回，请自己 /录属性 SAN{{新值}}）"
    ok_loss, bad_loss = _roll_loss(m.group(1)), _roll_loss(m.group(2))
    if ok_loss is None or bad_loss is None:
        return "损失的写法看不懂，数字或 1d6 这类即可。"
    roll = random.randint(1, 100)
    fumble = is_fumble(roll, san, house)
    passed = roll <= san and not fumble
    if passed:
        loss, process, verdict = ok_loss[0], ok_loss[2], "成功"
    elif fumble:
        loss, process, verdict = bad_loss[1], "大失败，取最大值", "大失败"
    else:
        loss, process, verdict = bad_loss[0], bad_loss[2], "失败"
    left = max(san - loss, 0)
    tail = "……请先坐下。" if verdict == "大失败" else ("还撑得住。" if passed else "失去的总得想办法找回来。")
    return f"理智检定：D100={roll}/{san} —— {verdict}\n损失 {loss}（{process}），理智 {san} → {left}。{tail}{note}"


def coc_growth(raw: str) -> str:
    m = re.match(r"^(.*?)\s*(\d{1,3})$", raw.strip())
    if not m:
        return "格式：/en [技能名] 当前数值\n例：/en 侦查 55"
    label, value = m.group(1).strip(), int(m.group(2))
    roll = random.randint(1, 100)
    prefix = f"{label} " if label else ""
    if roll > value or roll > 95:
        gain = random.randint(1, 10)
        return f"{prefix}成长检定：D100={roll}/{value} —— 成长！+{gain}（1D10），{value} → {value + gain}。"
    return f"{prefix}成长检定：D100={roll}/{value} —— 没有成长。经验急不得。"


# ---------- 疯狂症状（COC7 第 8 章，文字是概括的）----------

TEMP_MADNESS = [
    "失忆：发现自己身处陌生的地方，忘了自己是谁，记忆在持续时间结束后恢复",
    "假性残疾：心理性的失明、失聪或肢体失去知觉",
    "暴力倾向：六亲不认地攻击周围的人和物",
    "偏执：陷入严重的偏执妄想，觉得有人监视、同伴要背叛，谁也不能信",
    "人际依赖：把别人误认成背景里的重要之人，努力维持关系",
    "昏厥：当场昏倒，被唤醒后能正常行动",
    "逃避行为：不择手段地逃离所在的地方，哪怕抛下同伴",
    "歇斯底里：大笑、哭泣、嘶吼、恐惧等极端情绪爆发",
    "恐惧：患上一种恐惧症，持续时间内对恐惧源有反应（恐惧症状表请掷 1D100 或由 KP 选）",
    "狂躁：出现一种狂躁，持续时间内沉浸其中（狂躁症状表请掷 1D100 或由 KP 选）",
]
SUMMARY_MADNESS = [
    "失忆：回过神来身处陌生的地方，忘了自己是谁，记忆会随时间恢复",
    "被窃：恢复清醒后发现被盗，人毫发无损；带着贵重物品的话做幸运检定，有价值的东西可能全部消失",
    "遍体鳞伤：满身拳痕和瘀伤，生命值降到疯狂前的一半（不算重伤），没有被盗",
    "暴力倾向：陷入暴力和破坏欲，事后可能记得也可能毫无印象，对谁施暴、后果如何由 KP 定",
    "极端信念：以疯狂的方式表现背景里的某个思想或信念",
    "重要之人：不顾一切接近背景里的重要之人，并为这段关系做出行动",
    "被收容：在精神病院病房或警察局牢房里醒来，会慢慢想起原因",
    "逃避行为：醒来发现自己在很远的地方，也许迷失在荒野，或是在远行的车上",
    "恐惧：患上一种新的恐惧症（恐惧症状表请掷 1D100 或由 KP 选），会设法避开恐惧源",
    "狂躁：患上一种新的狂躁（狂躁症状表请掷 1D100 或由 KP 选），沉浸其中",
]


def madness(raw: str, summary: bool) -> str:
    """ti：临时疯狂（持续 1D10 轮）；li：总结疯狂（持续 1D10 小时）。raw 可以写「某某」当作被疯狂的人。"""
    who = raw.strip()
    idx = random.randint(1, 10)
    last = random.randint(1, 10)
    table = SUMMARY_MADNESS if summary else TEMP_MADNESS
    unit = "小时" if summary else "轮"
    kind = "总结疯狂（li）" if summary else "临时疯狂（ti）"
    head = f"{who} 的{kind}" if who else kind
    return f"{head}：1D10={idx}\n{table[idx - 1]}\n持续 1D10={last} {unit}。"


# ======================== DND5e ========================

def ability_mod(score: int) -> int:
    return (score - 10) // 2


def dnd_check(raw: str, adv: int, lookup: Lookup) -> str:
    mod, dc, labels = 0, None, []
    for t in raw.split():
        if t.lower() in ("优势", "adv"):
            adv = 1
        elif t.lower() in ("劣势", "dis"):
            adv = -1
        elif MOD_RE.match(t):
            mod += int(t)
        elif t.isdigit() and dc is None:
            dc = int(t)
        else:
            labels.append(t)
    if abs(mod) > 30 or (dc is not None and dc > 60):
        return "修正最多 ±30，DC 最多 60，再核对一下。"
    label = " ".join(labels)
    card_note = ""
    if label:
        got = lookup(label)
        if not isinstance(got, str):       # 没有这个属性就当普通检定名，不报错
            role, val = got
            cm = ability_mod(val)
            mod += cm
            card_note = f"（{role}的{label}{val}→{cm:+d}）"
    rolls = [random.randint(1, 20) for _ in range(2 if adv else 1)]
    chosen = max(rolls) if adv >= 0 else min(rolls)
    total = chosen + mod
    note = f"（{'优势' if adv > 0 else '劣势'}：{'/'.join(map(str, rolls))} 取{chosen}）" if adv else ""
    prefix = f"{label} " if label else ""
    mod_text = f"{mod:+d}" if mod else ""
    line = f"{prefix}检定：D20={chosen}{mod_text}{'=' + str(total) if mod else ''}{card_note}{note}"
    key = ""
    if dc is not None:
        passed = total >= dc
        line += f" vs DC{dc} —— {'通过' if passed else '未通过'}"
        key = "pass" if passed else "fail"
    if chosen == 20:
        line += "（自然20）"; key = "nat20"
    elif chosen == 1:
        line += "（自然1）"; key = "nat1"
    return line + (f"。{_pick(DND_REMARKS, key)}" if key else "")


def dnd_stats(raw: str) -> str:
    text = raw.strip()
    times = int(text) if text.isdigit() else 1
    if not 1 <= times <= 5:
        return "一次最多出 5 组。"
    lines = ["属性生成（4D6 取高 3，共 6 项，顺序随你分配）："]
    for i in range(1, times + 1):
        scores = sorted((sum(sorted(random.randint(1, 6) for _ in range(4))[1:]) for _ in range(6)), reverse=True)
        lines.append(f"第{i}组 " + " ".join(f"{v}({ability_mod(v):+d})" for v in scores) + f"，合计 {sum(scores)}")
    return "\n".join(lines)


def initiative(table: dict, raw: str) -> str:
    """table 是这个群的先攻表 {名字: (总值, 修正)}，原地修改。"""
    tokens = raw.split()
    if tokens and tokens[0] in ("清空", "重置", "clear"):
        table.clear()
        return "先攻表清掉了，下一场。"
    head = ""
    if tokens:
        mod = sum(int(t) for t in tokens if MOD_RE.match(t))
        name = " ".join(t for t in tokens if not MOD_RE.match(t)) or "无名氏"
        if abs(mod) > 30 or (len(table) >= 30 and name not in table):
            return "修正最多 ±30，先攻表最多 30 个。"
        roll = random.randint(1, 20)
        table[name] = (roll + mod, mod)
        head = f"{name} 先攻：D20={roll}{f'{mod:+d}' if mod else ''}={roll + mod}。\n"
    if not table:
        return "先攻表是空的。/ri 名字 +修正 加入，/ri 清空 重来。"
    order = sorted(table.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
    return head + "先攻顺序：\n" + "\n".join(f"{i}. {n} {v[0]}" for i, (n, v) in enumerate(order, 1))


def dnd_attack(raw: str) -> str:
    usage = "格式：/at [名字] +命中 AC 伤害 [优势|劣势]\n例：/at 长剑 +5 15 1d8+3"
    mod, ac, dmg, adv, labels = 0, None, None, 0, []
    for t in raw.split():
        if t.lower() in ("优势", "adv"):
            adv = 1
        elif t.lower() in ("劣势", "dis"):
            adv = -1
        elif MOD_RE.match(t):
            mod += int(t)
        elif t.isdigit() and ac is None:
            ac = int(t)
        elif dmg is None and re.search(r"[dD]", t) and (node := parse_expr(t)) is not None:
            dmg = node
        else:
            labels.append(t)
    if ac is None or dmg is None or ac > 60 or abs(mod) > 30 or not terms_valid(dmg):
        return usage
    rolls = [random.randint(1, 20) for _ in range(2 if adv else 1)]
    chosen = max(rolls) if adv >= 0 else min(rolls)
    total = chosen + mod
    note = f"（{'优势' if adv > 0 else '劣势'}：{'/'.join(map(str, rolls))} 取{chosen}）" if adv else ""
    label = " ".join(labels)
    line = f"{label + ' ' if label else ''}攻击：D20={chosen}{f'{mod:+d}' if mod else ''}={total}{note} vs AC{ac}"
    crit = chosen == 20
    if chosen == 1:
        return f"{line} —— 自然1，必定失手。"
    if not crit and total < ac:
        return f"{line} —— 未命中。{_pick(DND_REMARKS, 'fail')}"
    hit = double_dice(dmg) if crit else dmg
    if not terms_valid(hit):
        hit, crit = dmg, False
    detail, value = roll_terms(hit)
    value = max(value, 0)
    return f"{line} —— {'自然20，暴击！骰子翻倍' if crit else '命中'}。\n伤害：{expr_text(hit)} = {detail} = {value}"


# ======================== 帮助 ========================

HELP_SWITCH = "【切换规则】/切换规则 COC 或 DND，每个群各记各的；不带参数看当前规则。"
HELP_RD = (
    "【通用掷骰 /rd】两种规则都能用\n"
    "/rd 3d6+2d4-1　加减、常数\n"
    "/rd 2d6×3、/rd (1d6+2)×2　乘法、括号\n"
    "/rd 4d6k3　取最高3颗(k=kh)；/rd 2d20kl1　取最低1颗\n"
    "/rd 3d6!　爆骰：掷到最大面再掷，每颗最多连爆10次\n"
    "/rd 5#2d6　连掷5轮(最多10轮)；/rd 100　等于D100\n"
    "/rd 伤害 2d6+3　末尾空格后可写原因；不写表达式默认D100\n"
    f"上限：骰子1~{MAX_COUNT}颗、面数1~{MAX_SIDES}、最多{MAX_TERMS}项、括号{MAX_DEPTH}层。"
)
HELP_COC = (
    "【COC7 规则】/切换规则 COC\n"
    "/ra [技能名] 数值　D100检定，如 /ra 侦查 70\n"
    "/ra b2 困难 射击 60　b=奖励骰、p=惩罚骰(可带数字，最多5颗)，困难/极难要求更高\n"
    "/rb 70　奖励骰检定；/rp 45　惩罚骰检定\n"
    "/sc 成功损失/失败损失 当前理智　如 /sc 1/1d6 60\n"
    "/en [技能名] 当前数值　技能成长，如 /en 侦查 55\n"
    "不写数值就取你角色的同名属性：/ra 力量（要先 /录属性 力量60，超过100按100算）\n"
    "代骰：/ra 力量 @某人，取被 @ 者角色的数值替 TA 投\n"
    "/sc 1/1d6 不写理智就取你的 SAN\n"
    "/ti　临时疯狂症状；/li　总结疯狂症状（后面可以写人名）\n"
    "/setcoc [0-5]　房规：改大成功/大失败的范围，不带数字看说明；每个群各记各的\n"
    "/rh [表达式]　暗骰，结果私下发给你，群里只说一声；/rah 技能 数值　暗检定"
)
HELP_DND = (
    "【DND5e 规则】/切换规则 DND\n"
    "/ra [名字] [+修正] [DC]　D20+修正对难度，如 /ra 侦察 +3 15；DC可不写\n"
    "/rb 优势、/rp 劣势　掷2颗D20取高/取低；/ra 后面直接写 优势/劣势 也行\n"
    "/dnd [组数]　属性生成：6项各4D6取高3，一次最多5组\n"
    "/ri 名字 +修正　先攻；/ri 看顺序；/ri 清空 重置(程序重启也会清空)\n"
    "/at [名字] +命中 AC 伤害 [优势|劣势]　如 /at 长剑 +5 15 1d8+3\n"
    "　自然20必中且伤害骰翻倍，自然1必失手\n"
    "不写修正：/ra 力量 会取你角色的力量，自动换算成调整值（(力量-10)÷2，向下取整）\n"
    "/sc 和 /en 是 COC 的指令，DND 规则下用不了"
)
HELP_ATTR = (
    "【角色属性】\n"
    "/录属性 力量60 敏捷70 SAN55　记下（同名会覆盖；也可以写 力量 60）\n"
    "/属性　看自己的；/删属性 力量；/清空属性\n"
    "属性存在你的角色上，检定时直接写属性名就会取用。"
)
RULE_TITLE = {"coc": "COC7", "dnd": "DND5e"}
RULE_NAMES = {"coc": "coc", "coc7": "coc", "克苏鲁": "coc", "dnd": "dnd", "dnd5e": "dnd", "5e": "dnd", "龙与地下城": "dnd"}


def help_text(topic: str, rule: str) -> str:
    sections = {"rd": HELP_RD, "coc": HELP_COC, "dnd": HELP_DND, "规则": HELP_SWITCH, "切换规则": HELP_SWITCH, "属性": HELP_ATTR}
    t = topic.strip().lower()
    if t in sections:
        return sections[t]
    return (f"骰子（这里现在是 {RULE_TITLE.get(rule, rule)} 规则）\n\n{HELP_SWITCH}\n\n{HELP_RD}\n\n{HELP_COC}\n\n{HELP_DND}\n\n{HELP_ATTR}\n\n"
            "只看某一块：/骰子帮助 rd、coc、dnd、属性")
