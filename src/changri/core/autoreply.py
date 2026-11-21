"""自定义回复：群里有人说了某个词，机器人自动回一句。规则存在各机器人自己的库里（autoreplies 表）。
匹配只看文字；命中的第一条规则（按排序）生效；游戏指令永远优先，不会被这里抢走。
回复里能写 {角色}、{检定:侦查}（用说话人的角色卡检定）、{牌堆名}（从牌堆抽一张）、[1d6]（掷骰），见 render。"""
import json
import random
import re
import threading
import time

from .db import get_conn

MODES = {"contains": "包含", "exact": "完全等于", "prefix": "开头是", "suffix": "结尾是", "regex": "正则表达式"}
WHO = {"all": "所有人", "role": "有角色的玩家", "admin": "只有管理员"}
SEND = {"random": "随机发一条", "all": "依次全发"}
PLACE = {"all": "所有群", "personal": "只在个人群", "public": "只在个人群之外的群"}
MAX_RULES, MAX_WORDS, MAX_REPLIES, MAX_REPLY_LEN, MAX_TEXT = 200, 20, 10, 1000, 300

_last_hit: dict[tuple[str, int, str], float] = {}      # (机器人, 规则, 群) → 上次触发时间
_lock = threading.Lock()


def split_words(text: str) -> list[str]:
    return [w.strip() for w in re.split(r"[\n\r]+", text or "") if w.strip()]


