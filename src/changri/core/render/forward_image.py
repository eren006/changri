"""合并转发画成长图：官方机器人发不了合并转发，长日的合并转发（玩家名单、复盘、信件、关系线……）交给索罗时画成图片。

只依赖 Pillow 和系统里的中文字体，不碰 nonebot，可以单独测试。没装 Pillow / 找不到字体时直接抛异常，
发送那边接住后退回成文字，消息不会丢。全程在内存里画，不写文件。
两种版式：
· 卡片（玩家名单、指南、关系线……）：跟 hub.changri.work 一个调子，浅灰底、白色圆角卡片、暖赭色点缀；
  标题、小标题、分隔线、列表项各有样式。
· 对话（复盘、戏录、好几个人说话的）：照存档站戏录长图（rp_archive/review_image.py）的样子，宋体、
  页眉写日期地点参与者，别人的气泡在左、群主人（owners）的气泡在右。
"""

from __future__ import annotations

import hashlib
import io
import re

try:
    from . import palette as P
except ImportError:      # 测试按文件路径单独加载本文件时没有包，直接按路径加载同目录的 palette.py
    import importlib.util as _ilu
    from pathlib import Path as _Path
    _spec = _ilu.spec_from_file_location("changri_relay_palette", _Path(__file__).with_name("palette.py"))
    P = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(P)
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = 900
OUTER = 32                       # 卡片到图片边缘
PAD_X, PAD_Y = 48, 44            # 卡片内边距
TEXT_W = W - 2 * OUTER - 2 * PAD_X
BODY, LINE_H = 30, 48
TITLE, TITLE_H = 38, 58
HEAD, HEAD_H = 31, 50
NAME_SZ, BRAND_SZ, FOOT_SZ = 22, 22, 20
BULLET_INDENT = 30
LETTER_INDENT = 26                # 信的正文离左边竖线的距离
DAY_H = 76                        # 时间线里「D1」这种天标题的行高
# 时间线里的状态胶囊：(底色, 字色)，配色同「戏录」长图（紫 + 暖灰）：进行中实心紫、待开启浅紫、已完结暖灰；认不出的状态用最后一项
_STATUS_STYLE = {"进行中": (P.PURPLE, P.WHITE), "待开启": (P.PURPLE_SOFT, P.PURPLE),
                 "待开始": (P.PURPLE_SOFT, P.PURPLE), "已完结": (P.CHIP_GREY, P.MUTED),
                 "已结束": (P.CHIP_GREY, P.MUTED), "长期活跃": (P.PURPLE_SOFT, P.PURPLE)}
_STATUS_OTHER = (P.CHIP_GREY, P.MUTED)
# 回复情况胶囊（紧挨在状态左边）：未回 = 深色实心，已回 = 白底紫边紫字
_REPLY_WAIT = (P.INK, P.WHITE)
_REPLY_DONE = (P.WHITE, P.PURPLE)
CHIP_PAD = 20
CHIP_GAP = 12
KV_COL = 190
PAGE_MAX_H = 6000                # 一张图最高；再长按行切到下一张
MAX_PAGES = 6                    # 再长的只发前几张，最后注明省略了
FOOT_H = 64

PAPER2, PAPER, LINE = P.BG, P.WHITE, P.RULE   # 配色见 palette.py（同「戏录」长图：暖米底 + 紫）
INK, MUTED, ACCENT, SUBTLE = P.INK, P.MUTED, P.PURPLE, P.SUBTLE
BRAND = "长日将尽"               # 机器人自己发言时用的名字（认这个名字就不标发言人）
BRAND_LABEL = "长日将尽系统"      # 图上左上角的标识

# (路径, ttc 里的序号)：贾维斯用思源黑体（序号 2 = 简体中文）；本机测试用 macOS 自带的冬青黑体
from ...platform.layout import fonts_dir

_FONT_DIR = fonts_dir()           # 开发时在包里；安装后在运行环境里（字体很大，不随每次更新重复下载）
FONT_REGULAR = [(str(_FONT_DIR / "SansSC-Regular.otf"), 0),
                ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
                ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 2),
                ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
                (r"C:\Windows\Fonts\msyh.ttc", 0)]
FONT_BOLD = [(str(_FONT_DIR / "SansSC-Bold.otf"), 0),
             ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
             ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 2),
             ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
             (r"C:\Windows\Fonts\msyhbd.ttc", 0)]

