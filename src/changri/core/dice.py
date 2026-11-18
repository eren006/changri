"""跑团骰子的规则与文案（不依赖 QQ / NoneBot，可单独测试）。

规则照索罗的做法：rd 通用掷骰；COC7 的 ra / rb / rp / sc / en；DND5e 的 ra / rb / rp（D20 对 DC）、dnd / ri / at。
和索罗不同的两处：①口吻是中性的（索罗那套是拿破仑·索罗的台词，不搬）；②属性在这个系统自己的角色上存，不走存档站。
每个函数返回要回给玩家的一段文字。lookup(name) -> (角色名, 数值[, 附注]) | 提示文字，由调用方提供（读角色属性）；
附注（如「，初始值」）会跟在检定说明里。
"""
from __future__ import annotations

import json
import random
import re
from typing import Callable

from . import dicecard

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


class Rolled(str):
    """回给玩家的文字。本身就是纯文字（str），另带 .card（Markdown 小卡片，发不了卡片时用文字）和一些结果字段
    （理智检定的 san_left、成长检定的 gain 等，插件拿来写回角色属性）。"""
    card: str | None = None

    def __new__(cls, text: str, card: str | None = None, **extra):
        self = super().__new__(cls, text)
        self.card = card
        for k, v in extra.items():
            setattr(self, k, v)
        return self

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
SAN_REMARKS = {
    "pass": ["还撑得住。"],
    "fail": ["失去的总得想办法找回来。"],
    "fumble": ["……请先坐下。"],
}
GROWTH_REMARKS = {
    "grow": ["经验积累，终于开窍了。"],
    "none": ["经验急不得。"],
}


# 后台「个性化骰子」可以改上面每一组的句子（一组可以写好几句，掷骰时随机挑一句；清空 = 恢复成这里的默认）。
# 改过的存在设置 dice_lines（JSON：{"coc.大成功": ["…", "…"], …}），没改过的照旧用上面的默认。
POOLS = {"coc": LEVEL_REMARKS, "upset": UPSET, "rd": RD_REMARKS, "multi": MULTI_ROLL, "dnd": DND_REMARKS,
         "sc": SAN_REMARKS, "en": GROWTH_REMARKS}
_POOL_OF = {id(v): k for k, v in POOLS.items()}
LINE_GROUPS = [
    ("COC / 绿色三角洲 检定（/ra）", "coc", [("大成功", "大成功"), ("极难成功", "极难成功"), ("困难成功", "困难成功"),
                                        ("成功", "成功"), ("失败", "失败"), ("大失败", "大失败")]),
    ("意外结果（COC 检定，有六成机会替换上面那句）", "upset", [("high_fail", "数值 ≥70 却失败"), ("low_success", "数值 ≤30 却成功")]),
    ("理智检定（/sc）", "sc", [("pass", "通过"), ("fail", "没通过"), ("fumble", "大失败")]),
    ("成长检定（/en）", "en", [("grow", "成长了"), ("none", "没有成长")]),
    ("通用掷骰（/rd）", "rd", [("max", "掷出最大值"), ("high", "偏高"), ("mid", "中间"), ("low", "偏低"), ("min", "掷出最小值")]),
    ("连掷几轮（/rd 3#…）", "multi", [("all_same", "每轮一样"), ("spread_big", "起伏大"), ("spread_small", "很接近")]),
    ("DND 检定 / 攻击", "dnd", [("nat20", "自然 20"), ("nat1", "自然 1"), ("pass", "通过 DC"), ("fail", "没通过 / 没命中")]),
]
LINES_KEY = "dice_lines"


def custom_lines() -> dict[str, list[str]]:
    """后台改过的台词 {"coc.大成功": [...]}；读不出来当没改过。"""
    from .db import get_config
    try:
        data = json.loads(get_config(LINES_KEY, "") or "{}")
    except ValueError:
        return {}
    return {k: [str(x) for x in v] for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}


def save_custom_lines(data: dict[str, list[str]]) -> None:
    """只存和默认不一样的；整组和默认一样、或者清空了，就当没改过（用默认）。"""
    from .db import set_config
    out = {}
    for k, lines in data.items():
        pool, _, key = k.partition(".")
        if pool in POOLS and key in POOLS[pool] and lines and lines != POOLS[pool][key]:      # 空的 = 用默认
            out[k] = lines
    set_config(LINES_KEY, json.dumps(out, ensure_ascii=False) if out else "")


def _pick(pool: dict, key: str) -> str:
    lines = custom_lines().get(f"{_POOL_OF.get(id(pool), '')}.{key}") or pool[key]
    return random.choice(lines) if lines else ""


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

def _rd_token(tok: str):
    """一个词能不能当 rd 表达式：能就返回 (轮数, 语法树)，不能返回 None。"""
    tok = tok.translate(FULLWIDTH)
    m = _EXPR_CHARS_RE.match(tok) if tok else None
    if not (m and (tok[0].isdigit() or tok[0] in "dD(")):
        return None
    parsed = parse_expr(m.group(2))
    if parsed is None:
        return None
    if tok.isdigit():                    # 裸数字按面数处理：/rd 100 = D100
        parsed = ("dice", 1, int(tok), None, 0, False)
    return (int(m.group(1)) if m.group(1) else 1), parsed


