"""晚餐小游戏：围猎（合作追猎物）、巡逻（捉迷藏淘汰赛）。都属于「晚餐」扩展：开了晚餐就能玩。规则照搬老长日系统的长日晚餐.js。

围猎：要先「开始晚餐」，入座的人参加。一张 N×N 的林地（列 A..，行 1..），猎物藏在某格，最多几轮内拿下。
  · 并发模式（默认）：每轮先「侦查」（每人一次 追踪 格子 / 设陷阱 格子），再「开火」（每人一次 开枪 格子 / 观望），
    大家同时出手，全员出完或到点统一结算；到点由定时任务结算（plugins/dinner.py）。
  · 轮流模式：按座位一个个来，每人三选一（追踪 / 设陷阱 / 开枪），全员行动一次算一轮，轮末猎物挪一格。
  · 打中先重伤，已重伤再中就拿下；陷阱踩中同理（算设陷阱的人）。拿下后：收尾 +5，重伤 +2，其余参与者 +1，战利品加进晚餐菜谱。
  · 排行榜存在 config 的 hunt_scores 里，不随季度清。
巡逻：管理员「巡逻开局」，玩家在自己的个人群「巡逻报名」「巡逻躲藏 地点」（别人看不到你藏哪）；
  管理员每发一次「巡逻」结算一轮：卫兵每轮多一个（最多「地点数-1」个，永远留一个安全地点），
  藏在危险地点或这轮没躲的人被捕淘汰，剩 3 人以内结束。
状态都存在 config：dinner_game（围猎，JSON）、guard_state（巡逻，JSON）、guard_locations（地点）。"""
import json
import random
import time

from . import dinner
from .db import get_config, set_config

# ====================== 围猎 ======================

PREY_BY_ERA = {"现代中餐": "野猪", "现代西餐": "野猪", "古代中餐": "梅花鹿", "古代西餐": "野鹿", "诡秘深渊": "深渊潜行者", "赛博未来": "变异机械鹿"}
COLS = "ABCDEFGHIJ"
PHASE_SECS = {"scout": 90, "fire": 60}
HEAT_WORD = {4: "就在这一格", 3: "很近", 2: "不远", 1: "很远"}

rng = random.Random()          # 测试里换成固定种子


def _load() -> dict | None:
    try:
        g = json.loads(get_config("dinner_game", "") or "null")
    except ValueError:
        return None
    return g if isinstance(g, dict) and g.get("type") == "hunt" else None


def _save(g: dict | None) -> None:
    set_config("dinner_game", json.dumps(g, ensure_ascii=False) if g else "")


def hunt_active() -> dict | None:
    return _load()


def parse_cell(text: str, n: int) -> tuple[int, int] | None:
    t = "".join(text.split()).upper()
    if len(t) < 2 or t[0] not in COLS or not t[1:].isdigit():
        return None
    c, r = COLS.index(t[0]), int(t[1:]) - 1
    return (r, c) if 0 <= c < n and 0 <= r < n else None


def cell_name(r: int, c: int) -> str:
    return f"{COLS[c]}{r + 1}"


def _heat(d: int) -> int:
    return 4 if d <= 0 else 3 if d == 1 else 2 if d == 2 else 1


def _dir(dr: int, dc: int) -> str:
    return (("北" if dr < 0 else "南" if dr > 0 else "") + ("西" if dc < 0 else "东" if dc > 0 else "")) or "就在这一格"


def _plan(p: int) -> tuple[int, int]:
    """按人数定边长和轮数：人多线索密，地图要大；一轮行动多，轮数要少。"""
    return (5 if p <= 6 else 6 if p <= 8 else 7), (6 if p <= 7 else 5 if p <= 9 else 4)


def map_text(st: dict) -> str:
    n = st["n"]
    mark = {}
    for x in st["traps"]:
        mark[(x["r"], x["c"])] = "🪤"
    for x in st["misses"]:
        mark[(x["r"], x["c"])] = "❌"
    for x in st["clues"]:
        mark[(x["r"], x["c"])] = {4: "🎯", 3: "🟥", 2: "🟧"}.get(x["heat"], "🟦")
    lines = ["　 " + " ".join(COLS[:n])]
    for r in range(n):
        lines.append(f"{r + 1:>2}" + "".join(mark.get((r, c), "⬜") for c in range(n)))
    return MAP_A + "\n".join(lines) + "\n" + _legend(st) + MAP_B


