"""跑团 log：一场战役 = 一个 log，有进行中 / 暂停 / 已结束 / 强制停止四种状态（参考 SealDice 的 .log）。
不依赖 QQ / NoneBot，可单独测试。设计见 docs/跑团模块设计.md。"""
from __future__ import annotations

import re
import secrets
import time

from .db import get_config, get_conn

MAX_LINES = 20000
MAX_NAME = 20
CURRENT = ("on", "off")
STATUS_TEXT = {"on": "进行中", "off": "暂停", "ended": "已结束", "halted": "强制停止"}
BRACKETS = ("(", "（", "【", "[")

DEFAULT_PREFIXES = "//\n##"


# ======================== 记录规则 ========================

def rules() -> dict:
    return {
        "brackets": get_config("trpg_log_skip_brackets", "1") == "1",
        "prefixes": [x.strip() for x in get_config("trpg_log_skip_prefixes", DEFAULT_PREFIXES).splitlines() if x.strip()],
        "cmds": get_config("trpg_log_skip_cmds", "0") == "1",
    }


def should_skip(text: str, is_command: bool = False, r: dict | None = None) -> bool:
    """括号开头、命中不记前缀的消息不进数据库。"""
    r = r or rules()
    t = text.strip()
    if not t:
        return True
    if r["brackets"] and t.startswith(BRACKETS):
        return True
    if any(t.startswith(p) for p in r["prefixes"]):
        return True
    return bool(r["cmds"] and is_command)


# ======================== 状态 ========================

def _row(sql: str, args: tuple = ()):
    with get_conn() as c:
        r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def current(scope: str) -> dict | None:
    """这个群当前的 log（进行中或暂停）。"""
    return _row("SELECT * FROM trpg_logs WHERE scope=? AND status IN ('on','off') ORDER BY id DESC LIMIT 1", (scope,))


def recording_id(scope: str) -> int | None:
    cur = current(scope)
    return cur["id"] if cur and cur["status"] == "on" else None


def find(scope: str, key: str) -> dict | None:
    """按名字或短编号找本群的 log。"""
    key = key.strip().lstrip("#")
    if not key:
        return None
    return _row("SELECT * FROM trpg_logs WHERE scope=? AND (name=? OR substr(token,1,4)=?) ORDER BY (name=?) DESC LIMIT 1", (scope, key, key.lower(), key))