def rd(text: str) -> str:
    text = text.strip()
    times, terms, reason = 1, None, text
    first, _, rest = text.partition(" ")
    got = _rd_token(first)
    if got:
        (times, terms), reason = got, rest.strip()
    else:
        # 原因写在前面：/rd 伤害 2d6+3。末尾那个词要带 d 才算表达式，免得「/rd 第 3 轮」被当成 D3
        head, _, last = text.rpartition(" ")
        if head and re.search(r"[dDｄＤ]", last) and (got := _rd_token(last)):
            (times, terms), reason = got, head.strip()
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
        card = dicecard.rd_card(reason, f"D{terms[2]}" if simple else et, detail, total, remark, simple)
        return Rolled(f"{head}{body}" + (f"。{remark}" if remark else ""), card)
    lines, totals, rounds = [f"{head}{et} 掷 {times} 轮："], [], []
    for i in range(1, times + 1):
        detail, total = roll_terms(terms)
        totals.append(total)
        rounds.append((detail, total))
        lines.append(f"  第{i}轮 {total}" if detail == str(total) else f"  第{i}轮 {detail} = {total}")
    tail = (_pick(MULTI_ROLL, "all_same") if len(set(totals)) == 1
            else _pick(MULTI_ROLL, "spread_big") if max(totals) - min(totals) >= 15 else _pick(MULTI_ROLL, "spread_small"))
    lines.append(f"最高 {max(totals)}，最低 {min(totals)}，合计 {sum(totals)}。{tail}")
    return Rolled("\n".join(lines), dicecard.rd_multi_card(reason, et, rounds, tail))


# ======================== COC7 ========================

LEVEL_RANK = {"大失败": 0, "失败": 1, "成功": 2, "困难成功": 3, "极难成功": 4, "大成功": 5}
_DIFFICULTY = {"困难": ("困难成功", 2), "极难": ("极难成功", 5)}
_BP_TOKEN_RE = re.compile(r"^([bBpP])(\d?)$")
_COC_MOD_RE = re.compile(r"\s*([+-]\s*\d{1,3})$")


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


def _coc_parse(raw: str, extra: int, lookup: Lookup, proxy: bool):
    """解析 /ra 的参数。看不懂返回要回给玩家的文字（str），解析好了返回字段 dict。"""
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
    # 临时修正：侦查+10、侦查 -20、侦查70+10（可以连写几个）
    mod = 0
    while (mm := _COC_MOD_RE.search(rest)) and mm.start() > 0:
        mod += int(mm.group(1).replace(" ", ""))
        rest = rest[:mm.start()].rstrip()
    if abs(mod) > MAX_TARGET:
        return "修正值太大了，请再核对一下。"
    if re.fullmatch(r"[+-]\s*\d*", rest):          # 只剩一个修正、没有技能名也没有数值
        return COC_USAGE
    mod_text = f"{mod:+d}" if mod else ""
    m = re.match(r"^(.*?)\s*(\d{1,3})$", rest)
    note_cap = ""
    if m:
        label, base = m.group(1).strip(), int(m.group(2))
        target = base + mod
        if mod:
            note_cap = f"（{base}{mod_text}）"
    else:
        if not rest:
            return COC_USAGE
        got = lookup(rest)
        if isinstance(got, str):
            return got
        role, val = got[:2]
        tag = got[2] if len(got) > 2 else ""
        label, target = rest, min(val + mod, 100)
        if val + mod > 100:
            note_cap = f"（{role}的{label}{val}{mod_text}{tag}，按100算）"
        elif proxy:
            note_cap = f"（代{role}投，{label}{val}{mod_text}{tag}）"
        else:
            note_cap = f"（{role}的{label}{val}{mod_text}{tag}）"
        if target < 1:
            return f"「{role}」的{label}是 {val}{mod_text}，掷不出成功。"
    if mod and target < 1:
        return f"{label}加上修正只剩 {target}，掷不出成功。"
    if not 1 <= target <= MAX_TARGET:
        return "数值有误，请再核对一下。"
    if abs(extra) > MAX_EXTRA_DICE:
        return f"奖励/惩罚骰最多 {MAX_EXTRA_DICE} 颗。"
    return {"label": label, "target": target, "extra": extra, "difficulty": difficulty, "note_cap": note_cap, "from_card": not m}


def _coc_roll(spec: dict, house: str) -> Rolled:
    label, target, difficulty, note_cap = spec["label"], spec["target"], spec["difficulty"], spec["note_cap"]
    roll, note = roll_d100(spec["extra"])
    level = judge_success(roll, target, house)
    shown, verdict, key, need = str(target), level, level, ""
    if difficulty:
        need_level, div = _DIFFICULTY[difficulty]
        need = f"{difficulty}要求≤{target // div}"
        shown = f"{target}（{need}）"
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
    plain = f"{prefix}检定：D100={roll}/{shown}{note_cap}{note} —— {verdict}。{remark}"
    card = dicecard.check_card(label, roll, target, verdict, remark, need=need, dice_note=note.strip("（）"), cap_note=note_cap.strip("（）"))
    return Rolled(plain, card, roll=roll, level=level, target=target, verdict=verdict, label=label, from_card=spec.get("from_card", False))