# 文字地图前后的标记：群里发的时候能出图就把这一段换成单独一张地图图片（表情画不进图），出不了图就留着文字版
MAP_A, MAP_B = "⟦地图⟧", "⟦/地图⟧"


def split_map(text: str) -> tuple[str, bool]:
    """去掉文字地图，返回 (剩下的文字, 原来有没有地图)。"""
    if MAP_A not in text:
        return text, False
    a, _, rest = text.partition(MAP_A)
    _, _, b = rest.partition(MAP_B)
    return (a.rstrip("\n") + ("\n" if a.strip() and b.strip() else "") + b.lstrip("\n")).strip(), True


def plain(text: str) -> str:
    """出图失败时：留着文字地图，只去掉标记。"""
    return text.replace(MAP_A, "").replace(MAP_B, "")


def hunt_map_png() -> bytes | None:
    g = _load()
    if not g:
        return None
    st = g["state"]
    phase = {"scout": " · 侦查", "fire": " · 开火"}.get(st.get("phase", ""), "") if st["mode"] == "sync" else ""
    title = f"围猎 第 {st['round']}/{st['max_round']} 轮{phase} · {st['prey']}{'（带伤）' if st['hp'] == 1 else ''}"
    try:
        from .render import hunt_map
        return hunt_map.render(st, title)
    except Exception:  # noqa: BLE001 出不了图就发文字地图
        import logging
        logging.getLogger(__name__).exception("围猎地图出图失败")
        return None


def _legend(st: dict) -> str:
    return "🪤陷阱 🟥近 🟧不远 🟦远 ❌打空　" + ("猎物带着伤" if st["hp"] == 1 else "猎物完好")


def _names(st: dict) -> list[str]:
    return list(st["players"])


def _phase_begin(st: dict, phase: str, now: float) -> None:
    st["phase"], st["deadline"], st["submits"] = phase, now + PHASE_SECS[phase], []


def _sync_prompt(st: dict) -> str:
    sec = PHASE_SECS[st["phase"]]
    return (f"🔍 侦查阶段（{sec} 秒内，全员出完就提前结算）：每人「追踪 格子」或「设陷阱 格子」一次。例：追踪 B3" if st["phase"] == "scout"
            else f"🔫 开火阶段（{sec} 秒内，全员表态就提前结算）：每人「开枪 格子」一次，或「观望」。例：开枪 C2")


def _sync_view(st: dict, now: float) -> str:
    left = max(0, int(st["deadline"] - now + 0.999))
    waiting = [n for n in _names(st) if n not in {x["name"] for x in st["submits"]}]
    return f"{map_text(st)}\n{_sync_prompt(st)}\n还剩约 {left} 秒" + (f"，还没出手：{'、'.join(waiting)}" if waiting else "")


def _turn_view(st: dict) -> str:
    return f"{map_text(st)}\n{st['players'][st['turn']]}，轮到你了：追踪 / 设陷阱 / 开枪 + 格子"


def hunt_start(bot_id: str, group: str, args: list[str], now: float | None = None) -> tuple[bool, str]:
    """管理员开局。args：可以有「轮流」「并发」、边长（4-7）、猎物名。"""
    now = time.time() if now is None else now
    d = dinner.state()
    if not d:
        return False, "晚餐还没开始，先「开始晚餐 座位数」。"
    if _load():
        return False, "已经有一局围猎在进行，先「结束狩猎」。"
    players = [x for x in d["seats"] if x]
    if not players:
        return False, "晚餐席位上还没有人，大家先「入座 座位号」。"
    mode, n_arg, prey = "sync", None, ""
    for a in args:
        if a in ("轮流", "并发"):
            mode = "turn" if a == "轮流" else "sync"
        elif a.isdigit() and n_arg is None:
            n_arg = int(a)
        elif not prey:
            prey = a[:12]
    n, rounds = _plan(len(players))
    n = max(4, min(7, n_arg if n_arg is not None else n))
    prey = prey or PREY_BY_ERA.get(d.get("era", ""), "野猪")
    st = {"n": n, "prey": prey, "pos": [rng.randrange(n), rng.randrange(n)], "hp": 2, "round": 1, "max_round": rounds,
          "players": players, "turn": 0, "acted": 0, "traps": [], "clues": [], "misses": [], "wounded_by": "", "mode": mode}
    if mode == "sync":
        _phase_begin(st, "scout", now)
    _save({"type": "hunt", "bot": bot_id, "group": group, "state": st})
    head = (f"🏹 号角响了。众人提着灯踏进林子——一头「{prey}」就藏在这片 {n}×{n} 的林地里，"
            f"天亮之前（{rounds} 轮）必须拿下它，否则今晚的桌上就只剩空盘子。\n参加的人：{'、'.join(players)}\n")
    if mode == "sync":
        return True, head + _sync_view(st, now)
    return True, head + f"{map_text(st)}\n{players[0]}，你走在最前面。看脚印（追踪 格子）、下套（设陷阱 格子），还是直接举枪（开枪 格子）？"


