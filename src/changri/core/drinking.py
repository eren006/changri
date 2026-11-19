"""喝酒游戏（晚餐里的小游戏，开了晚餐就能玩）：照搬老的「喝酒游戏（醉酒名单版）」（作者 长日将尽，Apache-2）。

玩法：大家「加入喝酒游戏」→ 有人「开始喝酒游戏」→「上酒」摆 24 杯（公布有哪些酒、各加多少醉酒值，但不说哪杯在几号）
→ 每人「喝酒 位置号」盲选一杯，醉酒值加上这杯的点数（无酒精的会减）；到阈值就进「醉酒名单」，不能再喝。
24 杯喝完再「上酒」。「结束喝酒游戏」公布醉酒名单（醉得最厉害的排第一）和幸存者。每个群一局，互不影响。
状态存在 config 的 drink_game（JSON：{群: 状态}）。"""
import json
import random
import time

from .db import get_config, get_int, set_config

DRINKS = [
    ("啤酒", "拉格", 10, "一种清爽的淡色啤酒，适合夏天畅饮。"),
    ("啤酒", "艾尔", 10, "一款稍微浓烈的啤酒，味道偏重。"),
    ("啤酒", "白啤", 15, "一种浓郁的啤酒，口感更加顺滑，适合搭配小吃。"),
    ("啤酒", "帝国 Stout", 20, "深色浓烈的啤酒，带有咖啡和巧克力的香气。"),
    ("啤酒", "小麦啤酒", 15, "使用小麦酿造，口感轻盈，带有香气。"),
    ("啤酒", "苦啤", 18, "非常苦的啤酒，适合喜欢浓烈味道的饮酒者。"),
    ("红酒", "赤霞珠", 20, "经典的红酒，带有浓郁的果香和单宁味。"),
    ("红酒", "梅洛", 20, "温和口感，适合搭配大多数食物。"),
    ("红酒", "黑皮诺", 25, "柔顺的红酒，果香浓郁，口感极为顺滑。"),
    ("红酒", "桑娇维塞", 22, "带有酸度和草本香气的红酒，适合搭配烧烤。"),
    ("红酒", "意大利巴罗洛", 30, "来自意大利的高端红酒，带有坚果和玫瑰的香气。"),
    ("白葡萄酒", "冰酒", 70, "甜美的酒，带有浓郁的果香，适合餐后享用。"),
    ("白葡萄酒", "霞多丽", 55, "具有丰富的果香和微妙的橡木味，口感圆润。"),
    ("白葡萄酒", "长相思", 50, "清新、爽口，带有柑橘和青草的香气。"),
    ("白葡萄酒", "雷司令", 60, "带有较强的果香，适合与辛辣食物搭配。"),
    ("白葡萄酒", "灰比诺", 50, "带有蜂蜜和苹果的香气，口感清新。"),
    ("烈酒", "伏特加 - 斯托尔", 40, "无色无味的烈酒，适合与其他饮品调配，口感干净且清爽。"),
    ("烈酒", "伏特加 - 绝对伏特加", 45, "瑞典生产的高端伏特加，口感纯净，适合调制鸡尾酒。"),
    ("烈酒", "威士忌 - 苏格兰单一麦芽", 50, "由单一麦芽酿造，带有浓烈的烟熏味和木桶味。"),
    ("烈酒", "威士忌 - 美国波本", 45, "甜味浓郁，带有香草、焦糖和橡木的香气。"),
    ("烈酒", "威士忌 - 加拿大混合", 38, "更加顺滑，香气优雅，适合饮用或调制鸡尾酒。"),
    ("烈酒", "龙舌兰 - 白龙舌兰", 35, "透明、浓烈且带有辛辣感的酒，可直接饮用或调制鸡尾酒。"),
    ("烈酒", "龙舌兰 - 金龙舌兰", 40, "金黄色的龙舌兰，经过橡木桶熟成，味道更加柔和且富有层次感。"),
    ("烈酒", "龙舌兰 - 额尔多斯", 42, "顶级龙舌兰，口感极其细腻，适合纯饮。"),
    ("烈酒", "朗姆酒 - 白朗姆", 35, "无色的朗姆酒，口感清爽，适合调配鸡尾酒。"),
    ("烈酒", "朗姆酒 - 黑朗姆", 45, "深色朗姆酒，带有浓烈的焦糖和香料味。"),
    ("烈酒", "朗姆酒 - 椰子朗姆", 40, "带有浓郁椰子香气的朗姆酒，适合制作热带鸡尾酒。"),
    ("白酒", "茅台", 50, "浓烈的中国白酒，具有深厚的酒香和辛辣的味道。"),
    ("白酒", "五粮液", 45, "中国传统白酒，以五种粮食酿成，风味独特。"),
    ("白酒", "泸州老窖", 48, "带有酒香和果香的中国传统白酒，口感顺滑。"),
    ("鸡尾酒", "马提尼", 30, "经典鸡尾酒，通常用金酒和苦艾酒调制而成。"),
    ("鸡尾酒", "长岛冰茶", 25, "口感清新，甜中带点酸，适合聚会时饮用。"),
    ("鸡尾酒", "血腥玛丽", 30, "含有番茄汁、伏特加、香料等，味道辛辣、酸爽。"),
    ("鸡尾酒", "龙舌兰日出", 28, "由龙舌兰、橙汁和石榴糖浆调制而成，色彩鲜艳，口感甘甜。"),
    ("鸡尾酒", "老式鸡尾酒", 35, "经典的威士忌鸡尾酒，加入糖和苦精，味道浓郁。"),
    ("香槟", "干型", 30, "酒体清新，适合庆祝场合，味道略带辛辣。"),
    ("香槟", "甜型", 35, "带有甜味，口感柔和，适合搭配甜点。"),
    ("香槟", "年份香槟", 60, "高端香槟，年份较长，带有更加丰富的香气和口感。"),
    ("清酒", "吟酿", 25, "日本传统的米酒，味道柔和，适合品尝。"),
    ("清酒", "大吟酿", 40, "最高级的清酒，口感丰富，带有花香和米香。"),
    ("清酒", "普通清酒", 15, "传统的日本清酒，适合餐桌上享用。"),
    ("果酒", "苹果酒", 12, "由苹果酿造的果酒，口感酸甜，适合开胃。"),
    ("果酒", "樱桃酒", 18, "由樱桃酿成的果酒，带有浓郁的果香和甜美的味道。"),
    ("果酒", "蓝莓酒", 20, "使用蓝莓酿成的果酒，带有蓝莓的浓郁果香。"),
    ("无酒精", "橙汁", -10, "富含维生素C，清爽又健康的果汁。"),
    ("无酒精", "白水", -5, "最基础的饮品，解渴的最佳选择。"),
    ("无酒精", "苏打水", -8, "气泡丰富的饮料，清爽解渴。"),
    ("无酒精", "咖啡", -5, "苦香醇厚的咖啡，提神醒脑。"),
    ("无酒精", "抹茶拿铁", -7, "融合了抹茶和牛奶的饮品，口感顺滑。"),
    ("无酒精", "椰子水", -6, "天然的电解质饮品，适合补充水分。"),
    ("无酒精", "奶昔", -9, "甜美的奶昔，通常搭配巧克力、香草或草莓口味。"),
    ("无酒精", "热巧克力", -6, "香浓的热巧克力，适合寒冷天气饮用。"),
    ("无酒精", "蜂蜜柠檬水", -7, "富含维生素C的健康饮品，甜酸可口。"),
    ("无酒精", "冰红茶", -8, "清凉的冰红茶，略带甜味，适合解暑。")
]
CUPS = 24
rng = random.Random()


