"""QQ 消息按钮（官方机器人的 Markdown + 按钮键盘）。只管排版，不依赖 nonebot，方便单独测试；真正发送在 core/qqsend.py 的 reply_buttons。

· 按钮点一下 = 替玩家在群里发一条指令（action.type 2）。enter=True 直接发出；enter=False 只填进输入框，玩家补完再发
  （开房间、摘心愿这类点错了有后果的，用填入，让玩家自己按发送）。
· 按钮必须挂在 Markdown 消息上，要机器人有群 Markdown 权限。发不出去就退回纯文字，玩家照文字里的提示手打指令，功能不受影响。
· 后台「基础」里的 qq_buttons 设成 0 可以整个关掉。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .db import get_config
from .dicecard import md_escape

MAX_ROWS = 5            # QQ 限制：一条消息最多 5 行按钮
MAX_PER_ROW = 5         # 每行最多 5 个
LABEL_MAX = 12          # 按钮上的字太长会被 QQ 截断，这里先截好

# 按钮颜色：官方文档只写了 0 灰色线框、1 蓝色线框（2026-11 查）。红色（危险）的编号只在第三方文档里见过、说法不一，
# 没在真机上确认前先用灰色，确认后改 DANGER_STYLE 这一个数就行。写错编号 QQ 可能整条拒收（会退回纯文字一小时）。
DANGER_STYLE = 0
STYLES = {"primary": 1, "secondary": 0, "danger": DANGER_STYLE}


@dataclass(frozen=True)
class Btn:
    label: str
    cmd: str              # 点了以后发出的指令（不带前缀，发送时按「指令必须带前缀」的设置补）
    enter: bool = True    # True 直接发出；False 填进输入框
    style: str = "primary"   # primary 主要（蓝）/ secondary 次要（灰）/ danger 危险（红，见 DANGER_STYLE）


def enabled() -> bool:
    return get_config("qq_buttons", "1") == "1"


def _cmd_text(cmd: str) -> str:
    return ("/" + cmd) if get_config("require_prefix", "0") == "1" and not cmd[:1] in "/.。" else cmd


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+")


def _label(s: str) -> str:
    """按钮上不放 emoji（作者定的）：写了也在这里去掉。"""
    s = _EMOJI.sub("", str(s)).strip()
    return s if len(s) <= LABEL_MAX else s[:LABEL_MAX - 1] + "…"


def layout(btns: list[Btn], per_row: int = 3) -> tuple[list[list[Btn]], int]:
    """排成几行。返回 (行, 放不下的个数)。"""
    per_row = max(1, min(MAX_PER_ROW, per_row))
    cap = per_row * MAX_ROWS
    shown = btns[:cap]
    return [shown[i:i + per_row] for i in range(0, len(shown), per_row)], max(0, len(btns) - cap)


def keyboard(rows: list[list[Btn]]) -> dict:
    """QQ 的自定义按钮键盘（给 MessageKeyboard.model_validate 用）。所有人都能点，点了谁发指令就算谁的，权限由指令自己判断。"""
    out, n = [], 0
    for row in rows:
        bs = []
        for b in row:
            n += 1
            data = _cmd_text(b.cmd)
            bs.append({
                "id": str(n),
                "render_data": {"label": _label(b.label), "visited_label": _label(b.label), "style": STYLES.get(b.style, 1)},
                "action": {"type": 2, "permission": {"type": 2}, "data": data, "enter": b.enter,
                           "unsupport_tips": f"QQ 版本太旧，按钮用不了，请手动发送：{data}"},
            })
        out.append({"buttons": bs})
    return {"content": {"rows": out}}


def markdown(text: str) -> str:
    """把一段现成的纯文字回复转成 Markdown：第一行加粗当标题，其余原样（去掉格式效果），换行保留。"""
    lines = str(text).split("\n")
    head, rest = lines[0], lines[1:]
    out = [f"**{md_escape(head)}**" if head.strip() else ""]
    out += [md_escape(x) for x in rest]
    return "  \n".join(out)


def build(text: str, btns: list[Btn], per_row: int = 3) -> tuple[str, dict]:
    """返回 (Markdown 正文, 键盘)。按钮太多放不下的，在正文末尾说一句还有几个、怎么手打。"""
    rows, more = layout(btns, per_row)
    md = markdown(text)
    if more:
        md += f"  \n*还有 {more} 个没放下，照上面的写法手动发送*"
    return md, keyboard(rows)