def _record(name: str, score: int, capture: bool) -> None:
    try:
        board = json.loads(get_config("hunt_scores", "") or "{}")
    except ValueError:
        board = {}
    r = board.get(name) or {"score": 0, "captures": 0, "games": 0}
    r["score"] += score
    r["captures"] += int(capture)
    r["games"] += 1
    board[name] = r
    set_config("hunt_scores", json.dumps(board, ensure_ascii=False))


def _finish(st: dict, captor: str, how: str) -> str:
    pts = {p: 1 for p in _names(st)}
    if st["wounded_by"] in pts:
        pts[st["wounded_by"]] += 2
    pts[captor] = pts.get(captor, 1) + 5
    for p, s in pts.items():
        _record(p, s, p == captor)
    dish = f"现烤{st['prey']}"
    d = dinner.state()
    if d is not None and dish not in d.get("dishes", []):
        d.setdefault("dishes", []).append(dish)
        dinner._save(d)
    _save(None)
    ranked = "　".join(f"{p} +{s}" for p, s in sorted(pts.items(), key=lambda x: -x[1]))
    return (f"🎯 {how}\n🏆 林子里安静了。{captor} 拎起了「{st['prey']}」，扛着它回到了灯火通明的餐桌——「{dish}」添进了今晚的菜单。\n"
            f"今夜的功劳：{ranked}")


def _escape(st: dict) -> str:
    for p in _names(st):
        _record(p, 0, False)
    _save(None)
    r, c = st["pos"]
    return f"💨 东方泛白，「{st['prey']}」的影子没入了林子深处（它最后在 {cell_name(r, c)}）。众人空着手回到桌边——这一局没有人得分。"


def _step(st: dict) -> None:
    r, c = st["pos"]
    opts = [(r + dr, c + dc) for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)) if 0 <= r + dr < st["n"] and 0 <= c + dc < st["n"]]
    st["pos"] = list(rng.choice(opts))


def _trap(st: dict) -> tuple[str, bool] | None:
    """猎物落脚的格子有陷阱就结算。返回 None 或 (文字, 是否结束)。"""
    for i, t in enumerate(st["traps"]):
        if [t["r"], t["c"]] == st["pos"]:
            st["traps"].pop(i)
            st["hp"] -= 1
            name = cell_name(t["r"], t["c"])
            if st["hp"] <= 0:
                return _finish(st, t["owner"], f"{name} 传来一声闷响——「{st['prey']}」一脚踩进了 {t['owner']} 设的陷阱，哀嚎着倒了下去！"), True
            st["wounded_by"] = st["wounded_by"] or t["owner"]
            return f"🪤 {name} 方向传来铁夹合拢的脆响——「{st['prey']}」踩中了 {t['owner']} 设的陷阱，拖着血迹逃开了。先前的脚印全乱了。", False
    return None


def _move_end_of_round(st: dict, lines: list[str], now: float) -> tuple[str, bool]:
    """轮末：猎物挪一格、结算陷阱、进下一轮或逃走。返回 (全文, 是否结束)。"""
    _step(st)
    st["clues"], st["misses"] = [], []
    hit = _trap(st)
    if hit:
        lines.append(hit[0])
        if hit[1]:
            return "\n".join(lines), True
    else:
        lines.append("🌿 灌木丛里窸窸窣窣一阵响，猎物换了个地方，先前的痕迹作不得数了。")
    st["round"] += 1
    if st["round"] > st["max_round"]:
        lines.append(_escape(st))
        return "\n".join(lines), True
    return "", False


