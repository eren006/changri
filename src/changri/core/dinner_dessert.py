"""晚餐小游戏：送甜点（毒果）。照搬老系统「假面舞会.js」的送毒果，改成跟着晚餐走。

· 入座的人在自己的个人群「领取甜点」：拿到 3 道甜点（2 道有毒、1 道真甜点），只有领的这一刻告诉你哪道是哪道。
  一局晚餐每人领一次；同一局里甜点名字不重复（池子够 30 个座位 × 3 道）。
· 「送甜点 角色名 甜点名」：在自己的个人群发，把一道甜点送给另一位入座的人。晚餐群里匿名播报「有人给 X 送了 Y」，
  对方个人群收到提醒（写了是谁送的）。冷却在设置「晚餐」组，格式错、找不到人不算冷却。
· 「我的甜点」：只列名字和谁送的，不显示真假——收到的东西是毒是甜，自己也不知道。
· 管理员「全部甜点」看每个人手里的真假和来源；「揭晓甜点」公布全部真假并排名（真甜点多的在前，同样多毒的少的在前），
  揭晓后这局不能再送。「开始晚餐」「结束晚餐」都会清掉这局甜点。
状态存在 config 的 dessert_game（JSON）：{"inv": {角色名: [{"name","poison","from"}]}, "claimed": [角色名], "used": [甜点名], "revealed": bool}。
按角色名记（跟晚餐座位一样）。"""
import json
import random
import time

from . import dinner
from .db import get_config, get_int, set_config

POOL = [
    "柠檬杏仁塔", "石榴蜜饼", "橙花芝士卷", "无花果蜂蜜派", "樱桃酒浸蛋糕", "葡萄干蜜酥卷", "草莓奶油小盏", "榅桲果馅饼",
    "覆盆子玫瑰慕斯", "栗子松露球", "杏仁蜜梨挞", "蜜渍无花果芝士杯", "橄榄油柑橘蛋糕", "葡萄酒炖梨派", "西西里柠檬奶油卷",
    "蜂蜜苹果布丁", "蓝莓迷迭香塔", "杏仁橙花蛋糕", "马斯卡彭樱桃杯", "葡萄柚糖渍片", "开心果蜜橙挞", "甜酒浸提拉米苏",
    "黑莓奶油泡芙", "糖渍玫瑰梨盏", "杏桃果酱千层酥", "蜜渍橄榄杏仁卷", "葡萄干朗姆蛋糕", "蜂蜜无花果派", "柑橘杏仁布朗尼",
    "玫瑰荔枝慕斯", "香草杏仁奶冻", "烤梨杏仁塔", "酒渍樱桃巧克力球", "无花果杏仁挞", "柠檬马鞭草小蛋糕", "榛子覆盆子蛋糕",
    "橄榄油葡萄干饼", "焦糖苹果小塔", "白葡萄酒梨子冻", "百香果奶油泡芙", "青提杏仁布丁", "红醋栗芝士慕斯", "佛手柑杏仁酥",
    "金橘蜜酱卷", "香料苹果蜂蜜塔", "樱桃开心果奶酥塔", "无花果香草奶油卷", "橙酒蜜渍草莓杯", "焦糖柠檬蛋白派",
    "白兰地苹果干酥饼", "蜂蜜石榴奶冻盏", "蓝莓薰衣草慕斯", "酒渍葡萄奶油泡芙", "蜜渍杏仁葡萄干卷", "香料梨焦糖布丁",
    "白桃香草慕斯盏", "黑樱桃杏仁奶酥卷", "覆盆子柠檬芝士塔", "无花果红酒果冻杯", "杏梨蜂蜜酥皮卷", "百香果椰奶奶冻",
    "桑葚玫瑰奶油派", "青苹果薄荷蛋白饼", "李子肉桂奶酥盏", "蜜橙香草奶酪卷",
    # 老系统 66 道之外补的，凑够 30 个座位 × 3 道
    "托斯卡纳杏仁脆饼", "圣洁之酒泡饼干", "佛罗伦萨奶油泡芙塔", "锡耶纳蜜饼", "里科塔柠檬小挞", "栗子粉蜂蜜饼",
    "香草萨芭雍杯", "咖啡榛子半冻糕", "橙皮巧克力卷", "开心果奶油千层", "杏仁蛋白小圆饼", "葡萄佛卡夏甜饼",
    "玫瑰水杏仁奶冻", "蜂蜜松子挞", "柠檬乳酒冻", "樱桃里科塔卷", "糖渍栗子蛋糕", "无花果核桃蜜卷",
    "覆盆子开心果小盏", "香橙杏仁海绵蛋糕", "梨子榛子挞", "黑巧克力橙酒慕斯", "柑橘蜂蜜奶冻", "葡萄干松子卷",
    "肉桂苹果酥皮盏",
]
POISON, SWEET = "有毒", "真甜点"


def _load() -> dict:
    try:
        g = json.loads(get_config("dessert_game", "") or "{}")
    except ValueError:
        g = {}
    if not isinstance(g, dict):
        g = {}
    for k, v in (("inv", {}), ("claimed", []), ("used", []), ("revealed", False)):
        g.setdefault(k, v)
    return g


def _save(g: dict | None) -> None:
    set_config("dessert_game", json.dumps(g, ensure_ascii=False) if g else "")


def reset() -> None:
    """开始 / 结束晚餐时清掉这局甜点。"""
    _save(None)


def _seated() -> list[str]:
    s = dinner.state()
    return [x for x in s["seats"] if x] if s else []


