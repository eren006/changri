"""晚餐（额外）：管理员开一桌 N 个座位，玩家入座，相邻座位（首尾也算相邻）可以「戳你一下」。
状态存在 config 的 dinner_state（JSON）里：{"max": 座位数, "era": 风格, "dishes": [...], "seats": [角色名或 null]}。"""
import json
import random
import time

from .db import get_config, get_int, set_config

MENUS = {
    "现代中餐": ["东坡肉", "宫保鸡丁", "清蒸鲈鱼", "麻婆豆腐", "蒜蓉西兰花", "腌笃鲜", "扬州炒饭", "北京烤鸭", "佛跳墙"],
    "现代西餐": ["惠灵顿牛排", "法式洋葱汤", "香煎干贝", "松露意面", "凯撒沙拉", "波士顿龙虾", "红酒炖牛肉", "提拉米苏"],
    "古代中餐": ["花炊鹌子", "炙金肠", "洗手蟹", "羹腊兔", "莲花鸭签", "拨鱼儿", "玉灌肺", "金乳酥", "山煮羊"],
    "古代西餐": ["烤野猪肉", "蜂蜜炖鹅", "香料葡萄酒", "中世纪黑面包", "麦芽糊", "盐渍鹿肉", "肉豆蔻烤鱼", "无花果挞"],
    "诡秘深渊": ["不可名状的触手羹", "深潜者之卵", "米戈真菌刺身", "发光的紫色浓汤", "蠕动的肉块饼", "拉莱耶深海藻泥"],
    "赛博未来": ["合成蛋白块", "营养液胶囊(草莓味)", "人造合成和牛", "霓虹酒精饮料", "增强现实全息布丁"],
}


def state() -> dict | None:
    try:
        s = json.loads(get_config("dinner_state", "") or "null")
    except ValueError:
        return None
    return s if isinstance(s, dict) and s.get("max") else None


def _save(s: dict | None) -> None:
    set_config("dinner_state", json.dumps(s, ensure_ascii=False) if s else "")


def start(n: int, era: str = "现代中餐") -> tuple[bool, str]:
    if n < 2 or n > 30:
        return False, "座位数要在 2 到 30 之间。"
    dishes = [] if era == "无菜" else random.sample(MENUS.get(era, ["家常小菜"]), min(len(MENUS.get(era, ["家常小菜"])), 5 + n // 3))
    _save({"max": n, "era": era, "dishes": dishes, "seats": [None] * n})
    return True, render(state(), "🍽️ 【晚餐开始】")


def stop() -> bool:
    had = state() is not None
    _save(None)
    return had


def render(s: dict, head: str | None = None) -> str:
    out = [head or f"🍽️ 【晚餐席位 - {s['era']}】"]
    if head and s["era"]:
        out.append(f"风格：{s['era']}")
    if head and s["dishes"]:
        out.append("菜谱：" + "、".join(s["dishes"]))
    out.append("────────────")
    out += [f"{i + 1}. {n or '（空位）'}" for i, n in enumerate(s["seats"])]
    return "\n".join(out)


def sit(name: str, seat_no: int) -> tuple[bool, str]:
    s = state()
    if not s:
        return False, "晚餐还没开始，等管理员「开始晚餐」。"
    if not 1 <= seat_no <= s["max"]:
        return False, f"请输入有效的座位号（1-{s['max']}）。"
    i = seat_no - 1
    if s["seats"][i] not in (None, name):
        return False, f"座位 {seat_no} 已经被 {s['seats'][i]} 坐了，换一个空位。"
    if s["seats"][i] == name:
        return False, f"你已经在座位 {seat_no} 上了。"
    s["seats"] = [None if x == name else x for x in s["seats"]]     # 换座：先离开旧座位
    s["seats"][i] = name
    _save(s)
    return True, render(s)


def cooldown_left(role_id: int) -> int:
    cd = get_int("dinner_poke_cooldown_min", 5) * 60
    last = get_int(f"poke_at:{role_id}", 0)
    return max(0, last + cd - int(time.time())) if cd > 0 else 0


def poke_check(me_name: str, target: str) -> tuple[str, str]:
    """能戳返回 ("", ""),否则 (原因, "")。座位相邻（首尾算相邻）才行。"""
    s = state()
    if not s:
        return "现在没有进行中的晚餐，不能戳人。", ""
    seats = s["seats"]
    if me_name not in seats:
        return "你还没有入座，先「入座 座位号」。", ""
    if target not in seats:
        return f"「{target}」不在晚餐座位上。", ""
    a, b = seats.index(me_name), seats.index(target)
    if a == b:
        return "不能戳自己哦～", ""
    n = s["max"]
    if (a - b) % n not in (1, n - 1):
        return f"你只能戳相邻座位的人（你在 {a + 1} 号，{target} 在 {b + 1} 号）。", ""
    return "", ""


def mark_poked(role_id: int) -> None:
    set_config(f"poke_at:{role_id}", str(int(time.time())))