def coc_check(raw: str, extra: int, lookup: Lookup, proxy: bool = False, house: str = DEFAULT_HOUSE) -> str:
    """/ra。前面写「3#」连掷几次（/ra 3#侦查 60），一次最多 MAX_TIMES 次。"""
    raw = raw.strip()
    times = 1
    m = re.match(r"^(\d{1,3})#\s*(.*)$", raw)
    if m:
        times, raw = int(m.group(1)), m.group(2)
        if not 1 <= times <= MAX_TIMES:
            return f"一次最多连掷 {MAX_TIMES} 次。"
    spec = _coc_parse(raw, extra, lookup, proxy)
    if isinstance(spec, str):
        return spec
    if times == 1:
        return _coc_roll(spec, house)
    results = [_coc_roll(spec, house) for _ in range(times)]
    lines = [f"第{i}次 " + str(r).split("检定：", 1)[1] for i, r in enumerate(results, 1)]
    wins = sum(1 for r in results if r.verdict.endswith("成功"))
    head = f"{spec['label'] + ' ' if spec['label'] else ''}连续检定 ×{times}（{spec['target']}）："
    return Rolled(head + "\n" + "\n".join(lines) + f"\n成功 {wins} 次，失败 {times - wins} 次。",
                  label=spec["label"], verdict="成功" if wins else "失败", from_card=spec.get("from_card", False))


def coc_oppose(raw: str, lookup: Lookup, house: str = DEFAULT_HOUSE) -> str:
    """/rav 甲 vs 乙：各掷一次，比成功等级，等级一样比数值，数值也一样算平局。
    每一边的写法和 /ra 一样（侦查 70、b 力量 60、困难 …），例：/rav 力量 60 vs 力量 50。"""
    parts = re.split(r"\s+(?:vs|VS|Vs|对|对抗)\s+|\s*(?:vs|VS)\s*", raw.strip(), maxsplit=1)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return "格式：/rav 甲方检定 vs 乙方检定\n例：/rav 力量 60 vs 力量 50、/rav b 侦查 70 vs 潜行 55\n每边的写法和 /ra 一样。"
    sides = []
    for tag, part in zip(("甲", "乙"), parts):
        spec = _coc_parse(part, 0, lookup, False)
        if isinstance(spec, str):
            return f"{tag}方：{spec}"
        sides.append(spec)
    rs = [_coc_roll(sp, house) for sp in sides]
    lv = [LEVEL_RANK[r.level] for r in rs]
    lines = []
    for tag, sp, r in zip(("甲", "乙"), sides, rs):
        lines.append(f"{tag}方 {sp['label'] + ' ' if sp['label'] else ''}D100={r.roll}/{sp['target']} —— {r.level}")
    if lv[0] != lv[1]:
        win = "甲" if lv[0] > lv[1] else "乙"
        verdict = f"{win}方胜出（成功等级更高）"
    elif rs[0].target != rs[1].target and rs[0].level not in ("失败", "大失败"):
        win = "甲" if rs[0].target > rs[1].target else "乙"
        verdict = f"{win}方胜出（等级相同，数值更高）"
    else:
        verdict = "平局，双方都没占到便宜" if rs[0].level in ("失败", "大失败") else "平局，数值也一样，重掷或由 KP 裁定"
    return Rolled("对抗检定\n" + "\n".join(lines) + f"\n{verdict}")


def _roll_loss(expr: str):
    terms = parse_expr(expr)
    if terms is None or not terms_valid(terms):
        return None
    detail, total = roll_terms(terms)
    lo, hi = expr_range(terms)
    return max(total, 0), max(hi, 0), detail, max(lo, 0)


def coc_sanity(raw: str, lookup: Lookup, house: str = DEFAULT_HOUSE) -> str:
    m = re.match(r"^(\S+?)/(\S+?)(?:\s+(\d{1,3}))?$", raw.strip())
    if not m:
        return "格式：/sc 成功损失/失败损失 [当前理智]\n例：/sc 1/1d6 60；不写理智就取你角色的 SAN（先 /录属性 SAN60）"
    note, from_card, who = "", False, ""
    if m.group(3):
        san = int(m.group(3))
    else:
        got = lookup("SAN")
        if isinstance(got, str):
            return got
        san, from_card, who = got[1], True, got[0]
        note = f"（取自{who}的 SAN）"
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
    tail = _pick(SAN_REMARKS, "fumble" if verdict == "大失败" else "pass" if passed else "fail")
    hints = []
    if left == 0:
        hints.append("理智归零，角色陷入永久疯狂。")
    elif loss >= 5:
        hints.append(f"单次损失 {loss} 点（≥5），需要做一次智力检定（/ra 智力），通过则陷入临时疯狂（/ti 看症状）。")
    plain = f"理智检定：D100={roll}/{san} —— {verdict}\n损失 {loss}（{process}），理智 {san} → {left}。{tail}{note}"
    if hints:
        plain += "\n" + "\n".join(hints)
    card = dicecard.sc_card(roll, san, verdict, loss, process, left, tail, "　".join(x for x in [note.strip("（）")] + hints if x))
    return Rolled(plain, card, san_left=left, san_before=san, loss=loss, from_lookup=from_card, who=who)