def _clean_replies(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [p for p in re.split(r"\n\s*\n", raw.replace("\r", ""))]     # 空行隔开多条
    return [r.strip() for r in raw if r and r.strip()]


def _row(r) -> dict:
    d = dict(r)
    d["word_list"] = split_words(d["words"])
    try:
        d["reply_list"] = json.loads(d["replies"] or "[]")
    except ValueError:
        d["reply_list"] = []
    return d


def list_rules() -> list[dict]:
    with get_conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM autoreplies ORDER BY sort, id")]


def get_rule(rule_id: int) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM autoreplies WHERE id=?", (rule_id,)).fetchone()
    return _row(r) if r else None


def validate(data: dict) -> str:
    """返回空串表示没问题，否则是给人看的原因。"""
    words = split_words(data.get("words", ""))
    if not words:
        return "至少写一个触发词。"
    if len(words) > MAX_WORDS:
        return f"触发词最多 {MAX_WORDS} 个。"
    if data.get("mode") not in MODES:
        return "匹配方式不对。"
    if data.get("mode") == "regex":
        for w in words:
            if len(w) > 200:
                return "正则太长了（最多 200 字）。"
            try:
                re.compile(w)
            except re.error as e:
                return f"正则写得不对：{w}（{e}）"
    replies = _clean_replies(data.get("replies", ""))
    if not replies:
        return "要写一条回复。"
    if len(replies) > MAX_REPLIES or any(len(r) > MAX_REPLY_LEN for r in replies):
        return f"回复最多 {MAX_REPLIES} 条，每条不超过 {MAX_REPLY_LEN} 字。"
    if data.get("send_mode", "random") not in SEND:
        return "发送方式不对。"
    if data.get("who", "all") not in WHO or data.get("place", "all") not in PLACE:
        return "范围选项不对。"
    return ""


def save_rule(data: dict, rule_id: int | None = None) -> tuple[bool, str]:
    if err := validate(data):
        return False, err
    vals = (str(data.get("name", "")).strip()[:30], data["mode"], "\n".join(split_words(data["words"])),
            json.dumps(_clean_replies(data["replies"]), ensure_ascii=False), data.get("who", "all"), data.get("place", "all"),
            max(0, min(int(data.get("cooldown") or 0), 86400)), data.get("send_mode", "random"))
    with get_conn() as c:
        if rule_id:
            cur = c.execute("UPDATE autoreplies SET name=?, mode=?, words=?, replies=?, who=?, place=?, cooldown=?, send_mode=? WHERE id=?", vals + (rule_id,))
            return cur.rowcount > 0, "" if cur.rowcount else "没有这条规则。"
        if c.execute("SELECT COUNT(*) FROM autoreplies").fetchone()[0] >= MAX_RULES:
            return False, f"规则最多 {MAX_RULES} 条。"
        nxt = (c.execute("SELECT COALESCE(MAX(sort), 0) FROM autoreplies").fetchone()[0] or 0) + 1
        c.execute("INSERT INTO autoreplies(name, mode, words, replies, who, place, cooldown, send_mode, sort, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  vals + (nxt, int(time.time())))
    return True, ""


def delete_rule(rule_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM autoreplies WHERE id=?", (rule_id,))


def set_all_enabled(on: bool) -> int:
    """一键全关 / 全开。返回改了几条。"""
    with get_conn() as c:
        return c.execute("UPDATE autoreplies SET enabled=? WHERE enabled!=?", (1 if on else 0, 1 if on else 0)).rowcount


def set_enabled(rule_id: int, on: bool) -> None:
    with get_conn() as c:
        c.execute("UPDATE autoreplies SET enabled=? WHERE id=?", (1 if on else 0, rule_id))


def move(rule_id: int, delta: int) -> None:
    """上移/下移一位（排在前面的先判断）。"""
    rules = list_rules()
    ids = [r["id"] for r in rules]
    if rule_id not in ids:
        return
    i = ids.index(rule_id)
    j = i + (-1 if delta < 0 else 1)
    if not 0 <= j < len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    with get_conn() as c:
        for n, rid in enumerate(ids, 1):
            c.execute("UPDATE autoreplies SET sort=? WHERE id=?", (n, rid))


def matches(rule: dict, text: str) -> bool:
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return False
    for w in rule["word_list"]:
        m = rule["mode"]
        if m == "contains" and w in text:
            return True
        if m == "exact" and w == text:
            return True
        if m == "prefix" and text.startswith(w):
            return True
        if m == "suffix" and text.endswith(w):
            return True
        if m == "regex":
            try:
                if re.search(w, text):
                    return True
            except re.error:
                continue
    return False


CHECK_RE = re.compile(r"\{(?:检定|ra)\s*[:：]\s*([^{}]{1,40}?)\s*\}", re.I)
MAX_CHECKS = 3                      # 一条回复里最多掷几次检定，多的原样留着


def render(reply: str, role_name: str = "", rng: random.Random | None = None, check=None) -> str:
    """{角色} 换成角色名；{检定:侦查} 用说话人的角色卡做一次检定（跟 /ra 一样）；{牌堆名} 从牌堆抽一张、[1d6] 掷骰（跟「抽卡」同一套写法，见 core/decks）。
    check(技能) -> 检定结果文字，由插件给（要读说话人的角色卡、群的规则）；不给（后台「试一试」）就显示成占位。
    {角色} 永远是角色名（有人建了叫「角色」的牌堆也不影响），抽出来的牌里写的 {角色} 也换。
    只认带 d 的骰子，旧回复里本来写的 [1] 之类不动。找不到的牌堆原样留着。"""
    from . import decks
    name = role_name or "朋友"
    text = reply.replace("{角色}", name)
    n = [0]

    def do_check(m):
        if n[0] >= MAX_CHECKS:
            return m.group(0)
        n[0] += 1
        what = m.group(1)
        if check is None:
            return f"〔{what}检定结果〕"
        try:
            return str(check(what)).strip()
        except Exception:  # noqa: BLE001 检定出错只影响这一处
            return f"（{what}检定没掷成）"
    text = CHECK_RE.sub(do_check, text)
    try:
        text = decks.expand(text, rng or random.Random(), dice_need_d=True, keep=("角色",))
    except Exception:  # noqa: BLE001 牌堆出错就发原文，不能让自定义回复整条不发
        return text
    text = text.replace("{角色}", name)
    return text[:decks.MAX_OUT] + ("……" if len(text) > decks.MAX_OUT else "")


def chosen(rule: dict, role_name: str = "", check=None) -> list[str]:
    """这条规则这次要发的话：依次全发 = 全部（最多 5 条，官方机器人一次最多回 5 条），随机 = 挑一条。"""
    replies = rule["reply_list"]
    out = replies[:5] if rule.get("send_mode") == "all" else [random.choice(replies)] if replies else []
    return [render(r, role_name, check=check) for r in out]


def preview(text: str) -> list[dict]:
    """后台「试一试」：这句话会触发哪些规则（不看范围和冷却，只看词；排最前的才是真正会发的）。"""
    return [{"id": r["id"], "name": r["name"] or "（没起名）", "enabled": bool(r["enabled"]), "replies": chosen(r, "小明")}
            for r in list_rules() if matches(r, text)]


def pick(text: str, *, bot_id: str, group: str, has_role: bool, is_admin: bool, in_personal: bool, role_name: str = "", check=None) -> list[str] | None:
    """真正触发：返回要回复的话（一条或几条），没有命中返回 None。会检查开关、范围、冷却，并记一次命中。"""
    for r in list_rules():
        if not r["enabled"] or not r["reply_list"] or not matches(r, text):
            continue
        if r["who"] == "role" and not has_role or r["who"] == "admin" and not is_admin:
            continue
        if r["place"] == "personal" and not in_personal or r["place"] == "public" and in_personal:
            continue
        key = (bot_id, r["id"], group)
        now = time.time()
        with _lock:
            if r["cooldown"] and now - _last_hit.get(key, 0) < r["cooldown"]:
                return None                 # 命中了但在冷却里：不往后找别的规则，免得绕过冷却
            _last_hit[key] = now
        with get_conn() as c:
            c.execute("UPDATE autoreplies SET hits=hits+1 WHERE id=?", (r["id"],))
        return chosen(r, role_name, check)
    return None