def _check_cell(st: dict, action: str, cell_text: str) -> tuple[tuple[int, int] | None, str]:
    cell = parse_cell(cell_text, st["n"])
    if not cell:
        return None, f"要写格子，例：{action} B3（列 A-{COLS[st['n'] - 1]}，行 1-{st['n']}）"
    return cell, ""


def _track_line(st: dict, who: str, cell: tuple[int, int]) -> str:
    dr, dc = st["pos"][0] - cell[0], st["pos"][1] - cell[1]
    d = abs(dr) + abs(dc)
    heat = _heat(d)
    st["clues"].append({"r": cell[0], "c": cell[1], "heat": heat})
    if d == 0:
        return f"🔍 {who} 蹲下身摸了摸 {cell_name(*cell)} 的泥土——脚印还是热的，「{st['prey']}」就在这一格！"
    fuzzy = st["hp"] == 2 and rng.random() < 0.25                    # 猎物完好时 25% 只知道远近
    return f"🔍 {who} 查看 {cell_name(*cell)}：{HEAT_WORD[heat]}" + ("，痕迹太乱辨不出方向" if fuzzy else f"，往{_dir(dr, dc)}边去了")


def _trap_ok(st: dict, cell: tuple[int, int], pending: list) -> str:
    if len(st["traps"]) + len(pending) >= st["n"]:
        return f"陷阱已经够多了（最多 {st['n']} 个）。"
    if any((t["r"], t["c"]) == cell for t in st["traps"]) or cell in pending:
        return f"{cell_name(*cell)} 已经有陷阱了。"
    return ""


def hunt_act(name: str, action: str, cell_text: str = "", now: float | None = None) -> tuple[bool, str, bool]:
    """玩家行动：追踪 / 设陷阱 / 开枪 / 观望。返回 (算不算数, 回复, 这局是否结束)。并发模式下没出齐只回一句「已就位」。"""
    now = time.time() if now is None else now
    g = _load()
    if not g:
        return False, "现在没有进行中的围猎。", False
    st = g["state"]
    if name not in st["players"]:
        return False, "你没在这一局里（开局时入座的人才能参加）。", False
    if st["mode"] == "sync":
        return _sync_submit(g, st, name, action, cell_text, now)
    return _turn_act(g, st, name, action, cell_text, now)


def _sync_submit(g, st, name, action, cell_text, now):
    if any(x["name"] == name for x in st["submits"]):
        return False, f"{name} 这一阶段已经出过手了，等大家。", False
    sub = {"name": name, "action": action}
    if action == "观望":
        if st["phase"] != "fire":
            return False, "还在侦查阶段，先「追踪」或「设陷阱」。", False
    else:
        if action == "开枪" and st["phase"] != "fire":
            return False, "还在侦查阶段，先「追踪」或「设陷阱」；开火阶段才能开枪。", False
        if action != "开枪" and st["phase"] != "scout":
            return False, "现在是开火阶段，只能「开枪 格子」或「观望」。", False
        cell, why = _check_cell(st, action, cell_text)
        if why:
            return False, why, False
        if action == "设陷阱":
            why = _trap_ok(st, cell, [tuple(x["cell"]) for x in st["submits"] if x["action"] == "设陷阱"])
            if why:
                return False, why, False
        sub["cell"] = list(cell)
    st["submits"].append(sub)
    if len(st["submits"]) >= len(st["players"]):
        text, ended = _resolve(g, st, now)
        return True, text, ended
    _save(g)
    return True, f"✅ {name} 已就位（{len(st['submits'])}/{len(st['players'])}）", False