def coc_growth(raw: str, lookup: Lookup | None = None) -> str:
    """/en 技能 数值：数值可以不写，有 lookup 就取角色的同名技能（成长后由插件写回）。"""
    text = raw.strip()
    m = re.match(r"^(.*?)\s*(\d{1,3})$", text)
    from_card, who = False, ""
    if m:
        label, value = m.group(1).strip(), int(m.group(2))
    elif text and lookup is not None:
        got = lookup(text)
        if isinstance(got, str):
            return got
        who, value = got[:2]
        label, from_card = text, True
        value = min(value, 100)
    else:
        return "格式：/en [技能名] 当前数值\n例：/en 侦查 55；也可以只写技能名 /en 侦查，取你角色的数值，成长后自动记上"
    roll = random.randint(1, 100)
    prefix = f"{label} " if label else ""
    if roll > value or roll > 95:
        gain = random.randint(1, 10)
        remark = _pick(GROWTH_REMARKS, "grow")
        plain = f"{prefix}成长检定：D100={roll}/{value} —— 成长！+{gain}（1D10），{value} → {value + gain}。"
    else:
        gain, remark = 0, _pick(GROWTH_REMARKS, "none")
        plain = f"{prefix}成长检定：D100={roll}/{value} —— 没有成长。{remark}"
    return Rolled(plain, dicecard.en_card(label, roll, value, gain, remark), gain=gain, value=value,
                  label=label, from_lookup=from_card, who=who)


# ---------- COC7 派生值与制卡 ----------

def coc_db_build(strength: int, size: int) -> tuple[str, int]:
    """伤害加值（DB）和体格，按 力量+体型 查表。"""
    total = strength + size
    if total < 65:
        return "-2", -2
    if total < 85:
        return "-1", -1
    if total < 125:
        return "0", 0
    if total < 165:
        return "+1D4", 1
    if total < 205:
        return "+1D6", 2
    extra = (total - 205) // 80
    return f"+{2 + extra}D6", 3 + extra


def coc_hp_max(con: int, size: int) -> int:
    return (con + size) // 10


def coc_mov(strength: int, dex: int, size: int) -> int:
    """移动力（没算年龄：40 岁起每 10 年 -1）。"""
    if strength < size and dex < size:
        return 7
    if strength > size and dex > size:
        return 9
    return 8


COC_STAT_ORDER = ("力量", "体质", "体型", "敏捷", "外貌", "智力", "意志", "教育", "幸运")
_COC_2D6 = {"体型", "智力", "教育"}                  # 这三项是 (2D6+6)×5，其余 3D6×5


def coc_stats(raw: str) -> str:
    """/coc [组数]：COC7 制卡，每组 9 项属性 + 生命、魔法、理智、DB、体格、移动。"""
    text = raw.strip()
    times = int(text) if text.isdigit() else 1
    if not 1 <= times <= 5:
        return "一次最多出 5 组。"
    lines = ["COC7 制卡（力量体质敏捷外貌意志幸运 3D6×5，体型智力教育 (2D6+6)×5）："]
    for i in range(1, times + 1):
        v = {k: (sum(random.randint(1, 6) for _ in range(2)) + 6 if k in _COC_2D6 else sum(random.randint(1, 6) for _ in range(3))) * 5
             for k in COC_STAT_ORDER}
        db, build = coc_db_build(v["力量"], v["体型"])
        total = sum(v[k] for k in COC_STAT_ORDER if k != "幸运")
        lines.append(f"第{i}组 " + " ".join(f"{k}{v[k]}" for k in COC_STAT_ORDER)
                     + f"\n　总计{total}（不含幸运）　生命{coc_hp_max(v['体质'], v['体型'])} 魔法{v['意志'] // 5} 理智{v['意志']}"
                     + f" DB{db} 体格{build} 移动{coc_mov(v['力量'], v['敏捷'], v['体型'])}")
    lines.append("移动没算年龄（40 岁起每 10 年 -1）。选好一组后 /st 力量60 体质50 …… 录进角色卡。")
    return "\n".join(lines)


# ---------- 绿色三角洲（Delta Green）----------
# D100 ≤ 技能成功；掷出 01 或两位相同（11、22……99）是大成功（成功时）/大失败（失败时），00 永远大失败。
# 没有奖励/惩罚骰和困难等级，用 ±20、±40 修正。属性检定 = 属性×5。

DG_STATS = ("力量", "体质", "敏捷", "智力", "意志", "魅力")
DG_USAGE = "格式：/ra 技能名 [数值] [+20/-20]\n例：/ra 搜索 50、/ra 枪械+20、/ra 力量（属性自动×5）"


def judge_dg(roll: int, target: int) -> str:
    if roll == 100:
        return "大失败"
    ok = roll <= target
    if roll == 1 or roll % 11 == 0:
        return "大成功" if ok else "大失败"
    return "成功" if ok else "失败"


def _dg_roll(spec: dict) -> Rolled:
    label, target, note_cap = spec["label"], spec["target"], spec["note_cap"]
    roll = random.randint(1, 100)
    level = judge_dg(roll, target)
    remark = _pick(LEVEL_REMARKS, level)
    prefix = f"{label} " if label else ""
    plain = f"{prefix}检定：D100={roll}/{target}{note_cap} —— {level}。{remark}"
    card = dicecard.check_card(label, roll, target, level, remark, cap_note=note_cap.strip("（）"))
    return Rolled(plain, card, roll=roll, level=level, target=target, verdict=level, label=label,
                  from_card=spec.get("from_card", False))


