"""骰子结果排成 Markdown 小卡片（版式照索罗的 check_card）。纯排版，不依赖 nonebot，方便单独测试。

    ### ✅ 敏捷 检定　成功

    **D100**　掷出 **19**　目标 ≤ 30
    *困难要求 ≤ 15*

    > 台词
"""

from __future__ import annotations

import json
import random
import re


_MD_FW = str.maketrans({"\\": "＼", "`": "｀", "*": "＊", "_": "＿", "[": "［", "]": "］", "<": "＜", ">": "＞", "~": "～"})
_MD_LINE_HEAD = {"#": "＃", "+": "＋", "-": "－", "|": "｜"}


def md_escape(text: str) -> str:
    """玩家写的字放进 Markdown 前去掉格式效果。QQ 对反斜杠转义不稳（\\+ \\* 会把反斜杠直接显示出来，2026-11-11 线上截图确认），
    所以不用反斜杠：会触发格式的 * _ ` ~ [ ] < > 换成全角（看着一样），# + - | 只有在行首才会变标题/列表，行首才换全角，句中原样。"""
    text = str(text or "").translate(_MD_FW)
    return re.sub(r"(?m)^(\s*)([#+\-|])", lambda m: m.group(1) + _MD_LINE_HEAD[m.group(2)], text)


# 成功程度一眼看得出来：越好越亮，大失败是爆炸。后台「个性化骰子」可以换（存在设置 dice_marks，JSON {"大成功": "🌟", …}，只存改过的）
# 一格可以写好几个表情、用空格隔开，每次随机挑一个；难度骰（/rt）默认就是这样，成败各一组轮着换。
MARKS = {"大成功": "🎉", "极难成功": "💎", "困难成功": "⭐", "成功": "✅", "失败": "❌", "大失败": "💥",
         "难度骰成功": "✅ 🎯 👌 ✨ 🍀", "难度骰失败": "❌ 💨 🫠 🙃 🌀"}
MARKS_KEY = "dice_marks"
MARK_MAX = 40         # 一格最多几个字符（组合 emoji 会占好几个，一格又可以写好几个）


def custom_marks() -> dict[str, str]:
    """后台改过的表情；读不出来、或者不在库里（单独测排版时）当没改过。"""
    try:
        from .db import get_config
        data = json.loads(get_config(MARKS_KEY, "") or "{}")
    except Exception:
        return {}
    return {k: str(v)[:MARK_MAX] for k, v in data.items() if k in MARKS and str(v).strip()} if isinstance(data, dict) else {}


def save_custom_marks(data: dict[str, str]) -> None:
    """只存和默认不一样的；空的 = 用默认。"""
    from .db import set_config
    out = {k: v.strip()[:MARK_MAX] for k, v in data.items() if k in MARKS and v.strip() and v.strip() != MARKS[k]}
    set_config(MARKS_KEY, json.dumps(out, ensure_ascii=False) if out else "")


def mark_for(verdict: str) -> str:
    marks = {**MARKS, **custom_marks()}
    got = marks.get(verdict) or marks["成功" if verdict.endswith("成功") else "失败"]
    return random.choice(got.split())


def check_card(label: str, roll: int, target: int, verdict: str, remark: str, *,
               need: str = "", dice_note: str = "", cap_note: str = "") -> str:
    """label 技能名（可空）；need 困难/极难的要求，如「困难要求 ≤ 15」；dice_note 奖励/惩罚骰说明；
    cap_note 数值被压到 100 时的说明。这三项没有就不出现，卡片只剩标题、数字和台词。"""
    mark = mark_for(verdict)
    title = f"{md_escape(label)}检定" if label else "检定"
    lines = [f"### {mark} {title}　{md_escape(verdict)}", "",
             f"**D100**　掷出 **{roll}**　目标 ≤ {target}"]
    for extra in (need, dice_note, cap_note):
        if extra:
            lines.append(f"*{md_escape(extra)}*")
    body = "  \n".join(lines[2:])
    out = [lines[0], "", body]
    if remark:
        out += ["", "> " + md_escape(remark)]
    return "\n".join(out)


# ── 其余检定类指令：同一套版式（标题 / 数字行 / 小斜体说明 / 台词引用块）─────────────────