def _resolve(g: dict, st: dict, now: float) -> tuple[str, bool]:
    """结算并发模式的当前阶段（出齐 / 到点 / 管理员跳过）。"""
    done = {x["name"] for x in st["submits"]}
    absent = [n for n in st["players"] if n not in done]
    lines = []
    if st["phase"] == "scout":
        for x in st["submits"]:
            if x["action"] == "设陷阱" and not _trap_ok(st, tuple(x["cell"]), []):
                st["traps"].append({"r": x["cell"][0], "c": x["cell"][1], "owner": x["name"]})
                lines.append(f"🪤 {x['name']} 在 {cell_name(*x['cell'])} 埋好了铁夹")
        for x in st["submits"]:
            if x["action"] == "追踪":
                lines.append(_track_line(st, x["name"], tuple(x["cell"])))
        if absent:
            lines.append(f"💤 没来得及出手：{'、'.join(absent)}")
        _phase_begin(st, "fire", now)
        lines.append(f"—— 第 {st['round']}/{st['max_round']} 轮 · 开火 ——")
        _save(g)
        return "\n".join(lines) + "\n" + _sync_view(st, now), False
    for x in st["submits"]:                                   # 开火：按出手先后判定
        if x["action"] == "观望":
            continue
        r, c = x["cell"]
        if [r, c] == st["pos"]:
            st["hp"] -= 1
            if st["hp"] <= 0:
                lines.append(_finish(st, x["name"], f"{x['name']} 朝 {cell_name(r, c)} 扣下扳机——正中那头已经带伤的「{st['prey']}」，它连挣扎都来不及，就倒下了！"))
                return "\n".join(lines), True
            st["wounded_by"] = st["wounded_by"] or x["name"]
            lines.append(f"💥 {x['name']} 朝 {cell_name(r, c)} 扣下扳机——命中！「{st['prey']}」惨叫着带伤撞开灌木")
        else:
            st["misses"].append({"r": r, "c": c})
            lines.append(f"💨 {x['name']} 朝 {cell_name(r, c)} 开枪——只打中了树干")
    passed = [x["name"] for x in st["submits"] if x["action"] == "观望"] + absent
    if passed:
        lines.append(f"🌫️ 没有开枪：{'、'.join(passed)}")
    text, ended = _move_end_of_round(st, lines, now)
    if ended:
        return text, True
    _phase_begin(st, "scout", now)
    lines.append(f"—— 第 {st['round']}/{st['max_round']} 轮 · 侦查 ——")
    _save(g)
    return "\n".join(lines) + "\n" + _sync_view(st, now), False


def _turn_act(g, st, name, action, cell_text, now):
    cur = st["players"][st["turn"]]
    if name != cur:
        return False, f"现在轮到 {cur}，请等一等。", False
    if action == "观望":
        return False, "轮流模式不用观望，轮到你时选一个：追踪 / 设陷阱 / 开枪 + 格子。", False
    cell, why = _check_cell(st, action, cell_text)
    if why:
        return False, why, False
    lines = []
    if action == "追踪":
        lines.append(_track_line(st, name, cell))
    elif action == "设陷阱":
        why = _trap_ok(st, cell, [])
        if why:
            return False, why, False
        st["traps"].append({"r": cell[0], "c": cell[1], "owner": name})
        lines.append(f"🪤 {name} 屏住呼吸，在 {cell_name(*cell)} 埋好了铁夹，又用落叶仔细盖住。")
    else:                                                     # 开枪：打中或打空，猎物都会挪一格
        if list(cell) == st["pos"]:
            st["hp"] -= 1
            if st["hp"] <= 0:
                return True, _finish(st, name, f"{name} 朝 {cell_name(*cell)} 扣下扳机——正中那头已经带伤的「{st['prey']}」，它倒下了！"), True
            st["wounded_by"] = st["wounded_by"] or name
            lines.append(f"💥 {name} 朝 {cell_name(*cell)} 扣下扳机——命中了！「{st['prey']}」带伤撞开灌木逃走。先前的脚印全乱了。")
            st["clues"], st["misses"] = [], []
        else:
            st["misses"].append({"r": cell[0], "c": cell[1]})
            lines.append(f"💨 {name} 朝 {cell_name(*cell)} 扣下扳机——只打中了树干。「{st['prey']}」受了惊，往旁边蹿开了一格！")
            st["clues"] = []
        _step(st)
        hit = _trap(st)
        if hit:
            lines.append(hit[0])
            if hit[1]:
                return True, "\n".join(lines), True
    return _turn_next(g, st, lines, now)


def _turn_next(g, st, lines, now):
    st["acted"] += 1
    st["turn"] = (st["turn"] + 1) % len(st["players"])
    if st["acted"] >= len(st["players"]):
        st["acted"] = 0
        text, ended = _move_end_of_round(st, lines, now)
        if ended:
            return True, text, True
        lines.append(f"—— 第 {st['round']}/{st['max_round']} 轮 ——")
    _save(g)
    return True, "\n".join(lines) + "\n" + _turn_view(st), False