def threshold() -> int:
    return max(1, get_int("drink_threshold", 500))


def _all() -> dict:
    try:
        d = json.loads(get_config("drink_game", "") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _get(group: str) -> dict:
    return _all().get(group) or {"players": {}, "drunk": [], "cups": [], "drunk_cups": [], "active": False}


def _put(group: str, s: dict | None) -> None:
    d = _all()
    if s is None:
        d.pop(group, None)
    else:
        d[group] = s
    set_config("drink_game", json.dumps(d, ensure_ascii=False))


def join(group: str, name: str) -> tuple[bool, str]:
    s = _get(group)
    if s["active"]:
        return False, "游戏已经开始了，下一局再来。"
    if name in s["players"]:
        return False, "你已经加入了。"
    if any(p["name"] == name for p in s["drunk"]):
        return False, "你已经在醉酒名单里了。"
    s["players"][name] = 0
    _put(group, s)
    return True, f"✅ {name} 加入了喝酒游戏！现在 {len(s['players'])} 人。"


def start(group: str) -> tuple[bool, str]:
    s = _get(group)
    if s["active"]:
        return False, "游戏已经开始了。"
    if not s["players"]:
        return False, "还没有人加入：先「加入喝酒游戏」。"
    s["active"] = True
    _put(group, s)
    return True, f"🎮 喝酒游戏开始！{len(s['players'])} 人：{'、'.join(s['players'])}\n醉酒阈值：{threshold()} 点。发「上酒」摆酒。"


def serve(group: str) -> tuple[bool, str]:
    s = _get(group)
    if not s["active"]:
        return False, "游戏还没开始：先「开始喝酒游戏」。"
    if not s["players"]:
        return False, "所有人都喝醉了！发「结束喝酒游戏」看结果。"
    if s["cups"] and not all(s["drunk_cups"]):
        return False, "还有没喝完的酒，喝完才能再上。"
    s["cups"] = [rng.randrange(len(DRINKS)) for _ in range(CUPS)]
    s["drunk_cups"] = [False] * CUPS
    _put(group, s)
    shown = sorted(s["cups"], key=lambda i: (DRINKS[i][0], rng.random()))       # 按种类列出来，但不说哪杯在几号
    lines = ["🥂 新一轮酒上桌了！一共 24 杯，号码 1–24，「喝酒 号码」盲选一杯。", f"醉酒阈值：{threshold()} 点", ""]
    cur = None
    for i in shown:
        t, sub, v, desc = DRINKS[i]
        if t != cur:
            lines.append(f"【{t}】")
            cur = t
        lines.append(f"  · {sub}（{'+' if v > 0 else ''}{v}）{desc}")
    return True, "\n".join(lines)


def drink(group: str, name: str, pos_text: str) -> tuple[bool, str]:
    s = _get(group)
    if not s["active"]:
        return False, "游戏还没开始。"
    if not s["cups"]:
        return False, "还没上酒：先「上酒」。"
    if name not in s["players"]:
        return False, "你已经喝醉了，不能再喝了。" if any(p["name"] == name for p in s["drunk"]) else "你还没加入喝酒游戏。"
    from .textutil import parse_id
    n = parse_id(pos_text)
    if not n or not 1 <= n <= CUPS:
        return False, f"要写 1 到 {CUPS} 的号码，例：喝酒 7"
    i = n - 1
    if s["drunk_cups"][i]:
        return False, f"{i + 1} 号已经被喝掉了，换一杯。还剩：{_left_text(s)}"
    t, sub, v, desc = DRINKS[s["cups"][i]]
    s["drunk_cups"][i] = True
    s["players"][name] += v
    now = s["players"][name]
    lim = threshold()
    out = [f"🍻 {name} 喝了 {i + 1} 号：{t}（{sub}）", f"📝 {desc}", f"📊 醉酒值 {'+' if v > 0 else ''}{v}，现在 {now}/{lim}"]
    if now >= lim:
        s["drunk"].append({"name": name, "value": now, "ts": int(time.time())})
        del s["players"][name]
        out.append(f"\n💀 {name} 喝醉了！进了醉酒名单。还剩 {len(s['players'])} 人。")
        if not s["players"]:
            out.append("🎉 所有人都喝醉了！发「结束喝酒游戏」看结果。")
    if all(s["drunk_cups"]) and s["players"]:
        out.append("🍶 这一轮的酒都喝完了，发「上酒」继续。")
    _put(group, s)
    return True, "\n".join(out)


def _left_text(s: dict) -> str:
    left = [str(i + 1) for i, d in enumerate(s["drunk_cups"]) if not d]
    return "、".join(left) or "（没有了）"


def left(group: str) -> str:
    s = _get(group)
    if not s["cups"]:
        return "还没上酒。"
    return f"🥂 还能喝的号码（{sum(1 for d in s['drunk_cups'] if not d)} 杯）：{_left_text(s)}"


def status(group: str) -> str:
    s = _get(group)
    lim = threshold()
    lines = [f"🎮 喝酒游戏（{'进行中' if s['active'] else '没开始'}，阈值 {lim}）", f"🏃 还清醒（{len(s['players'])}）："]
    for n, v in sorted(s["players"].items(), key=lambda x: -x[1]):
        p = max(0, min(100, v * 100 // lim))
        lines.append(f"  {n}：{v}/{lim} [{'█' * (p // 10)}{'░' * (10 - p // 10)}] {p}%")
    lines.append(f"💀 醉酒名单（{len(s['drunk'])}）：" + ("、".join(f"{p['name']} {p['value']}" for p in sorted(s["drunk"], key=lambda x: -x["value"])) or "暂无"))
    if s["cups"]:
        lines.append(f"🥂 酒池：还剩 {sum(1 for d in s['drunk_cups'] if not d)}/{CUPS} 杯")
    return "\n".join(lines)


def drunk_list(group: str) -> str:
    s = _get(group)
    if not s["drunk"]:
        return "💀 醉酒名单还是空的。"
    return "💀 醉酒名单\n" + "\n".join(f"{i}. {p['name']}：{p['value']} 点" for i, p in enumerate(sorted(s["drunk"], key=lambda x: -x["value"]), 1))


def end(group: str) -> tuple[bool, str]:
    s = _get(group)
    if not s["active"] and not s["players"] and not s["drunk"]:
        return False, "现在没有喝酒游戏。"
    medal = ["🏆", "🥈", "🥉"]
    drunk = sorted(s["drunk"], key=lambda x: -x["value"])
    alive = sorted(s["players"].items(), key=lambda x: -x[1])
    out = ["🎮 喝酒游戏结束！", "", "💀【醉酒名单】"]
    out += [f"{medal[i] if i < 3 else ''}{i + 1}. {p['name']}：{p['value']} 点" for i, p in enumerate(drunk)] or ["  没有人喝醉"]
    out += ["", "🏃【幸存者】"] + ([f"  {n}：{v} 点" for n, v in alive] or ["  无"])
    out += ["", f"📊 一共 {len(drunk) + len(alive)} 人，醉了 {len(drunk)} 人。"]
    _put(group, None)
    return True, "\n".join(out)


def reset(group: str) -> None:
    _put(group, None)


def set_threshold(n: int) -> tuple[bool, str]:
    if n < 10 or n > 100000:
        return False, "阈值写 10 到 100000 之间的数。"
    set_config("drink_threshold", str(n))
    return True, f"醉酒阈值改成 {n} 点（所有群）。"