_LOCK = threading.Lock()         # 同一时间只画一张，长图吃内存
_fonts: dict = {}
_MENTION = re.compile(r"\{\{AT:\d+:([^}]*)\}\}")
_INVISIBLE = {"\ufe0f", "\ufe0e", "\u200d", "\u200b", "\u200c", "\u2060", "\ufeff"}
_RULE = re.compile(r"^[\s━─—\-=＝_~～·•.。┈┄┅]{4,}$")
_BULLET = re.compile(r"^\s*[·•・\-–]\s*")
_KV = re.compile(r"^(\S{1,12})\s{3,}(\S.*)$")     # 「玩家名单            查看所有角色」这种靠空格对齐的两栏
_ITEM = re.compile(r"^\d{1,3}[.、．]\s*\S")     # 「1. 林晚」这种编号条目，加粗
_PAIR = re.compile(r"^([^\s：:，。！？]{1,6})[：:]\s*(\S.{0,13})$")   # 短的「地点：家里」：两条并成一行，名目用浅色
_FRAMED = re.compile(r"^[=＝━─\-—~]{2,}\s*(\S.{0,24}?)\s*[=＝━─\-—~]{2,}$")   # 「==== D100 的日程 ====」两边带线：当小标题
_HEADING = re.compile(r"^【[^】]{1,20}】$|^[^，。！？\s]{1,16}[：:]$")
_LETTER_HEAD = re.compile(r"寄给.{1,16}的(?:心动)?信|你收到一封信|包含\s*\d+\s*封信件")          # 信件：个人信、心动信、飘落的信笺
_LETTER_META = re.compile(r"^来自「|你收到一封信[：:]?$")                      # 信里的署名、「某某，你收到一封信：」这种引子
_ADMIN_HINT = re.compile(r"^手动修正[：:]")                                        # 场次总览里给管理员的操作提示，图里不画（文字版仍带着）
_TIMELINE_HEAD = re.compile(r"共\s*\d+\s*条日程")                                  # 时间线（日程表）第一段「共 3 条日程，1 个群组」
_DAY_HEAD = re.compile(r"^[=＝\s]*(\S.*?)\s*的日程\s*[=＝\s]*$")                  # 「==== D1 的日程 ====」
_WECHAT_HEAD = re.compile(r"^(?:我的)?微信群$")
_EVENT_HEAD = re.compile(r"^【([^】]{1,30})】$")                                   # 「【D1 12:00-13:00】」
_SIGN = re.compile(r"^署名[：:]\s*(\S.{0,20})$")                                  # 「署名：周屿」画成信末落款
_ITEM_HEAD = re.compile(r"^[A-Za-z]{0,3}\d{1,4}\s*「[^」]{1,24}」$")             # 拍卖物品「A01 「旧唱片」」
_GROUP_HEAD = re.compile(r"^群号[：:]\s*(\d{5,})$")                            # 场次总览里每个群的抬头
_META_LINE = re.compile(r"\s[|｜]\s")                                          # 「共 2 条 | 单条上限 500 字」：头部的小字说明
_TITLE_END = re.compile(r"关系细节$")                                          # 关系线档案的标题行


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    key = (bold, size)
    if key not in _fonts:
        hit = next(((p, i) for p, i in (FONT_BOLD if bold else FONT_REGULAR) if Path(p).exists()), None)
        if hit is None:
            raise RuntimeError("找不到中文字体")
        _fonts[key] = ImageFont.truetype(hit[0], size, index=hit[1])
    return _fonts[key]


def clean(text: str) -> str:
    """@ 标记换成「@名字」，去掉不可见字符和 emoji（这些字体里没有，画出来是方块）"""
    text = _MENTION.sub(lambda m: "@" + (m.group(1) or "某人"), text or "").replace("{{ATALL}}", "@全体成员")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    out = []
    for ch in text:
        if ch in _INVISIBLE:
            continue
        cp = ord(ch)
        if cp >= 0x1F000 or 0x2300 <= cp <= 0x23FF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF:
            continue
        out.append(ch)
    return "\n".join(line.rstrip() for line in "".join(out).split("\n"))