def hunt_skip(now: float | None = None) -> tuple[bool, str, bool]:
    """管理员跳过：并发模式直接结算当前阶段；轮流模式跳过当前这个人。"""
    now = time.time() if now is None else now
    g = _load()
    if not g:
        return False, "现在没有进行中的围猎。", False
    st = g["state"]
    if st["mode"] == "sync":
        text, ended = _resolve(g, st, now)
        return True, "⏭️ 不等了，直接结算：\n" + text, ended
    who = st["players"][st["turn"]]
    ok, text, ended = _turn_next(g, st, [f"⏭️ 已跳过 {who}"], now)
    return ok, text, ended


def hunt_due(now: float | None = None) -> tuple[str, str, str, bool] | None:
    """定时任务调：并发模式到点了就结算。返回 (机器人, 群, 文字, 是否结束) 或 None。"""
    now = time.time() if now is None else now
    g = _load()
    if not g or g["state"]["mode"] != "sync" or now < g["state"]["deadline"]:
        return None
    text, ended = _resolve(g, g["state"], now)
    return g["bot"], g["group"], "⏰ 时间到，结算：\n" + text, ended


def hunt_status(now: float | None = None) -> str:
    now = time.time() if now is None else now
    g = _load()
    if not g:
        return "现在没有进行中的围猎。"
    st = g["state"]
    head = f"🏹 围猎 第 {st['round']}/{st['max_round']} 轮（猎物：{st['prey']}，陷阱 {len(st['traps'])}/{st['n']}）\n"
    return head + (_sync_view(st, now) if st["mode"] == "sync" else _turn_view(st))


def hunt_end() -> bool:
    had = _load() is not None
    _save(None)
    return had


def hunt_rank(limit: int = 10) -> str:
    try:
        board = json.loads(get_config("hunt_scores", "") or "{}")
    except ValueError:
        board = {}
    rows = sorted(board.items(), key=lambda x: (-x[1]["score"], -x[1]["captures"]))[:limit]
    if not rows:
        return "🏹 还没有人打过围猎。"
    medals = ["🥇", "🥈", "🥉"]
    return "🏹 围猎排行榜\n────────────\n" + "\n".join(
        f"{medals[i] if i < 3 else f'{i + 1}.'} {n}　{r['score']} 分 · 拿下 {r['captures']} 次 · {r['games']} 局" for i, (n, r) in enumerate(rows))


def hunt_rank_reset() -> None:
    set_config("hunt_scores", "")


# ====================== 巡逻（捉迷藏淘汰赛） ======================

DEFAULT_LOCATIONS = ["伦敦", "巴黎", "罗马", "君士坦丁堡", "北京", "京都", "莫斯科", "维也纳", "开罗", "德里"]


def guard_locations() -> list[str]:
    try:
        locs = json.loads(get_config("guard_locations", "") or "null")
    except ValueError:
        locs = None
    return locs if isinstance(locs, list) and len(locs) >= 2 else list(DEFAULT_LOCATIONS)


def guard_set_locations(locs: list[str]) -> tuple[bool, str]:
    locs = list(dict.fromkeys(x.strip()[:12] for x in locs if x.strip()))
    if len(locs) < 2:
        return False, "至少要 2 个地点。"
    set_config("guard_locations", json.dumps(locs, ensure_ascii=False))
    return True, f"地点已更新（{len(locs)} 个）：{'、'.join(locs)}。下次开局用这批地点。"


def guard_state() -> dict:
    try:
        s = json.loads(get_config("guard_state", "") or "{}")
    except ValueError:
        s = {}
    return s if isinstance(s, dict) else {}


def _gsave(s: dict | None) -> None:
    set_config("guard_state", json.dumps(s, ensure_ascii=False) if s else "")


def guard_open(bot_id: str, group: str) -> tuple[bool, str]:
    s = guard_state()
    if s.get("active"):
        return False, "已经有一场巡逻在进行，先「巡逻结束」。"
    locs = guard_locations()
    _gsave({"active": True, "round": 0, "locations": locs, "bot": bot_id, "group": group,
            "alive": {}, "jailed": [], "choices": {}, "last_danger": [], "winners": []})
    return True, (f"🛡️ 巡逻赛开局！结果会播报在这个群。\n📍 地点（{len(locs)}）：{'、'.join(locs)}\n"
                  f"想参加的人在自己的个人群发「巡逻报名」，再发「巡逻躲藏 地点」（别人看不到你藏哪）。\n"
                  f"管理员每发一次「巡逻」结算一轮：卫兵每轮多一个，藏在危险地点或没躲的人被捕，剩 3 人以内结束。")