def _label(d: dict) -> str:
    return POISON if d.get("poison") else SWEET


def claim(name: str, rng: random.Random | None = None) -> tuple[bool, str]:
    rng = rng or random
    if not dinner.state():
        return False, "现在没有进行中的晚餐，等管理员「开始晚餐」。"
    if name not in _seated():
        return False, "先「入座 座位号」，入座的人才能领甜点。"
    g = _load()
    if g["revealed"]:
        return False, "这一局甜点已经揭晓了。"
    if name in g["claimed"]:
        return False, "这局晚餐你已经领过甜点了。用「我的甜点」看手里还有什么。"
    free = [x for x in POOL if x not in g["used"]]
    if len(free) < 3:
        return False, "甜点已经发完了，找管理员。"
    picks = rng.sample(free, 3)
    poison_idx = set(rng.sample(range(3), 2))
    got = [{"name": n, "poison": i in poison_idx, "from": ""} for i, n in enumerate(picks)]
    g["used"] += picks
    g["claimed"].append(name)
    g["inv"].setdefault(name, []).extend(got)
    _save(g)
    return True, ("🍰 你领到了三道甜点：\n" + "\n".join(f"· {d['name']}（{'☠️ 有毒' if d['poison'] else '🍯 真甜点'}）" for d in got)
                  + "\n\n只有现在能看到真假，记好了。送出去以后，别人收到的是什么，只有你知道。\n「送甜点 角色名 甜点名」送给入座的人。")


def cooldown_left(name: str, now: float | None = None) -> int:
    cd = get_int("dinner_dessert_cooldown_sec", 180)
    if cd <= 0:
        return 0
    last = _load().get("last_give", {}).get(name)
    if last is None:
        return 0
    return max(0, int(last + cd - (now if now is not None else time.time())))


def give(giver: str, receiver: str, dessert: str, now: float | None = None) -> tuple[bool, str]:
    """送一道甜点。先查格式、人、甜点，都对了才算冷却。"""
    now = now if now is not None else time.time()
    if not dinner.state():
        return False, "现在没有进行中的晚餐。"
    g = _load()
    if g["revealed"]:
        return False, "这一局甜点已经揭晓了，不能再送。"
    if not receiver or not dessert:
        return False, "用法：送甜点 角色名 甜点名"
    if receiver == giver:
        return False, "不能送给自己。"
    if receiver not in _seated():
        return False, f"「{receiver}」不在晚餐座位上，只能送给入座的人。"
    mine = g["inv"].get(giver, [])
    idx = next((i for i, d in enumerate(mine) if d["name"] == dessert), -1)
    if idx < 0:
        have = "、".join(d["name"] for d in mine)
        return False, f"你手里没有「{dessert}」。" + (f"你有：{have}" if have else "你手里没有甜点了。")
    left = cooldown_left(giver, now)
    if left:
        return False, f"⏳ 送得太快了，{left} 秒后再送。"
    d = mine.pop(idx)
    d["from"] = giver
    g["inv"].setdefault(receiver, []).append(d)
    g.setdefault("last_give", {})[giver] = now
    _save(g)
    return True, f"🎁 已把「{dessert}」送给 {receiver}。"


def mine_text(name: str) -> str:
    items = _load()["inv"].get(name, [])
    if not items:
        return "🍽️ 你手里没有甜点。入座后在个人群「领取甜点」。"
    return f"🍰 你手里的甜点（{len(items)} 道，真假自己猜）：\n" + "\n".join(
        f"· {d['name']}" + (f"（{d['from']} 送的）" if d.get("from") else "（自己领的）") for d in items)


def _holders(g: dict) -> list[tuple[str, list]]:
    return [(n, items) for n, items in g["inv"].items() if items]


def all_text() -> str:
    """管理员看：每个人手里的真假和来源。"""
    g = _load()
    rows = _holders(g)
    if not rows:
        return "🍽️ 这一局还没有人领甜点。"
    out = ["📦 全部甜点（仅管理员）" + ("（已揭晓）" if g["revealed"] else "")]
    for n, items in rows:
        out.append(f"\n👤 {n}")
        out += [f"· {d['name']}｜{_label(d)}｜{('来自 ' + d['from']) if d.get('from') else '自己领的'}" for d in items]
    return "\n".join(out)


def reveal() -> tuple[bool, str]:
    """公布全部真假并排名，之后不能再送。"""
    g = _load()
    rows = _holders(g)
    if not rows:
        return False, "这一局还没有人领甜点。"
    stats = sorted(((n, sum(not d["poison"] for d in items), sum(d["poison"] for d in items), items) for n, items in rows),
                   key=lambda x: (-x[1], x[2]))
    out = ["🍰 甜点揭晓", "真甜点多的在前；一样多时，吃到毒的少的在前。", ""]
    rank, prev = 0, None
    for i, (n, sweet, poison, items) in enumerate(stats):
        if (sweet, poison) != prev:
            rank, prev = i + 1, (sweet, poison)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
        out.append(f"{medal} {n}：🍯 {sweet} 道 · ☠️ {poison} 道")
        out += [f"　　{d['name']}（{_label(d)}{'，' + d['from'] + ' 送的' if d.get('from') else ''}）" for d in items]
    g["revealed"] = True
    _save(g)
    return True, "\n".join(out)


def broadcast_target() -> tuple[str, str] | None:
    """晚餐开在哪个群（播报送甜点用）。老版本开的晚餐没记群，返回 None 就不播报。"""
    s = dinner.state()
    return (s["bot"], s["group"]) if s and s.get("bot") and s.get("group") else None