def _wrap(draw, text: str, font, maxw: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if cur and draw.textlength(cur + ch, font=font) > maxw:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    lines.append(cur)
    return lines


def _parse_event(lines_: list[str]):
    """时间线里一个日程：「【D1 12:00-13:00】 / 私约 · 进行中 / 地点：家里 / 伙伴：周屿 / 当前进度：3v2」。认不出返回 None"""
    m = _EVENT_HEAD.match(lines_[0]) if lines_ else None
    if not m or len(lines_) < 2:
        return None
    kind, _, status = (x.strip() for x in lines_[1].partition("·"))
    if not status:
        kind, status = "", kind
    items = []
    for ln in lines_[2:]:
        k, sep, v = ln.partition("：")
        items.append((k.strip(), v.strip()) if sep and v.strip() else ("", ln))
    return {"time": m.group(1), "kind": kind, "status": status, "items": items}


def _parse_day(lines_: list[str]):
    """「==== D1 的日程 ====」→ 「D1」；「我的微信群」→ 「微信群」。认不出返回 None"""
    if len(lines_) != 1:
        return None
    m = _DAY_HEAD.match(lines_[0])
    if m:
        return m.group(1)
    return "微信群" if _WECHAT_HEAD.match(lines_[0]) else None


def _blocks(nodes):
    """把每个转发节点拆成一行行要画的东西：(种类, 文字)。种类：name 发言人 / title 标题 / head 小标题 / rule 分隔线 /
    bullet 列表项 / item 编号条目 / sub 缩进的附属行 / chip 单行短条目 / text 正文 / gap 空行 / split 节点之间的分隔"""
    base_of = lambda n: re.sub(r"\s*[·・]\s*\d+$", "", clean(str(n.get("name") or "")).strip())   # 「玩家名单·2」是续页
    speakers = {b for b in map(base_of, nodes) if b and b != BRAND and not b.startswith("【")}
    first_text = clean(str((nodes[0] if nodes else {}).get("text") or "")).strip().split("\n")[0].strip()
    letter = bool(_LETTER_HEAD.search(first_text))      # 信件：抬头当标题，正文画成信纸
    timeline = bool(_TIMELINE_HEAD.search(first_text))  # 时间线：天标题、日程卡片、状态胶囊
    out, first_node, prev = [], True, None
    for node in nodes:
        base = base_of(node)
        paras = clean(str(node.get("text") or "")).strip("\n").split("\n")
        if not any(p.strip() for p in paras):
            continue
        lines_ = [q.strip() for q in paras if q.strip()]
        if timeline:
            if first_node:                                  # 「时间线档案」：标题 + 一行小字
                out += [("title", "时间线"), ("meta", lines_[0])] + [("meta", q) for q in lines_[1:]]
                first_node = False
                continue
            ev = _parse_event(lines_)
            day = None if ev else _parse_day(lines_)
            if ev or day:
                out.append(("event", ev) if ev else ("day", day))
                prev = base
                continue
        if not first_node and not letter and len(lines_) == 1 and len(lines_[0]) <= 40 and base == prev and len(speakers) <= 1:
            # 只有一行的短条目（玩家指南后半段每条指令单独一条，原本是方便长按复制）：画成紧凑的小标签，彼此不加分隔线
            out.append(("chip", lines_[0]))
            continue
        if base != prev or not first_node:
            if not first_node:
                out.append(("split", ""))
            if base != prev and base.startswith("【"):         # 名字本身就是标题（玩家名单这种）
                out.append(("title", base))
            elif base != prev and len(speakers) > 1 and base in speakers:   # 好几个人说话（信件、复盘）才标发言人
                out.append(("name", base))
        prev = base
        nxt = lambda k: next((q.strip() for q in paras[k + 1:] if q.strip()), "")
        for k, raw in enumerate(paras):
            p = raw.strip()
            if _ADMIN_HINT.match(p):
                continue
            if not p:
                if out and out[-1][0] not in ("gap", "rule", "split"):
                    out.append(("gap", ""))
            elif letter and k == 0 and first_node and len(p) <= 18:
                out.append(("title", p))                      # 信件抬头：「寄给 某人 的信」
            elif letter and k == 0 and first_node:
                out.append(("meta", p))                       # 太长的开场白（「亲爱的 X，你有一份…包裹待启封。」）用小字，不当标题
            elif letter and _RULE.match(p):
                continue                                      # 信里的装饰虚线不画，免得把正文和署名隔开
            elif letter and _LETTER_META.search(p):
                out.append(("meta", p))
            elif letter and _SIGN.match(p):
                out.append(("sign", _SIGN.match(p).group(1)))  # 信末落款
            elif letter and not _RULE.match(p) and not _FRAMED.match(p):
                body = re.sub(r"^内容[：:]\s*", "", p)
                body = re.sub(r"^「\s*(.*?)\s*」$", r"\1", body) or p
                out.append(("letter", body))                  # 信的正文
            elif _FRAMED.match(p) and not _RULE.match(p):
                out.append(("head", _FRAMED.match(p).group(1)))
            elif _RULE.match(p):
                if out and out[-1][0] != "rule":
                    out.append(("rule", ""))
            elif k == 0 and first_node and len(p) <= 30 and (_RULE.match(nxt(k)) or (p.startswith("【") and p.endswith("】"))
                                                             or (len(p) <= 16 and k + 1 < len(paras) and not paras[k + 1].strip())):
                out.append(("title", p))                      # 第一行后面紧跟分隔线或空行 / 整行【】：当标题
            elif k == 0 and first_node and _TITLE_END.search(p) and len(p) <= 30:
                out.append(("title", p))                      # 「你与「周屿」的关系细节」
            elif first_node and k > 0 and _META_LINE.search(p):
                out.append(("meta", p))
            elif _ITEM_HEAD.match(p):
                out.append(("head", p))                       # 拍卖物品名当小标题
            elif _GROUP_HEAD.match(p):
                out.append(("head", "群号 " + _GROUP_HEAD.match(p).group(1)))
            elif _KV.match(p):
                out.append(("kv", p))
            elif _ITEM.match(p):
                out.append(("item", p))
            elif _PAIR.match(p):
                out.append(("pair", p))
            elif raw[:2] in ("  ", "　"):
                out.append(("sub", p))
            elif _HEADING.match(p):
                out.append(("head", p))
            elif _BULLET.match(p):
                out.append(("bullet", _BULLET.sub("", p, count=1)))
            else:
                out.append(("text", p))
        first_node = False
    while out and out[-1][0] in ("gap", "rule", "split"):
        out.pop()
    return out


_HEADER_NAMES = {"复盘", "戏录"}    # 复盘 / 戏录的第一段是场次信息，画成页眉（「复盘 #1」「全部复盘」也算）


def _is_header_name(name: str) -> bool:
    return bool(re.match(r"^(全部)?(复盘|戏录)(\s*#\d+)?$", name))


def _is_chat(nodes, owners) -> bool:
    """只有复盘、戏录这种真的是一场戏的才画成对话气泡（插件会带 owners，或者第一段叫「复盘 / 戏录」）。
    时间线、指南这类每段署名不同的清单也画成卡片——画成气泡太占地方"""
    if owners:
        return True
    first = re.sub(r"\s*[·・]\s*\d+$", "", clean(str((nodes[0] if nodes else {}).get("name") or "")).strip())
    return _is_header_name(first)


def _parse_places(nodes):
    """长日「地点查看」的文字（replyLong 按行切成几页合并转发）认出来、还原成结构化数据；不是地点列表返回 None。
    靠的是长日原样带过来的 emoji 标记（画图前才会被 clean 删掉）：
      🏢 地点列表 / 📍 名字（描述） / 🔑 已解锁 名字（描述） / 🔒 需要钥匙 名字（描述） / 🏠 私人房间：✅ 可用 / 💡 ……的房间……
    这样改长日那边一行不用动，只改索罗。"""
    lines = [ln.strip() for n in nodes for ln in str(n.get("text") or "").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].startswith("🏢") or "地点列表" not in lines[0]:
        return None
    items, private, last = [], "", None
    for ln in lines[1:]:
        status = None
        if ln.startswith("📍"):
            status, body = "open", ln[1:]
        elif ln.startswith("🔑"):
            status, body = "key", ln[1:].strip().removeprefix("已解锁")
        elif ln.startswith("🔒"):
            status, body = "locked", ln[1:].strip().removeprefix("需要钥匙")
        if status:
            body = body.strip()
            name, _, desc = body.partition("（")
            last = {"name": name.strip(), "desc": desc, "status": status}
            items.append(last)
        elif ln.startswith("🏠"):
            private = "off" if ("关闭" in ln or "❌" in ln) else "on"
            last = None
        elif ln.startswith("💡"):
            if not private and "的房间" in ln:
                private = "on"
            last = None
        elif last is not None and not ln.startswith("（"):
            last["desc"] += ln               # 描述太长被长日硬切到下一行 / 下一页的续文
    for it in items:
        if it["desc"].endswith("）"):
            it["desc"] = it["desc"][:-1]
    return {"items": items, "private_rooms": private} if items or private else None


def _apply_brand() -> None:
    """图上的署名用设置平台里的「系统名称」（没设就用默认的）。"""
    global BRAND, BRAND_LABEL
    try:
        from ..db import get_config
        name = get_config("brand_name", "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    BRAND = name or "长日将尽"
    BRAND_LABEL = BRAND + "系统" if not name else BRAND


def render_forward(nodes: list[dict], owners: list[str] | None = None) -> tuple[list[bytes], bool]:
    """nodes: [{"name": 发言人, "text": 内容}]；owners：这份是发给谁看的（他们的气泡放右边）。
    返回 (PNG 字节列表, 是否太长被截断)。全程在内存里。"""
    _apply_brand()
    with _LOCK:
        return _render_chat(nodes, owners or []) if _is_chat(nodes, owners) else _render(nodes)


def split_status(status: str):
    """「进行中 [⏳未回]」→ ("进行中", "wait", False)；「待开启 [🤝已接]」→ ("待开启", None, True)。
    括号里认「未回」「已回」「已接」，别的符号和表情不管"""
    status = status or ""
    main = re.split(r"[\[【]", status, maxsplit=1)[0].strip()
    rest = status[len(main):]
    reply = "wait" if "未回" in rest else ("done" if "已回" in rest else None)
    return main, reply, "已接" in rest


def _event_layout(probe, ev, body):
    """一个日程画成一整行：第一行时间 + 状态胶囊，第二行类型，后面是地点/伙伴/进度（放得下就两栏并排）"""
    half = TEXT_W // 2 - 16
    entries, items, i = [], ev["items"], 0
    width = lambda k, v: probe.textlength(f"{k}：{v}" if k else v, font=body)
    while i < len(items):
        k, v = items[i]
        if width(k, v) <= half and i + 1 < len(items) and width(*items[i + 1]) <= half:
            entries.append([("pair", k, v), ("pair",) + items[i + 1]])
            i += 2
        elif width(k, v) <= half:
            entries.append([("pair", k, v)])
            i += 1
        else:                                               # 太长放不下半栏：整行，必要时折行
            for ln in _wrap(probe, f"{k}：{v}" if k else v, body, TEXT_W):
                entries.append([("line", ln)])
            i += 1
    h = 56 + (40 if ev["kind"] else 0) + 46 * len(entries) + 26
    return h, ("event", ev, entries)


def _draw_event(d, probe, tx, right, y, row_h, ev, entries, body, bold_body):
    head_f, cap_f = _font(True, HEAD), _font(True, 22)
    d.text((tx, y + 2), ev["time"], font=head_f, fill=INK)
    main, reply, accepted = split_status(ev["status"])
    # 状态 / 已接 / 回复情况三种胶囊共用一套画法：同高、同一条中线、文字真居中，从右往左依次排开
    def pill(right_, word, bg, fg, outline=False):
        w = int(probe.textlength(word, font=cap_f)) + 32
        top, h = y + 4, 38
        d.rounded_rectangle([right_ - w, top, right_, top + h], radius=h // 2, fill=bg,
                            outline=fg if outline else None, width=2 if outline else 0)
        d.text(((right_ - w + right_) / 2, top + h / 2 + 1), word, font=cap_f, fill=fg, anchor="mm")
        return right_ - w - 10
    if main:
        bg, fg = _STATUS_STYLE.get(main, _STATUS_OTHER)
        right = pill(right, main, bg, fg)
    if accepted:                                            # 多人约「已接」
        right = pill(right, "已接", *_STATUS_OTHER)
    if reply:                                               # 未回 / 已回，在状态左边
        bg, fg = _REPLY_WAIT if reply == "wait" else _REPLY_DONE
        pill(right, "未回" if reply == "wait" else "已回", bg, fg, outline=reply != "wait")
    cy = y + 56
    if ev["kind"]:
        d.text((tx, cy - 4), ev["kind"], font=body, fill=SUBTLE)
        cy += 40
    for entry in entries:
        for col, item in enumerate(entry):
            px = tx + col * (TEXT_W // 2)
            if item[0] == "line":
                d.text((px, cy), item[1], font=body, fill=INK)
            else:
                _, k, v = item
                label = k + "：" if k else ""
                d.text((px, cy), label, font=body, fill=MUTED)
                d.text((px + probe.textlength(label, font=body), cy), v, font=body, fill=INK)
        cy += 46
    d.line([tx, y + row_h - 8, right, y + row_h - 8], fill=LINE, width=2)


def _paginate(rows, limit):
    """把排好的行切成若干页：(页列表, 是否太长被截断)。天标题不会孤零零留在页尾。"""
    pages, cur, h, cut = [], [], 0, False
    for row in rows:
        if h + row[0] > limit and cur:
            tail = []                                        # 天标题不能孤零零留在页尾：连同后面的空隙带到下一页
            while cur and (cur[-1][1] is None or cur[-1][1][0] == "day"):
                tail.insert(0, cur.pop())
            if not any(t[1] and t[1][0] == "day" for t in tail):
                cur.extend(tail)
                tail = []
            pages.append(cur)
            if len(pages) >= MAX_PAGES:
                cut = True
                break
            cur, h = tail, sum(t[0] for t in tail)
            if row[1] is None or row[1][0] in ("rule", "split"):
                continue                                     # 新页别以空行、分隔线开头
        cur.append(row)
        h += row[0]
    if not cut and cur:
        pages.append(cur)
    return pages, cut


def _render(nodes):
    blocks = _blocks(nodes)
    if not blocks:
        raise ValueError("没有可画的内容")
    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    body, bold_body = _font(False, BODY), _font(True, BODY)
    title_f, head_f, name_f = _font(True, TITLE), _font(True, HEAD), _font(True, NAME_SZ)

    # 先排成一行一行：(高度, 画法)，画法里的 y 是相对这一行顶部的
    rows = []
    for kind, text in blocks:
        if kind == "title":
            for ln in _wrap(probe, text, title_f, TEXT_W):
                rows.append((TITLE_H, ("text", 0, ln, title_f, INK)))
            rows.append((22, None))
        elif kind == "head":
            rows.append((10, None))
            for ln in _wrap(probe, text, head_f, TEXT_W):
                rows.append((HEAD_H, ("text", 0, ln, head_f, INK)))
        elif kind == "name":
            rows.append((34, ("name", 0, text, name_f, ACCENT)))
        elif kind == "rule":
            rows.append((34, ("rule",)))
        elif kind == "split":
            rows.append((34, ("split",)))
        elif kind == "day":                                   # 时间线里的天标题：色条 + 大字
            rows.append((14, None))
            rows.append((DAY_H, ("day", text)))
        elif kind == "event":                                 # 时间线里的一个日程：整张卡片当一行，翻页不会拆开
            rows.append(_event_layout(probe, text, body))
        elif kind == "meta":                                  # 头部小字说明：浅色、小一号
            for ln in _wrap(probe, text, _font(False, NAME_SZ + 2), TEXT_W):
                rows.append((40, ("text", 0, ln, _font(False, NAME_SZ + 2), MUTED)))
        elif kind == "sign":                                  # 落款：靠右、宋体、浅色
            rows.append((LINE_H + 6, ("sign", "—— " + text, _serif(False, BODY - 2))))
        elif kind == "letter":                                # 信的正文：宋体、行距宽、左边一道暖色竖线
            lf = _serif(False, BODY)
            for ln in _wrap(probe, text, lf, TEXT_W - LETTER_INDENT):
                rows.append((LINE_H + 8, ("letter", ln, lf)))
        elif kind == "gap":
            rows.append((20, None))
        elif kind == "chip":                                  # 并排摆，一行放不下再换行
            ln = _wrap(probe, text, body, TEXT_W - 2 * CHIP_PAD)[0]
            w = int(probe.textlength(ln, font=body)) + 2 * CHIP_PAD
            last = rows[-1][1] if rows else None
            if last and last[0] == "chips" and last[1] + CHIP_GAP + w <= TEXT_W:
                last[2].append((last[1] + CHIP_GAP, ln, w))
                rows[-1] = (rows[-1][0], ("chips", last[1] + CHIP_GAP + w, last[2]))
            else:
                rows.append((CHIP_GAP if last and last[0] == "chips" else 20, None))
                rows.append((LINE_H + 12, ("chips", w, [(0, ln, w)])))
        elif kind == "pair":                                  # 两条并一行：上一行还空着右半边就放进去
            k_, v_ = _PAIR.match(text).groups()
            last = rows[-1][1] if rows else None
            fits = probe.textlength(k_ + "：" + v_, font=body) <= TEXT_W // 2 - 16
            if not fits:                                      # 一栏放不下（值太长）：整行画，不并排
                rows.append((LINE_H, ("kv", max(KV_COL // 2, int(probe.textlength(k_ + "：", font=bold_body)) + 12), k_ + "：", v_)))
            elif last and last[0] == "pairs" and len(last[1]) == 1 and last[2]:
                last[1].append((k_, v_))
            else:
                rows.append((LINE_H, ("pairs", [(k_, v_)], True)))
        elif kind == "kv":
            k_, v_ = _KV.match(text).groups()
            kx = max(KV_COL, int(probe.textlength(k_, font=bold_body)) + 24)
            vlines = _wrap(probe, v_, body, TEXT_W - kx)
            rows.append((LINE_H, ("kv", kx, k_, vlines[0])))
            for ln in vlines[1:]:
                rows.append((LINE_H, ("text", kx, ln, body, SUBTLE)))
        elif kind == "item":
            rows.append((8, None))
            for ln in _wrap(probe, text, bold_body, TEXT_W):
                rows.append((LINE_H, ("text", 0, ln, bold_body, INK)))
        elif kind == "sub":
            for ln in _wrap(probe, text, body, TEXT_W - BULLET_INDENT):
                rows.append((LINE_H - 4, ("text", BULLET_INDENT, ln, body, SUBTLE)))
        elif kind == "bullet":
            for j, ln in enumerate(_wrap(probe, text, body, TEXT_W - BULLET_INDENT)):
                rows.append((LINE_H, ("bullet" if j == 0 else "text", BULLET_INDENT, ln, body, INK)))
        else:
            for ln in _wrap(probe, text, body, TEXT_W):
                rows.append((LINE_H, ("text", 0, ln, body, INK)))

    # 分页：卡片顶部留品牌行，底部留页脚
    head_h = 56
    limit = PAGE_MAX_H - FOOT_H - 2 * OUTER - 2 * PAD_Y - head_h
    pages, cut = _paginate(rows, limit)

    out, total = [], len(pages)
    foot_f, brand_f = _font(False, FOOT_SZ), _font(True, BRAND_SZ)
    for n, page in enumerate(pages, 1):
        content_h = sum(r[0] for r in page)
        card_h = 2 * PAD_Y + head_h + content_h
        img = Image.new("RGB", (W, OUTER + card_h + FOOT_H), PAPER2)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = OUTER, OUTER, W - OUTER, OUTER + card_h
        d.rounded_rectangle([x0, y0 + 3, x1, y1 + 3], radius=28, fill=P.CHIP_GREY)     # 一点点投影
        d.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=PAPER, outline=LINE, width=2)
        tx, y = x0 + PAD_X, y0 + PAD_Y
        d.rounded_rectangle([tx, y + 4, tx + 6, y + 26], radius=3, fill=ACCENT)
        d.text((tx + 18, y), BRAND_LABEL, font=brand_f, fill=MUTED)
        y += head_h
        for row_h, op in page:
            if op is None:
                pass
            elif op[0] == "rule":
                d.line([tx, y + row_h // 2, x1 - PAD_X, y + row_h // 2], fill=LINE, width=2)
            elif op[0] == "split":
                mid = y + row_h // 2
                d.line([tx, mid, x1 - PAD_X, mid], fill=LINE, width=2)
            elif op[0] == "chips":
                for cx, ln, w in op[2]:
                    d.rounded_rectangle([tx + cx, y, tx + cx + w, y + row_h], radius=14, fill=PAPER2, outline=LINE, width=1)
                    d.text((tx + cx + CHIP_PAD, y + (row_h - BODY) // 2 - 4), ln, font=body, fill=INK)
            elif op[0] == "pairs":
                ty = y + (row_h - BODY) // 2 - 4
                for col, (k_, v_) in enumerate(op[1]):
                    px = tx + col * (TEXT_W // 2)
                    d.text((px, ty), k_ + "：", font=body, fill=MUTED)
                    d.text((px + probe.textlength(k_ + "：", font=body), ty), v_, font=body, fill=INK)
            elif op[0] == "kv":
                ty = y + (row_h - BODY) // 2 - 4
                d.text((tx, ty), op[2], font=bold_body, fill=INK)
                d.text((tx + op[1], ty), op[3], font=body, fill=SUBTLE)
            elif op[0] == "day":
                green = op[1] == "微信群"                      # 微信群单独一个区块，色条换成绿色
                d.rounded_rectangle([tx, y + 12, tx + 9, y + row_h - 12], radius=4, fill=(96, 150, 118) if green else ACCENT)
                d.text((tx + 28, y + (row_h - TITLE) // 2 - 4), op[1], font=title_f, fill=INK)
            elif op[0] == "event":
                _draw_event(d, probe, tx, x1 - PAD_X, y, row_h, op[1], op[2], body, bold_body)
            elif op[0] == "sign":
                sw = probe.textlength(op[1], font=op[2])
                d.text((x1 - PAD_X - sw, y + (row_h - BODY) // 2 - 4), op[1], font=op[2], fill=MUTED)
            elif op[0] == "letter":
                d.rectangle([tx, y, tx + 4, y + row_h - 1], fill=(232, 196, 178))   # 直角：同一段换行时竖线连成一条
                d.text((tx + LETTER_INDENT, y + (row_h - BODY) // 2 - 4), op[1], font=op[2], fill=INK)
            elif op[0] == "bullet":
                cy = y + row_h // 2
                d.ellipse([tx + 6, cy - 5, tx + 16, cy + 5], fill=ACCENT)
                d.text((tx + op[1], y + (row_h - op[3].size) // 2 - 4), op[2], font=op[3], fill=op[4])
            else:
                d.text((tx + op[1], y + (row_h - op[3].size) // 2 - 4), op[2], font=op[3], fill=op[4])
            y += row_h
        foot = (f"第 {n}/{total} 张" if total > 1 else "") + ("　内容太长，后面的省略了" if cut and n == total else "")
        if foot:
            d.text((W // 2, img.height - FOOT_H // 2), foot, font=foot_f, fill=MUTED, anchor="mm")
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        out.append(buf.getvalue())
    return out, cut


# ───────────────────────── 对话版式（照戏录长图） ─────────────────────────
CHAT_BG, CHAT_RULE = P.BG, P.RULE
MINE_BG, MINE_FG, THEIRS_BORDER = P.PURPLE, P.WHITE, P.RULE
# 其他发言人的气泡色板：取自四套参考配色（Powder Blush / Muted Teal / Pacific Cyan / Wheat / Rich Cerulean /
# Powder Blue / French Blue / Light Cyan / Wisteria Blue / Medium Slate Blue），已去掉互相太接近、或和米色底太接近的颜色。
# 每个人的颜色只由名字决定，全系统固定；底色偏深的自动用白字；owners 固定右侧 Dark Amethyst 白字，不入色板
SPEAKER_PALETTE = [
    ((246, 182, 183), (211, 156, 157)),   # Powder Blush F6B6B7
    ((166, 201, 182), (142, 172, 156)),   # Muted Teal A6C9B6
    ((62, 130, 142), (53, 111, 122)),   # Pacific Cyan 3E828E
    ((246, 224, 182), (211, 192, 156)),   # Wheat F6E0B6
    ((48, 120, 164), (41, 103, 141)),   # Rich Cerulean 3078A4
    ((166, 188, 201), (142, 161, 172)),   # Powder Blue A6BCC9
    ((62, 75, 142), (53, 64, 122)),   # French Blue 3E4B8E
    ((224, 250, 255), (192, 215, 219)),   # Light Cyan E0FAFF
    ((128, 164, 212), (110, 141, 182)),   # Wisteria Blue 80A4D4
    ((123, 117, 201), (105, 100, 172)),   # Medium Slate Blue 7B75C9
]


def _bubble_fg(sty: int):
    """气泡底色偏深（相对亮度 < 0.4）的用白字，其余用黑字；owners（sty<0）固定白字。"""
    if sty < 0:
        return MINE_FG
    lin = [(v / 255 / 12.92) if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in SPEAKER_PALETTE[sty][0]]
    return (255, 255, 255) if 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] < 0.4 else INK


def _speaker_style(who: str) -> int:
    """名字哈希取色板下标：同一个人在任何图里颜色都一样。"""
    return int(hashlib.md5(who.encode("utf-8")).hexdigest(), 16) % len(SPEAKER_PALETTE)


C_FOOT_H, C_FOOT_SZ = 96, 30      # 对话版式底部页码条：高度、字号（多页时「第 1/3 页」）
C_MARGIN, C_BUBBLE_MAX, C_PAD_X, C_PAD_Y = 40, 700, 28, 18
C_BODY, C_LINE_H, C_NAME, C_TITLE, C_SUB = 32, 50, 24, 50, 28
SERIF_REGULAR = [(str(_FONT_DIR / "SerifSC-Regular.otf"), 0),
                 ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 2),
                 ("/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc", 2),
                 ("/System/Library/Fonts/Supplemental/Songti.ttc", 6)]
SERIF_BOLD = [(str(_FONT_DIR / "SerifSC-Regular.otf"), 0),
              ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 2),
              ("/usr/share/fonts/noto-cjk/NotoSerifCJK-Bold.ttc", 2),
              ("/System/Library/Fonts/Supplemental/Songti.ttc", 1)]


def _serif(bold: bool, size: int):
    key = ("serif", bold, size)
    if key not in _fonts:
        hit = next(((p, i) for p, i in (SERIF_BOLD if bold else SERIF_REGULAR) if Path(p).exists()), None)
        _fonts[key] = ImageFont.truetype(hit[0], size, index=hit[1]) if hit else _font(bold, size)   # 没宋体就用黑体
    return _fonts[key]


def _render_chat(nodes, owners):
    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    body, name_f = _serif(False, C_BODY), _serif(False, C_NAME)
    title_f, sub_f = _serif(True, C_TITLE), _serif(False, C_SUB)
    owners = {clean(o).strip() for o in owners}
    pages, ops, y, cut = [], [], 0, False

    def new_page():
        nonlocal ops, y
        if ops:
            pages.append(ops)
        ops, y = [], (84 if pages else 36)      # 后面几页顶部留出右上角「n / total」的位置

    new_page()
    # 左上角标识
    ops.append(("bar", C_MARGIN, y + 4, C_MARGIN + 6, y + 26))
    ops.append(("text", C_MARGIN + 18, y, BRAND_LABEL, _font(True, BRAND_SZ), MUTED))
    y += 56
    items = list(nodes)
    first = clean(str(items[0].get("name") or "")).strip() if items else ""
    if items and _is_header_name(first):             # 页眉：第一行大字，其余小字，下面一条线
        head = [ln.strip() for ln in clean(str(items[0].get("text") or "")).split("\n") if ln.strip()]
        items = items[1:]
        for k, ln in enumerate(head):
            f, color = (title_f, INK) if k == 0 else (sub_f, MUTED if k == 1 else INK)
            for w in _wrap(probe, ln, f, W - 2 * C_MARGIN):
                ops.append(("text", C_MARGIN, y, w, f, color))
                y += (C_TITLE + 22) if k == 0 else (C_SUB + 14)
        y += 10
        ops.append(("rule", C_MARGIN, y, W - C_MARGIN))
        y += 14
    limit = PAGE_MAX_H - C_FOOT_H
    prev = None
    broke = False                      # 刚换页：同一个人接着说，名字后面补「接上页」
    for node in items:
        who = re.sub(r"\s*[·・]\s*\d+$", "", clean(str(node.get("name") or "")).strip()) or "未知"
        text = clean(str(node.get("text") or "")).strip()
        if not text:
            continue
        mine = who in owners
        sty = -1 if mine else _speaker_style(who)
        lines = [w for para in text.split("\n") for w in _wrap(probe, para, body, C_BUBBLE_MAX)]   # 先按换行拆段再折行
        first_of_run = who != prev
        prev_who, prev = prev, who
        i = 0
        while i < len(lines):
            label_h = 36 if first_of_run else 0
            gap = 24 if first_of_run else 8
            avail = limit - y - gap - label_h - 2 * C_PAD_Y
            n = min(len(lines) - i, max(int(avail // C_LINE_H), 0))
            if n < 1 or (n < len(lines) - i and n < 2):
                if len(pages) + 1 >= MAX_PAGES:
                    cut = True
                    break
                new_page()
                first_of_run, broke = True, True
                continue
            chunk = lines[i:i + n]
            y += gap
            if label_h:
                label = who + ("　（接上页）" if broke and (i > 0 or who == prev_who) else "")
                nx = W - C_MARGIN - 10 - int(probe.textlength(label, font=name_f)) if mine else C_MARGIN + 10   # owners 的名字靠右
                ops.append(("text", nx, y, label, name_f, MUTED))
                y += label_h
            w = int(max(probe.textlength(l, font=body) for l in chunk)) + 2 * C_PAD_X
            h = len(chunk) * C_LINE_H + 2 * C_PAD_Y - 6
            x0 = W - C_MARGIN - w if mine else C_MARGIN
            ops.append(("bubble", x0, y, x0 + w, y + h, sty))
            for k, l in enumerate(chunk):
                ops.append(("text", x0 + C_PAD_X, y + C_PAD_Y - 4 + k * C_LINE_H, l, body, _bubble_fg(sty)))
            y += h
            i += n
            first_of_run = broke = False
        if cut:
            break
    pages.append(ops)
    pages = [p for p in pages if any(o[0] in ("bubble", "text") for o in p)]
    if not pages:
        raise ValueError("没有可画的内容")
    out, total = [], len(pages)
    foot_f, tag_f = _font(True, C_FOOT_SZ), _font(True, 26)
    for n, page in enumerate(pages, 1):
        bottom = max([o[4] + 40 if o[0] in ("bubble", "bar") else o[2] + 40 for o in page] + [200])
        img = Image.new("RGB", (W, bottom + C_FOOT_H), CHAT_BG)
        d = ImageDraw.Draw(img)
        for o in page:
            if o[0] == "text":
                d.text((o[1], o[2]), o[3], font=o[4], fill=o[5])
            elif o[0] == "rule":
                d.line([(o[1], o[2]), (o[3], o[2])], fill=CHAT_RULE, width=2)
            elif o[0] == "bar":
                d.rounded_rectangle([o[1], o[2], o[3], o[4]], radius=3, fill=ACCENT)
            else:
                fill, edge = (MINE_BG, None) if o[5] < 0 else SPEAKER_PALETTE[o[5]]
                d.rounded_rectangle([o[1], o[2], o[3], o[4]], radius=30, fill=fill, outline=edge, width=2)
        if total > 1:        # 右上角也标一个，翻到哪张都能看到
            d.text((W - C_MARGIN, 36 + 15), f"{n} / {total}", font=tag_f, fill=INK, anchor="rm")
        foot = (f"第 {n}/{total} 页" if total > 1 else "") + ("　内容太长，后面的省略了" if cut and n == total else "")
        if foot:
            d.text((W // 2, img.height - C_FOOT_H // 2 - 4), foot, font=foot_f, fill=SUBTLE, anchor="mm")
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        out.append(buf.getvalue())
    return out, cut
