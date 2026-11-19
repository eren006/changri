"""晚餐小游戏：我有你没有。

· 入座的人（或管理员）在晚餐群发「开始我有你没有」开局：开局时在座的人参加，每人 10 根手指，按座位顺序每人出一次题。
· 轮到的人「发言 一句话」说一件自己做过的事（可以重发改内容）。其他人里没做过的「折手指」，做过的「不折」：
  机器人不回执（消息多，官方机器人扛不住），下一轮开始前可以改，以最后一次为准。
· 管理员或这轮出题的人「下一轮」：结算这一轮（折了的扣一根，扣到 0 出局），公布还剩几根，@ 没表态的人；
  没表态的不扣，可以在下一轮里「补折」「补不折」（只补上一轮）。出局的人不再出题、不再表态。
· 所有人都出完题，最后一次「下一轮」直接发最终统计；管理员「结束我有你没有」可以提前收。开始、结束晚餐都会清掉。
状态存在 config 的 ihave_game（JSON）。按角色名记（跟晚餐座位一样）；openid 是玩家在这个群发指令时记下的，@ 人用。"""
import json

from . import dinner
from .db import get_config, set_config

FINGERS = 10


def _load() -> dict | None:
    try:
        g = json.loads(get_config("ihave_game", "") or "null")
    except ValueError:
        return None
    return g if isinstance(g, dict) and g.get("order") else None


def _save(g: dict | None) -> None:
    set_config("ihave_game", json.dumps(g, ensure_ascii=False) if g else "")


def active() -> dict | None:
    return _load()


def reset() -> None:
    _save(None)


def _alive(g: dict) -> list[str]:
    return [n for n in g["order"] if g["fingers"][n] > 0]


def _speaker(g: dict) -> str:
    return g["order"][g["idx"]]


def note_openid(name: str, openid: str) -> None:
    g = _load()
    if g and name in g["fingers"] and openid and g["openids"].get(name) != openid:
        g["openids"][name] = openid
        _save(g)


def start(name: str, is_admin: bool, bot_id: str, group: str) -> tuple[bool, str]:
    s = dinner.state()
    if not s:
        return False, "现在没有进行中的晚餐，等管理员「开始晚餐」。"
    if s.get("group") and s["group"] != group:
        return False, "我有你没有在晚餐那个群里玩，回那个群再开。"
    if _load():
        return False, "已经有一局我有你没有在进行，玩完或管理员「结束我有你没有」再开。"
    order = [x for x in s["seats"] if x]
    if not is_admin and name not in order:
        return False, "入座的人才能开局，先「入座 座位号」。"
    if len(order) < 2:
        return False, "至少要 2 个人入座才能玩。"
    _save({"bot": bot_id, "group": group, "order": order, "fingers": {n: FINGERS for n in order}, "openids": {},
           "idx": 0, "round": 1, "said": "", "votes": {}, "prev": None})
    return True, (f"✋ 我有你没有开局！{len(order)} 人参加，每人 {FINGERS} 根手指，按座位顺序：{'、'.join(order)}。\n"
                  f"轮到的人「发言 一句话」说一件自己做过的事；其他人里没做过的发「折手指」，做过的发「不折」（不回执，下一轮前能改）。\n"
                  f"手指折完就出局。管理员或出题的人「下一轮」结算。\n\n👉 第 1 轮，请 {order[0]} 发言。")


def speak(name: str, text: str) -> tuple[bool, str]:
    g = _load()
    if not g:
        return False, "现在没有进行中的我有你没有。"
    if name != _speaker(g):
        return False, f"现在轮到 {_speaker(g)} 发言。"
    text = text.strip()
    if not text:
        return False, "用法：发言 一句话（说一件你做过的事）"
    again = bool(g["said"])
    g["said"] = text[:200]
    _save(g)
    return True, (f"🗣️ {name}{'（改了）' if again else ''}：{g['said']}\n"
                  f"没做过的发「折手指」，做过的发「不折」。")


def vote(name: str, fold: bool) -> str:
    """表态。成功返回 ""（插件不回执），不行返回原因。"""
    g = _load()
    if not g:
        return "现在没有进行中的我有你没有。"
    if name not in g["fingers"]:
        return "你不在这局里（开局时入座的人才参加）。"
    if g["fingers"][name] <= 0:
        return "你的手指已经折完出局了。"
    if name == _speaker(g):
        return "这一轮是你出题，不用表态。"
    if not g["said"]:
        return f"{_speaker(g)} 还没发言，等题目出来再表态。"
    g["votes"][name] = fold
    _save(g)
    return ""