def list_logs(scope: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT l.*, (SELECT COUNT(*) FROM trpg_log_lines WHERE log_id=l.id) AS n FROM trpg_logs l WHERE scope=? ORDER BY id", (scope,)).fetchall()
    return [dict(r) for r in rows]


def get(log_id: int) -> dict | None:
    return _row("SELECT * FROM trpg_logs WHERE id=?", (log_id,))


def short(log: dict) -> str:
    return log["token"][:4]


def new_log(scope: str, name: str, started_by: str) -> tuple[dict | None, str]:
    name = name.strip()
    if not name or len(name) > MAX_NAME or re.search(r"\s", name) or name.startswith("#"):
        return None, f"log 名字要 1~{MAX_NAME} 个字，不能有空格，也不能以 # 开头。"
    cur = current(scope)
    if cur:
        return None, f"这个群还有一个没结束的 log「{cur['name']}」（{STATUS_TEXT[cur['status']]}），先 /log end 结束它。"
    if _row("SELECT 1 AS x FROM trpg_logs WHERE scope=? AND name=?", (scope, name)):
        return None, f"已经有一个叫「{name}」的 log 了，想接着记就 /log on {name}。"
    with get_conn() as c:
        lid = c.execute("INSERT INTO trpg_logs(scope,name,status,token,started_by,started_ts) VALUES (?,?, 'on', ?,?,?)",
                        (scope, name, secrets.token_hex(16), started_by, int(time.time()))).lastrowid
    return get(lid), ""


def _set(log_id: int, status: str) -> None:
    with get_conn() as c:
        c.execute("UPDATE trpg_logs SET status=?, ended_ts=? WHERE id=?",
                  (status, int(time.time()) if status in ("ended", "halted") else None, log_id))


def pause(scope: str) -> tuple[bool, str]:
    cur = current(scope)
    if not cur:
        return False, "这个群没有在记的 log。"
    if cur["status"] == "off":
        return False, f"「{cur['name']}」本来就是暂停的。"
    _set(cur["id"], "off")
    return True, f"已暂停「{cur['name']}」。继续记：/log on　结束：/log end"


def resume(scope: str, key: str = "") -> tuple[bool, str]:
    """/log on：不带名字 = 继续暂停的当前 log；带名字 = 接着记指定的 log（已结束 / 强制停止的也行）。"""
    cur = current(scope)
    if not key.strip():
        if not cur:
            return False, "没有暂停的 log。想接着记以前的，用 /log on 名字（看 /log list）。"
        if cur["status"] == "on":
            return False, f"「{cur['name']}」正在记录中。"
        _set(cur["id"], "on")
        return True, f"继续记录「{cur['name']}」。"
    target = find(scope, key)
    if not target:
        return False, f"没有找到「{key}」，看 /log list。"
    if cur and cur["id"] != target["id"]:
        return False, f"现在还有一个没结束的 log「{cur['name']}」，先 /log end 结束它。"
    if target["status"] == "on":
        return False, f"「{target['name']}」正在记录中。"
    _set(target["id"], "on")
    return True, f"继续记录「{target['name']}」，新内容追加在后面。"


def finish(scope: str, halt: bool = False) -> tuple[dict | None, str]:
    cur = current(scope)
    if not cur:
        return None, "这个群没有在记的 log。"
    _set(cur["id"], "halted" if halt else "ended")
    return get(cur["id"]), ""


def delete(log_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM trpg_logs WHERE id=?", (log_id,))


# ======================== 记录 ========================

def add_line(log_id: int, speaker_key: str, speaker_name: str, kind: str, text: str, ref: int | None = None) -> int | None:
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM trpg_log_lines WHERE log_id=?", (log_id,)).fetchone()["n"]
        if n >= MAX_LINES:
            return None
        return c.execute("INSERT INTO trpg_log_lines(log_id,ts,speaker_key,speaker_name,kind,text,ref) VALUES (?,?,?,?,?,?,?)",
                         (log_id, int(time.time()), speaker_key, speaker_name, kind, text[:2000], ref)).lastrowid


def _norm(text: str) -> str:
    """去掉开头的 @某人，比较时两边一致。"""
    t = text.strip()
    while t.startswith("@"):
        t = t.split(None, 1)[1] if " " in t or "\u2005" in t or "\u00a0" in t else ""
        t = t.strip()
    return t


def find_duplicate(log_id: int, text: str, from_qq: bool, window: int = 8) -> int | None:
    """同一句话可能走两条路进来：卡拉收到 @ 的那条，和 QQ 号听到的那条。window 秒内、文字一样、
    而且来自另一条路（speaker_key 以 qq: 开头的算 QQ 号这条路），就是同一条，返回已有那行的 id。"""
    want, since = _norm(text), int(time.time()) - window
    with get_conn() as c:
        for r in c.execute("SELECT id, speaker_key, text FROM trpg_log_lines WHERE log_id=? AND kind='say' AND ts>=? ORDER BY id DESC LIMIT 30",
                           (log_id, since)):
            if r["speaker_key"].startswith("qq:") != from_qq and _norm(r["text"]) == want:
                return r["id"]
    return None


def add_member(log_id: int, player_id: int, card_id: int | None) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO trpg_log_members(log_id,player_id,card_id) VALUES (?,?,?) "
                  "ON CONFLICT(log_id,player_id) DO UPDATE SET card_id=COALESCE(excluded.card_id, card_id)", (log_id, player_id, card_id))


def is_observer(group: str, player_id: int) -> bool:
    return _row("SELECT 1 AS x FROM trpg_observers WHERE group_openid=? AND player_id=?", (group, player_id)) is not None


def toggle_observer(group: str, player_id: int) -> bool:
    """返回切换后是否为旁观者。"""
    with get_conn() as c:
        if c.execute("DELETE FROM trpg_observers WHERE group_openid=? AND player_id=?", (group, player_id)).rowcount:
            return False
        c.execute("INSERT INTO trpg_observers(group_openid,player_id) VALUES (?,?)", (group, player_id))
    return True


def lines(log_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM trpg_log_lines WHERE log_id=? ORDER BY ts, id", (log_id,))]


# ======================== 统计与导出 ========================

def stats(log_id: int) -> dict:
    """整场的总数，加上按人分的骰点（people）和按角色卡分的理智 / 生命损失（losses）。
    骰点归给谁：骰子那行的 ref 指向触发它的那句话，取那句话的发言人；对不上的算「（不明）」。"""
    from .trpgrender import verdict
    ls = lines(log_id)
    by_id = {l["id"]: l for l in ls}
    speakers = {l["speaker_name"] for l in ls if l["kind"] != "bot" and l["speaker_name"]}
    ok = bad = 0
    dice_n = 0
    people: dict[str, dict] = {}
    for l in ls:
        if l["kind"] != "dice":
            continue
        dice_n += 1
        who = (by_id.get(l["ref"]) or {}).get("speaker_name") or "（不明）"
        p = people.setdefault(who, {"name": who, "dice": 0, "crit": 0, "ok": 0, "bad": 0, "fumble": 0})
        p["dice"] += 1
        v = verdict(l["text"])
        if v:
            p[v[1]] += 1
            if v[1] in ("crit", "ok"):
                ok += 1
            else:
                bad += 1
    with get_conn() as c:
        changes = c.execute("SELECT COUNT(*) AS n FROM trpg_attr_history WHERE log_id=?", (log_id,)).fetchone()["n"]
        loss_rows = c.execute(
            "SELECT k.id AS cid, k.name AS card, h.name AS attr, SUM(h.old_value - h.new_value) AS lost FROM trpg_attr_history h "
            "JOIN trpg_cards k ON k.id=h.card_id WHERE h.log_id=? AND h.name IN ('理智','生命') "
            "AND h.old_value IS NOT NULL AND h.new_value IS NOT NULL AND h.new_value < h.old_value GROUP BY k.id, h.name",
            (log_id,)).fetchall()
    losses: dict[int, dict] = {}                         # 按卡的编号分：两个人的卡同名（默认的「角色1」）不能混成一个
    for r in loss_rows:
        losses.setdefault(r["cid"], {"name": r["card"], "理智": 0, "生命": 0})[r["attr"]] = r["lost"]
    for p in people.values():
        checks = p["crit"] + p["ok"] + p["bad"] + p["fumble"]
        p["checks"] = checks
        p["rate"] = round((p["crit"] + p["ok"]) * 100 / checks) if checks else None
    return {"lines": len(ls), "speakers": len(speakers), "dice": dice_n, "success": ok, "fail": bad, "changes": changes,
            "people": sorted(people.values(), key=lambda p: -p["dice"]), "losses": list(losses.values())}


def highlights(s: dict) -> list[str]:
    """团后战报里的几句「之最」。人少、检定少的时候不硬凑：骰运至少要 3 次检定才算。"""
    ppl = [p for p in s["people"] if p["name"] != "（不明）"]
    out = []

    def top(key, fmt):
        best = max(ppl, key=lambda p: p[key], default=None)
        if best and best[key] > 0:
            out.append(fmt.format(n=best["name"], v=best[key]))
    top("crit", "🌟 大成功最多：{n}（{v} 次）")
    top("fumble", "💥 大失败最多：{n}（{v} 次）")
    rated = [p for p in ppl if p["checks"] >= 3]
    if len(rated) >= 2:
        lucky = max(rated, key=lambda p: (p["rate"], p["checks"]))
        unlucky = min(rated, key=lambda p: (p["rate"], -p["checks"]))
        out.append(f"🍀 骰运最好：{lucky['name']}（检定 {lucky['checks']} 次，成功 {lucky['rate']}%）")
        if unlucky is not lucky and unlucky["rate"] < lucky["rate"]:
            out.append(f"🌧 骰运最差：{unlucky['name']}（检定 {unlucky['checks']} 次，成功 {unlucky['rate']}%）")
    for attr, icon in (("理智", "🌀"), ("生命", "🩸")):
        worst = max(s["losses"], key=lambda x: x[attr], default=None)
        if worst and worst[attr] > 0:
            out.append(f"{icon} {attr}掉得最多：{worst['name']}（-{worst[attr]}）")
    return out


def people_text(s: dict) -> str:
    rows = []
    for p in s["people"][:12]:
        parts = [f"大成功 {p['crit']}" if p["crit"] else "", f"成功 {p['ok']}" if p["ok"] else "",
                 f"失败 {p['bad']}" if p["bad"] else "", f"大失败 {p['fumble']}" if p["fumble"] else ""]
        detail = "、".join(x for x in parts if x)
        rows.append(f"· {p['name']}：骰 {p['dice']} 次" + (f"（{detail}）" if detail else ""))
    return "\n".join(rows)


def stats_text(log: dict) -> str:
    s = stats(log["id"])
    text = (f"「{log['name']}」（{STATUS_TEXT[log['status']]}）\n共 {s['lines']} 条，{s['speakers']} 位发言人\n"
            f"骰点 {s['dice']} 次，其中检定成功 {s['success']}、失败 {s['fail']}\n属性变动 {s['changes']} 次")
    if s["people"]:
        text += "\n\n【每个人】\n" + people_text(s)
    hl = highlights(s)
    if hl:
        text += "\n\n【本场之最】\n" + "\n".join(hl)
    return text


def export_txt(log_id: int) -> str:
    out = []
    for l in lines(log_id):
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(l["ts"]))
        out.append(f"{l['speaker_name'] or '骰子'}({t})\n{l['text']}\n")
    return "\n".join(out)


def logs_of_card(card_id: int) -> list[dict]:
    """这张卡参加过的战役（按时间倒序）。"""
    with get_conn() as c:
        rows = c.execute("SELECT l.* FROM trpg_log_members m JOIN trpg_logs l ON l.id=m.log_id "
                         "JOIN trpg_cards k ON k.player_id=m.player_id WHERE m.card_id=? AND k.id=m.card_id ORDER BY l.id DESC", (card_id,)).fetchall()
    return [dict(r) for r in rows]


# ======================== 分享与样式 ========================

def mark_shared(log_id: int, on: bool = True) -> None:
    with get_conn() as c:
        c.execute("UPDATE trpg_logs SET shared=? WHERE id=?", (1 if on else 0, log_id))


def by_token(token: str) -> dict | None:
    """分享链接用：只有标记了公开（/log end 或 /log get 过）的 log 才能按 token 打开。"""
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    return _row("SELECT * FROM trpg_logs WHERE token=? AND shared=1", (token,))


def share_url(log: dict) -> str:
    """分享网址；小手机的访问网址没配就返回空串。"""
    base = get_config("phone_base_url", "").rstrip("/")
    return f"{base}/l/{log['token']}" if base else ""


def save_style(log_id: int, style: dict) -> None:
    import json
    from .trpgrender import parse_style
    clean = parse_style(json.dumps(style))
    with get_conn() as c:
        c.execute("UPDATE trpg_logs SET palette=? WHERE id=?", (json.dumps(clean, ensure_ascii=False), log_id))