def guard_join(role_id: int, name: str) -> tuple[bool, str]:
    s = guard_state()
    if not s.get("active"):
        return False, "现在没有开局的巡逻，等管理员「巡逻开局」。"
    if s["round"] > 0:
        return False, "巡逻已经开始了，不接受中途加入。"
    s["alive"][str(role_id)] = name
    _gsave(s)
    return True, f"🎭 报名成功，{name}。用「巡逻躲藏 地点」选藏身处。可选：{'、'.join(s['locations'])}"


def guard_hide(role_id: int, place: str) -> tuple[bool, str]:
    s = guard_state()
    if not s.get("active"):
        return False, "现在没有开局的巡逻。"
    if str(role_id) not in s["alive"]:
        return False, "你还没报名，或者已经被捕了。先「巡逻报名」。"
    if place not in s["locations"]:
        return False, f"没有这个地点。可选：{'、'.join(s['locations'])}"
    s["choices"][str(role_id)] = place
    _gsave(s)
    return True, f"🫥 已躲进「{place}」（这一轮以最后一次为准）。愿群星庇佑你。"


def guard_call() -> tuple[bool, dict | str]:
    """结算一轮。返回 (成功, 结果) ；结果 = {text, caught: [(role_id, 地点)], alive: [role_id], ended, round, bot, group}。"""
    s = guard_state()
    if not s.get("active"):
        return False, "现在没有开局的巡逻。"
    if not s["alive"]:
        return False, "还没有人报名。"
    if len(s["alive"]) <= 3 and s["round"] > 0:
        return False, "只剩 3 人以内了，不用再巡逻。"
    s["round"] += 1
    n_guard = min(s["round"], max(1, len(s["locations"]) - 1))          # 永远留一个安全地点，免得一轮团灭
    danger = rng.sample(s["locations"], n_guard)
    caught, alive = [], {}
    for rid, nm in s["alive"].items():
        pick = s["choices"].get(rid)
        if not pick or pick in danger:
            caught.append((int(rid), nm, pick or "没躲"))
            s["jailed"].append({"id": int(rid), "name": nm, "round": s["round"], "place": pick or "没躲"})
        else:
            alive[rid] = nm
    s["alive"], s["choices"], s["last_danger"] = alive, {}, danger
    lines = [f"🛡️ 第 {s['round']} 轮巡逻结算", f"⚠️ 危险地点（{n_guard}）：{'、'.join(danger)}",
             (f"🚨 被捕（{len(caught)}）：" + "、".join(f"「{nm}」@{pl}" for _, nm, pl in caught)) if caught else "✅ 这一轮没人被捕。",
             f"🫱 还剩 {len(alive)} 人"]
    ended = len(alive) <= 3
    if ended:
        s["winners"], s["active"] = list(alive.values()), False
        lines.append(f"🏆 最后的幸存者：{'、'.join(alive.values()) or '（没有人）'}。本场结束！")
    else:
        lines.append(f"请在自己的个人群选第 {s['round'] + 1} 轮的藏身处：「巡逻躲藏 地点」")
    _gsave(s)
    return True, {"text": "\n".join(lines), "caught": [(rid, pl) for rid, _, pl in caught], "alive": [int(x) for x in alive],
                  "ended": ended, "round": s["round"], "bot": s["bot"], "group": s["group"], "locations": s["locations"]}


def guard_status() -> str:
    s = guard_state()
    if not s:
        return "现在没有巡逻赛。"
    jailed = "、".join(f"「{j['name']}」@{j['place']}（第 {j['round']} 轮）" for j in s.get("jailed", []))
    return ("📊 巡逻赛况" + f"\n· 状态：{'进行中' if s.get('active') else '已结束'}" + f"\n· 回合：{s.get('round', 0)}"
            + f"\n· 上一轮危险：{'、'.join(s.get('last_danger', [])) or '——'}"
            + f"\n· 还在（{len(s.get('alive', {}))}）：{'、'.join(s.get('alive', {}).values()) or '——'}"
            + f"\n· 被捕（{len(s.get('jailed', []))}）：{jailed or '——'}"
            + (f"\n· 幸存者：{'、'.join(s['winners'])}" if s.get("winners") else ""))


def guard_end() -> bool:
    had = bool(guard_state())
    _gsave(None)
    return had