def dg_check(raw: str, extra: int, lookup: Lookup, proxy: bool = False) -> str:
    """绿色三角洲的 /ra。前面写「3#」连掷几次。"""
    if extra:
        return "绿色三角洲没有奖励骰/惩罚骰，用修正代替：/ra 枪械+20、/ra 潜行-20。"
    raw = raw.strip()
    times = 1
    m = re.match(r"^(\d{1,3})#\s*(.*)$", raw)
    if m:
        times, raw = int(m.group(1)), m.group(2)
        if not 1 <= times <= MAX_TIMES:
            return f"一次最多连掷 {MAX_TIMES} 次。"
    spec = _coc_parse(raw, 0, lookup, proxy)
    if isinstance(spec, str):
        return spec.replace(COC_USAGE, DG_USAGE)
    if spec["extra"] or spec["difficulty"]:
        return "绿色三角洲没有奖励骰/惩罚骰和困难等级，用修正代替：/ra 枪械+20、/ra 潜行-40。"
    if times == 1:
        return _dg_roll(spec)
    results = [_dg_roll(spec) for _ in range(times)]
    lines = [f"第{i}次 " + str(r).split("检定：", 1)[1] for i, r in enumerate(results, 1)]
    wins = sum(1 for r in results if r.verdict.endswith("成功"))
    head = f"{spec['label'] + ' ' if spec['label'] else ''}连续检定 ×{times}（{spec['target']}）："
    return Rolled(head + "\n" + "\n".join(lines) + f"\n成功 {wins} 次，失败 {times - wins} 次。",
                  label=spec["label"], verdict="失败" if wins < times else "成功", from_card=spec.get("from_card", False))


def dg_sanity(raw: str, lookup: Lookup) -> str:
    """/sc 成功损失/失败损失 [理智]：大成功损失取最小，大失败取最大。"""
    m = re.match(r"^(\S+?)/(\S+?)(?:\s+(\d{1,3}))?$", raw.strip())
    if not m:
        return "格式：/sc 成功损失/失败损失 [当前理智]\n例：/sc 0/1d4 60、/sc 1/1d6；不写理智就取你角色卡的理智"
    note, from_card, who = "", False, ""
    if m.group(3):
        san = int(m.group(3))
    else:
        got = lookup("SAN")
        if isinstance(got, str):
            return got
        san, from_card, who = got[1], True, got[0]
        note = f"（取自{who}的理智）"
    ok_loss, bad_loss = _roll_loss(m.group(1)), _roll_loss(m.group(2))
    if ok_loss is None or bad_loss is None:
        return "损失的写法看不懂，数字或 1d6 这类即可。"
    roll = random.randint(1, 100)
    level = judge_dg(roll, san)
    if level == "大成功":
        loss, process = ok_loss[3], "大成功，取最小值"
    elif level == "成功":
        loss, process = ok_loss[0], ok_loss[2]
    elif level == "大失败":
        loss, process = bad_loss[1], "大失败，取最大值"
    else:
        loss, process = bad_loss[0], bad_loss[2]
    left = max(san - loss, 0)
    hints = []
    if left == 0:
        hints.append("理智归零：Agent 永久疯狂，交给 Handler。")
    elif loss >= 5:
        hints.append(f"一次失去 {loss} 点（≥5）：临时疯狂，由 Handler 决定反应（战斗、逃跑或崩溃）。")
    tail = _pick(SAN_REMARKS, "fumble" if level == "大失败" else "pass" if level.endswith("成功") else "fail")
    plain = f"理智检定：D100={roll}/{san} —— {level}\n损失 {loss}（{process}），理智 {san} → {left}。{tail}{note}"
    if hints:
        plain += "\n" + "\n".join(hints)
    return Rolled(plain, None, san_left=left, san_before=san, loss=loss, from_lookup=from_card, who=who, level=level)


def dg_stats(raw: str) -> str:
    """/dg [组数]：6 项属性各 4D6 取高 3，顺带算 生命、意志力、理智、崩溃点。"""
    text = raw.strip()
    times = int(text) if text.isdigit() else 1
    if not 1 <= times <= 5:
        return "一次最多出 5 组。"
    lines = ["绿色三角洲制卡（6 项属性各 4D6 取高 3；属性检定 = 属性×5）："]
    for i in range(1, times + 1):
        v = {k: sum(sorted(random.randint(1, 6) for _ in range(4))[1:]) for k in DG_STATS}
        san = v["意志"] * 5
        lines.append(f"第{i}组 " + " ".join(f"{k}{v[k]}" for k in DG_STATS) + f"，合计 {sum(v.values())}"
                     + f"\n　生命{dg_hp_max(v['力量'], v['体质'])} 意志力{v['意志']} 理智{san} 崩溃点{san - v['意志']}")
    lines.append("选好一组后 /st 力量12 体质10 …… 录进角色卡（理智、生命等不录也会按属性算初始值）。")
    return "\n".join(lines)


def dg_improve(label: str, value: int) -> Rolled:
    """绿色三角洲的成长：失败过的技能团后 +1D4，不用掷 D100。"""
    gain = random.randint(1, 4)
    return Rolled(f"{label}：{value} +1D4({gain}) → {value + gain}", None, gain=gain, value=value, label=label)