def make_up(name: str, fold: bool) -> str:
    """补上一轮的表态。成功返回 ""，不行返回原因。"""
    g = _load()
    if not g:
        return "现在没有进行中的我有你没有。"
    p = g.get("prev")
    if not p or name not in p["missing"]:
        return "你上一轮不用补。"
    if g["fingers"][name] <= 0:
        return "你的手指已经折完出局了。"
    p["missing"].remove(name)
    if fold:
        g["fingers"][name] -= 1
    _save(g)
    return ""


def _stats(g: dict) -> list[str]:
    order = sorted(g["order"], key=lambda n: -g["fingers"][n])
    return [f"· {n}：{g['fingers'][n]} 根" + ("（出局）" if g["fingers"][n] <= 0 else "") for n in order]


def _final(g: dict, head: str) -> str:
    alive = _alive(g)
    out = [head, "", "✋ 最终统计（剩的手指越多越清白）："] + _stats(g)
    top = max(g["fingers"].values())
    if top > 0:
        out.append("🏆 剩得最多：" + "、".join(n for n in g["order"] if g["fingers"][n] == top) + f"（{top} 根）")
    out.append(f"还在场 {len(alive)} 人。")
    return "\n".join(out)


def next_round(name: str, is_admin: bool) -> tuple[bool, dict | str]:
    """结算这一轮。返回 (成功, {text, at: [(名字, openid)], ended})。"""
    g = _load()
    if not g:
        return False, "现在没有进行中的我有你没有。"
    speaker = _speaker(g)
    if not is_admin and name != speaker:
        return False, "只有管理员或这一轮出题的人能「下一轮」。"
    lines = [f"📣 第 {g['round']} 轮结算"]
    missing = []
    if g["said"]:
        lines.append(f"题目（{speaker}）：{g['said']}")
        folded = []
        for n in _alive(g):
            if n == speaker:
                continue
            if n not in g["votes"]:
                missing.append(n)
            elif g["votes"][n]:
                g["fingers"][n] -= 1
                folded.append(n)
        lines.append(("🙇 折了手指：" + "、".join(folded)) if folded else "🙇 这一轮没人折手指。")
        out_now = [n for n in folded if g["fingers"][n] <= 0]
        if out_now:
            lines.append("💀 手指折完出局：" + "、".join(out_now))
    else:
        lines.append(f"{speaker} 这一轮没发言，跳过。")
    g["prev"] = {"round": g["round"], "missing": missing}
    # 找下一个还在场、还没出过题的人
    nxt = next((i for i in range(g["idx"] + 1, len(g["order"])) if g["fingers"][g["order"][i]] > 0), None)
    if nxt is None or len(_alive(g)) < 2:
        if missing:
            lines.append("没表态的（按没折算）：" + "、".join(missing))
        _save(None)
        return True, {"text": _final(g, "\n".join(lines) + "\n\n🏁 所有人都出完题了，本局结束！"), "at": [], "ended": True}
    g["idx"], g["round"], g["said"], g["votes"] = nxt, g["round"] + 1, "", {}
    lines += ["", "✋ 现在的手指："] + _stats(g)
    at = []
    if missing:
        lines.append(f"\n⏰ 上一轮还没表态：{'、'.join(missing)}。补的话发「补折」或「补不折」（这一轮里补都算）。")
        at += missing
    nxt_name = _speaker(g)
    lines.append(f"\n👉 第 {g['round']} 轮，请 {nxt_name} 发言。")
    at.append(nxt_name)
    _save(g)
    return True, {"text": "\n".join(lines), "at": [(n, g["openids"].get(n, "")) for n in at], "ended": False}


def status() -> str:
    g = _load()
    if not g:
        return "现在没有进行中的我有你没有。"
    speaker = _speaker(g)
    voters = [n for n in _alive(g) if n != speaker]
    done = [n for n in voters if n in g["votes"]]
    wait = [n for n in voters if n not in g["votes"]]
    return "\n".join([f"✋ 我有你没有 · 第 {g['round']} 轮", f"出题：{speaker}" + (f"「{g['said']}」" if g["said"] else "（还没发言）"),
                      f"已表态（{len(done)}）：{'、'.join(done) or '——'}", f"还没表态（{len(wait)}）：{'、'.join(wait) or '——'}",
                      "", "✋ 手指："] + _stats(g))


def end() -> tuple[bool, str]:
    g = _load()
    if not g:
        return False, "现在没有进行中的我有你没有。"
    _save(None)
    return True, _final(g, "🏁 我有你没有提前结束。")