def _card(title: str, rows: list[str], notes: list[str], remark: str) -> str:
    out = [f"### {title}", ""]
    out.append("  \n".join(rows + [f"*{md_escape(n)}*" for n in notes if n]))
    if remark:
        out += ["", "> " + md_escape(remark)]
    return "\n".join(out)


def rd_card(reason: str, expr: str, detail: str, total: int, remark: str, simple: bool, who: str = "") -> str:
    """/rd 单次，照人说话的口气写：「绵绵面掷了 D2，出了 2 点」「绵绵面掷了 2D6+3：(4+6)+3，一共 13 点」。
    以前写「D2　2」，玩家看不懂 2 是什么；「D2 掷出 2」又把骰子当成了主语（2026-11-21 作者）。海豹骰、Dice! 也是人名打头
    （「玩家掷出了 D2=2」）。who 是角色名 / 角色卡名，官方机器人拿不到群昵称，没建角色的人就省掉主语。"""
    title = md_escape(reason) if reason else "掷骰"
    return _card(title, [rd_sentence(who, expr, detail, total, simple, bold=True)], [], remark)


def rd_sentence(who: str, expr: str, detail: str, total: int, simple: bool, bold: bool = False, reason: str = "") -> str:
    """纯文字和卡片共用的那一句；bold 时人名和点数加粗（卡片用，原因在卡片标题里，不传）。
    原因写成「绵绵面为「伤害」掷了…」，不写成「伤害：绵绵面掷了 2D6+3：…」（两个冒号读着别扭）。"""
    b = (lambda s: f"**{s}**") if bold else (lambda s: s)
    e = md_escape if bold else (lambda s: s)
    name = (b(e(who)) if who else "") + (f"为「{e(reason)}」" if reason else "")
    if simple:
        return f"{name}掷了 {e(expr)}，出了 {b(total)} 点"
    return f"{name}掷了 {e(expr)}：{e(detail)}，一共 {b(total)} 点"


def rt_card(reason: str, sides: int, roll: int, target: int, verdict: str, remark: str = "") -> str:
    """/rt 难度骰：自定面数，≤ 成功线算成功；表情从难度骰自己那组里随机挑"""
    title = (md_escape(reason) + " " if reason else "") + "难度骰"
    return _card(f"{mark_for('难度骰' + verdict)} {title}　{verdict}", [f"**D{sides}**　掷出 **{roll}**　目标 ≤ {target}"], [], remark)


def rd_multi_card(reason: str, expr: str, rounds: list[tuple[str, int]], tail: str, who: str = "") -> str:
    """/rd N#表达式：rounds 是每轮的（过程, 点数）"""
    title = (md_escape(reason) + "　" if reason else "") + (f"{md_escape(who)}把 {md_escape(expr)} 掷了 {len(rounds)} 轮" if who else f"{md_escape(expr)} 掷了 {len(rounds)} 轮")
    rows = [f"第{i}轮　**{t}**" if d == str(t) else f"第{i}轮　{md_escape(d)} = **{t}**" for i, (d, t) in enumerate(rounds, 1)]
    totals = [t for _, t in rounds]
    rows.append(f"最高 **{max(totals)}**　最低 **{min(totals)}**　合计 **{sum(totals)}**")
    return _card(title, rows, [], tail)


def sc_card(roll: int, san: int, verdict: str, loss: int, process: str, left: int, remark: str, note: str = "") -> str:
    mark = mark_for(verdict)
    how = f"（{md_escape(process)}）" if process != str(loss) else ""
    rows = [f"**D100**　掷出 **{roll}**　目标 ≤ {san}", f"**损失**　**{loss}**{how}", f"**理智**　{san} → **{left}**"]
    return _card(f"{mark} 理智检定　{md_escape(verdict)}", rows, [note], remark)


def en_card(label: str, roll: int, value: int, gain: int, remark: str) -> str:
    """gain>0 成长了，0 没有成长"""
    title = md_escape(label) + "成长检定"
    rows = [f"**D100**　掷出 **{roll}**　目标 > {value}"]
    if gain:
        rows.append(f"**{md_escape(label) or '技能'} +{gain}**（1D10）　{value} → **{value + gain}**")
    return _card(f"{mark_for('成功' if gain else '失败')} {title}　{'成长' if gain else '没有成长'}", rows, [], remark)