def dg_hp_max(strength: int, con: int) -> int:
    return (strength + con + 1) // 2


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
    "恐惧：患上一种恐惧症，持续时间内对恐惧源有反应",
    "狂躁：出现一种狂躁，持续时间内沉浸其中",
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
    "恐惧：患上一种新的恐惧症，会设法避开恐惧源",
    "狂躁：患上一种新的狂躁，沉浸其中",
]


def _sub_table(kind: str) -> str:
    """恐惧 / 狂躁的子表：掷 1D100，返回一行文字。"""
    from .madness_tables import MANIAS, PHOBIAS
    n = random.randint(1, 100)
    if kind == "恐惧":
        name, desc = PHOBIAS[n - 1]
        return f"恐惧症状：1D100={n} {name}（害怕：{desc}）"
    name, desc = MANIAS[n - 1]
    return f"狂躁症状：1D100={n} {name}（{desc}）"


def phobia() -> str:
    return _sub_table("恐惧")


def mania() -> str:
    return _sub_table("狂躁")


def madness(raw: str, summary: bool) -> str:
    """ti：临时疯狂（持续 1D10 轮）；li：总结疯狂（持续 1D10 小时）。raw 可以写「某某」当作被疯狂的人。"""
    who = raw.strip()
    idx = random.randint(1, 10)
    last = random.randint(1, 10)
    table = SUMMARY_MADNESS if summary else TEMP_MADNESS
    unit = "小时" if summary else "轮"
    kind = "总结疯狂（li）" if summary else "临时疯狂（ti）"
    head = f"{who} 的{kind}" if who else kind
    out = f"{head}：1D10={idx}\n{table[idx - 1]}\n持续 1D10={last} {unit}。"
    if idx in (9, 10):                      # 抽到恐惧 / 狂躁：自动再掷一次具体症状
        out += "\n" + _sub_table("恐惧" if idx == 9 else "狂躁")
    return out


# ======================== DND5e ========================

def _split_glued_mods(raw: str) -> list[str]:
    """「侦察+3」这种名字和修正连写的，拆成「侦察」「+3」；伤害式（1d8+3）不拆。"""
    out = []
    for t in raw.split():
        m = re.match(r"^(.*?[^\d+\-])([+-]\d{1,2})$", t)
        if m and not re.search(r"\d[dD]|^[dD]\d", t):
            out += [m.group(1), m.group(2)]
        else:
            out.append(t)
    return out


DND_ABILITIES = {"力量", "STR", "敏捷", "DEX", "体质", "CON", "智力", "INT", "感知", "WIS", "魅力", "CHA"}


def ability_mod(score: int) -> int:
    return (score - 10) // 2


def dnd_check(raw: str, adv: int, lookup: Lookup) -> str:
    mod, dc, labels = 0, None, []
    has_adv, has_dis = adv > 0, adv < 0
    for t in _split_glued_mods(raw):
        if t.lower() in ("优势", "adv"):
            has_adv = True
        elif t.lower() in ("劣势", "dis"):
            has_dis = True
        elif MOD_RE.match(t):
            mod += int(t)
        elif t.isdigit() and dc is None:
            dc = int(t)
        else:
            labels.append(t)
    if abs(mod) > 30 or (dc is not None and dc > 60):
        return "修正最多 ±30，DC 最多 60，再核对一下。"
    adv = int(has_adv) - int(has_dis)          # 优势和劣势同时有就互相抵消
    label = " ".join(labels)
    card_note = ""
    if label:
        got = lookup(label)
        if not isinstance(got, str):       # 没有这个属性就当普通检定名，不报错
            role, val = got[:2]
            if label.upper() in DND_ABILITIES:       # 属性值（力量16）换算成调整值
                cm = ability_mod(val)
                card_note = f"（{role}的{label}{val}→{cm:+d}）"
            else:                                      # 技能（运动、侦察）存的就是加值，不再换算
                cm = val
                card_note = f"（{role}的{label}加值{cm:+d}）"
            mod += cm
            if abs(mod) > 30:
                return "修正最多 ±30，再核对一下角色卡里这个数。"
    rolls = [random.randint(1, 20) for _ in range(2 if adv else 1)]
    chosen = max(rolls) if adv >= 0 else min(rolls)
    total = chosen + mod
    note = f"（{'优势' if adv > 0 else '劣势'}：{'/'.join(map(str, rolls))} 取{chosen}）" if adv else ""
    prefix = f"{label} " if label else ""
    mod_text = f"{mod:+d}" if mod else ""
    line = f"{prefix}检定：D20={chosen}{mod_text}{'=' + str(total) if mod else ''}{card_note}{note}"
    key, passed = "", None
    if dc is not None:
        passed = total >= dc
        line += f" vs DC{dc} —— {'通过' if passed else '未通过'}"
        key = "pass" if passed else "fail"
    if chosen == 20:
        line += "（自然20）"; key = "nat20"
    elif chosen == 1:
        line += "（自然1）"; key = "nat1"
    remark = _pick(DND_REMARKS, key) if key else ""
    card = dicecard.dnd_check_card(label, chosen, mod, total, dc, passed, [card_note.strip("（）"), note.strip("（）")], remark)
    return Rolled(line + (f"。{remark}" if remark else ""), card)


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
    mod, ac, dmg, labels = 0, None, None, []
    has_adv = has_dis = False
    for t in _split_glued_mods(raw):
        if t.lower() in ("优势", "adv"):
            has_adv = True
        elif t.lower() in ("劣势", "dis"):
            has_dis = True
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
    adv = int(has_adv) - int(has_dis)
    rolls = [random.randint(1, 20) for _ in range(2 if adv else 1)]
    chosen = max(rolls) if adv >= 0 else min(rolls)
    total = chosen + mod
    note = f"（{'优势' if adv > 0 else '劣势'}：{'/'.join(map(str, rolls))} 取{chosen}）" if adv else ""
    label = " ".join(labels)
    line = f"{label + ' ' if label else ''}攻击：D20={chosen}{f'{mod:+d}' if mod else ''}={total}{note} vs AC{ac}"
    crit = chosen == 20
    notes = [note.strip("（）")]
    if chosen == 1:
        return Rolled(f"{line} —— 自然1，必定失手。", dicecard.at_card(label, chosen, mod, total, ac, "fumble", None, notes, ""))
    if not crit and total < ac:
        remark = _pick(DND_REMARKS, "fail")
        return Rolled(f"{line} —— 未命中。{remark}", dicecard.at_card(label, chosen, mod, total, ac, "miss", None, notes, remark))
    hit = double_dice(dmg) if crit else dmg
    if not terms_valid(hit):
        hit, crit = dmg, False
    detail, value = roll_terms(hit)
    value = max(value, 0)
    plain = f"{line} —— {'自然20，暴击！骰子翻倍' if crit else '命中'}。\n伤害：{expr_text(hit)} = {detail} = {value}"
    card = dicecard.at_card(label, chosen, mod, total, ac, "crit" if crit else "hit", (expr_text(hit), detail, value), notes, "")
    return Rolled(plain, card)