def dnd_check_card(label: str, chosen: int, mod: int, total: int, dc: int | None, passed: bool | None,
                   notes: list[str], remark: str) -> str:
    """DND 检定：passed 为 None 表示没给 DC，只看点数"""
    mark = "🎉 " if chosen == 20 else "💥 " if chosen == 1 else "" if passed is None else ("✅ " if passed else "❌ ")
    verdict = "" if passed is None else f"　{'通过' if passed else '未通过'}"
    title = f"{mark}{md_escape(label)}检定{verdict}"
    row = f"**D20**　掷出 **{chosen}**" + (f" {mod:+d} = **{total}**" if mod else "") + (f"　目标 ≥ {dc}" if dc is not None else "")
    return _card(title, [row], notes, remark)


def dnd_stat_card(groups: list[list[int]]) -> str:
    """/dnd 属性生成：groups 是每组 6 个已排好序的数值"""
    rows = [f"**第{i}组**　" + "　".join(f"{v}（{(v - 10) // 2:+d}）" for v in g) + f"　合计 **{sum(g)}**"
            for i, g in enumerate(groups, 1)]
    return _card("属性生成", rows, ["4D6 取高 3，共 6 项，顺序随你分配"], "")


def ri_card(joined: str, order: list[tuple[str, int]]) -> str:
    """/ri 先攻表：joined 是刚加入的那一行（可空），order 是排好序的（名字, 先攻值）"""
    out = ["### 先攻顺序", ""]
    if joined:
        out += [md_escape(joined), ""]
    out.append("  \n".join(f"**{i}.** {md_escape(n)}　**{v}**" for i, (n, v) in enumerate(order, 1)))
    return "\n".join(out)


_AT_VERDICT = {"crit": ("🎉", "暴击"), "hit": ("✅", "命中"), "miss": ("❌", "未命中"), "fumble": ("💥", "自然1，失手")}


def at_card(label: str, chosen: int, mod: int, total: int, ac: int, outcome: str,
            dmg: tuple[str, str, int] | None, notes: list[str], remark: str) -> str:
    """/at 攻击：outcome 是 crit / hit / miss / fumble；dmg 是（伤害表达式, 过程, 点数），没命中为 None"""
    mark, verdict = _AT_VERDICT[outcome]
    title = f"{mark} {md_escape(label) + ' ' if label else ''}攻击　{verdict}"
    rows = [f"**D20**　掷出 **{chosen}**" + (f" {mod:+d} = **{total}**" if mod else "") + f"　目标 ≥ AC {ac}"]
    if dmg:
        rows.append(f"**伤害**　{md_escape(dmg[0])} = {md_escape(dmg[1])} = **{dmg[2]}**")
    return _card(title, rows, notes, remark)


# ── 帮助：把现成的纯文字帮助转成 Markdown，不另存一份，免得两边改岔 ────────────────────────

def _hdesc(text: str) -> str:
    """帮助说明里的 * 是乘号，不是格式；md_escape 会把它换成全角＊。"""
    return md_escape(text.replace("*", "＊"))


def _help_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.split("\n"):
        ln = raw.rstrip()
        if not ln:
            continue
        if ln.startswith("【") and "】" in ln:                       # 【通用掷骰 /rd】两种规则都能用
            head, _, rest = ln.partition("】")
            out += ["", f"**{md_escape(head + '】')}**"]
            if rest.strip():
                out.append(_hdesc(rest.strip()))
        elif ln.startswith("/") and "　" in ln:                      # 「/rd 3d6+2d4-1　加减、常数」：指令用等宽字体
            cmd, _, desc = ln.partition("　")
            out.append(f"`{cmd.replace('`', '').replace('*', '×')}`　{_hdesc(desc)}")
        elif ln.startswith("　"):                                    # 缩进的附属行
            out.append("　" + _hdesc(ln.strip()))
        else:
            out.append(_hdesc(ln))
    return out


def help_card(sections: list[str], title: str = "", subtitle: str = "", footer: str = "") -> str:
    """sections 是若干段帮助原文；title 空就是只看某一块，不加大标题"""
    out: list[str] = []
    if title:
        out += [f"### {md_escape(title)}", ""]
        if subtitle:
            out.append(f"*{md_escape(subtitle)}*")
    for sec in sections:
        out += _help_lines(sec)
    if footer:
        out += ["", f"*{md_escape(footer)}*"]
    body = "  \n".join(out).replace("  \n\n", "\n\n").replace("\n  \n", "\n\n")
    return body.strip()