# ======================== 帮助 ========================

# ======================== 帮助 ========================
# /骰子帮助 不带参数只发目录；/骰子帮助 编号或名字 发对应的一小块。每块尽量短，不要一次刷屏。

HELP_RD = (
    "【掷骰】所有规则通用\n"
    "/rd 3d6+2d4-1　加减、常数；/rd 2d6×3、(1d6+2)×2　乘法、括号\n"
    "/rd 4d6k3　取最高3颗；/rd 2d20kl1　取最低1颗；/rd 3d6!　爆骰\n"
    "/rd 5#2d6　连掷5轮；/rd 伤害 2d6+3　带原因；不写表达式就是 D100\n"
    "/rh 表达式　暗骰：结果私聊给你，群里只说一声（要先把你加进机器人白名单）"
)
HELP_COC = (
    "【COC7 · 检定】\n"
    "/ra 侦查 70　D100 检定；不写数值就取你角色卡上的（没录过按规则书初始值）\n"
    "/ra b2 困难 射击　b 奖励骰、p 惩罚骰，困难/极难；/rb、/rp 是简写\n"
    "/ra 侦查+10　临时修正；/ra 3#侦查　连掷3次\n"
    "/ra 侦查 @某人　代骰，取 TA 的数值\n"
    "/rav 力量 60 vs 力量 50　对抗；/rah 侦查　暗检定\n"
    "/setcoc 0-5　房规（大成功/大失败的范围）"
)
HELP_COC2 = (
    "【COC7 · 理智、成长、制卡】\n"
    "/sc 1/1d6　理智检定，不写理智就取卡上的，扣完自动写回；一次掉 5 点以上会提醒智力检定\n"
    "/ti、/li　临时 / 总结疯狂症状；/恐惧症状、/狂躁症状　单掷具体症状\n"
    "/en 侦查　成长检定，成长了自动写回\n"
    "跑团模式：检定成功会记成长标记，团后发 /en 一次掷完\n"
    "/coc 3　制卡：一次掷 3 组属性，带生命、魔法、理智、DB、体格、移动"
)
HELP_DND = (
    "【DND5e】\n"
    "/ra 运动 +3 15　D20+修正 对 DC（DC 可不写）；属性名会自动换算成调整值\n"
    "/rb 优势、/rp 劣势　也可以在 /ra 后面写 优势/劣势\n"
    "/at 长剑 +5 15 1d8+3　攻击：命中、AC、伤害；自然20暴击\n"
    "/ri 名字 +2　先攻；/ri 看顺序；/ri 清空\n"
    "/dnd 3　属性生成：6 项各 4D6 取高 3"
)
HELP_DG = (
    "【绿色三角洲】\n"
    "/ra 搜索　D100 ≤ 技能成功；01 或两位相同（11、22……）：成功时大成功、失败时大失败；00 必大失败\n"
    "/ra 枪械+20　没有奖励骰和困难等级，用 ±20、±40 修正\n"
    "/ra 力量　属性检定自动按 属性×5\n"
    "/sc 0/1d4　理智检定：大成功取最小、大失败取最大；掉到崩溃点会提醒并重设崩溃点\n"
    "跑团模式：检定失败记成长标记，团后 /en 每项 +1D4\n"
    "/dg 3　制卡：属性 4D6 取高 3，带生命、意志力、理智、崩溃点"
)
HELP_CARD = (
    "【角色卡】跑团模式的群里用；卡跟着你走，换群也能用\n"
    "/st 力量60 敏捷70　录属性；/st 生命-3　增减；/st　看卡（含上限、DB）\n"
    "/st del 力量、/st clr　删一项 / 清空\n"
    "/pc new 名字 [coc|dnd|dg]　多建一张；/pc list；/pc 切换 名字；/pc del 名字\n"
    "/nn 名字　设你在 log 里显示的名字\n"
    "/en list　看成长标记；/en+ 侦查、/en- 侦查　手动加减；/en clr　清空"
)
HELP_TEAM = (
    "【队伍】跑团模式的群里用\n"
    "/team 调查员 add @甲 @乙　加人（不 @ 是加自己，队伍没有会自动建）\n"
    "/team 调查员 侦查 聆听　列出全队的数值，按第一项排\n"
    "/team 调查员 ra 聆听　全队各掷一次\n"
    "/team list；/team 调查员 del @甲；/team 调查员 clear　解散"
)
HELP_LOG = (
    "【跑团 log】跑团模式的群里用\n"
    "/log new 名字　开始记；/log off 暂停；/log on 继续；/log on 名字　接着记以前的\n"
    "/log end　结束（给网页链接）；/log halt　强制停止（不给链接）\n"
    "/log list；/log stat [名字]；/log get 名字；/log export 名字；/log del 名字\n"
    "/说 台词　记一条角色发言（开了自动听取就不用）；/ob　切换旁观\n"
    "括号开头的话默认不记"
)
HELP_ATTR = (
    "【恋综角色的属性】恋综模式的群里用\n"
    "/录属性 力量60 敏捷70　记下；/属性　看；/删属性 力量；/清空属性\n"
    "检定时直接写属性名就会取用"
)
HELP_FUN = (
    "【小工具】\n"
    "/name 日 3 女　随机人名（中/英/日、男/女、数量）\n"
    "/who 甲 乙 丙　随机排顺序\n"
    "/draw 塔罗 3　抽牌（塔罗、扑克、遭遇），不带参数看牌堆\n"
    "/jrrp　今日人品"
)
HELP_SWITCH = "【切换规则】/切换规则 COC、/切换规则 DND、/切换规则 绿色三角洲；每个群各记各的，不带参数看当前规则"

# (编号用的名字, 别名, 目录里的一句话, 正文, 只在哪种规则下标「当前」)
HELP_SECTIONS = [
    ("掷骰", ("rd", "r", "暗骰"), "/rd 通用掷骰、暗骰", HELP_RD, ""),
    ("COC检定", ("coc", "coc7", "检定"), "COC7 的 /ra、奖励惩罚骰、对抗", HELP_COC, "coc"),
    ("COC理智", ("理智", "成长", "sc", "en", "制卡", "coc2"), "COC7 的 /sc、疯狂、/en、/coc 制卡", HELP_COC2, "coc"),
    ("DND", ("dnd", "dnd5e", "5e"), "DND5e 的检定、攻击、先攻", HELP_DND, "dnd"),
    ("绿色三角洲", ("dg", "三角洲", "deltagreen"), "绿色三角洲的检定、理智、成长", HELP_DG, "dg"),
    ("角色卡", ("卡", "st", "pc", "nn"), "跑团模式的 /st、/pc、/nn、成长标记", HELP_CARD, ""),
    ("队伍", ("team",), "/team 全队查数值、一起掷", HELP_TEAM, ""),
    ("log", ("记录", "跑团log"), "/log 记录一场跑团", HELP_LOG, ""),
    ("小工具", ("工具", "name", "draw", "jrrp"), "/name /who /draw /jrrp", HELP_FUN, ""),
    ("恋综属性", ("属性", "录属性"), "恋综模式的 /录属性", HELP_ATTR, ""),
    ("切换规则", ("规则",), "COC / DND / 绿色三角洲 怎么换", HELP_SWITCH, ""),
]

RULE_TITLE = {"coc": "COC7", "dnd": "DND5e", "dg": "绿色三角洲"}
RULE_NAMES = {"coc": "coc", "coc7": "coc", "克苏鲁": "coc", "dnd": "dnd", "dnd5e": "dnd", "5e": "dnd", "龙与地下城": "dnd",
              "dg": "dg", "deltagreen": "dg", "delta": "dg", "绿色三角洲": "dg", "三角洲": "dg", "绿三角": "dg"}


def help_menu(rule: str) -> str:
    lines = [f"骰子帮助（这个群现在是 {RULE_TITLE.get(rule, rule)} 规则）", "发「/骰子帮助 编号」或「/骰子帮助 名字」看那一部分："]
    for i, (name, _, desc, _, only) in enumerate(HELP_SECTIONS, 1):
        lines.append(f"{i}. {name}　{desc}" + ("　← 当前规则" if only and only == rule else ""))
    return "\n".join(lines)


def help_text(topic: str, rule: str) -> str:
    """不带参数：目录。带编号或名字：对应的一块。认不出来：目录。"""
    t = topic.strip().lower()
    if not t:
        return help_menu(rule)
    if t.isdigit() and 1 <= int(t) <= len(HELP_SECTIONS):
        return HELP_SECTIONS[int(t) - 1][3]
    for name, aliases, _, text, _ in HELP_SECTIONS:
        if t == name.lower() or t in aliases:
            return text
    return f"没有「{topic.strip()}」这一部分。\n\n" + help_menu(rule)
