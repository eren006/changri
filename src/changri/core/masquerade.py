"""匿名舞会（额外扩展，默认关闭）：一个所有人都以面具 + 昵称发言的特别群聊，自带音乐播放器。只在小手机里，不进 QQ、不占短信次数。
  · 入场：从面具库随机抽 4 副（已被选走的不会出现）四选一，再起一个舞会昵称（不能是任何人的名字/微信名，也不能带名字）。入场后玩家自己不能改，暴露身份的话后台直接改昵称。
  · 邀请跳舞：只对舞会里的人，每人最多邀请 3 个；对方接受后开一段临时对话，两人合计最多 20 句，双方都只看到面具和昵称。
  · NPC 不戴面具，只能用真名在舞厅说话。
  · 后台能开场/打烊/新开一场（上一场连真名对照存档）/全员禁言/发旁白/定时旁白/自动开关场/改昵称/删消息/看回放。
时间戳都是毫秒。前端拿到的状态里绝不出现真名。"""
import json
import random
import time

from .db import get_config, get_conn, set_config

MUSIC = {
    "medieval": {"title": "Maschera di Roma", "label": "欧洲中世纪", "file": "maschera_di_roma.m4a"},
    "classic": {"title": "宫宴余韵", "label": "中国古风", "file": "gong_yan_yu_yun.m4a"},
    "space": {"title": "Deep Space Masquerade", "label": "太空未来", "file": "deep_space_masquerade.m4a"},
    "gothic": {"title": "Masquerade at the Academy", "label": "哈利波特哥特风", "file": "masquerade_at_the_academy.m4a"},
}
MASKS = [
    ("银狐面具", "笑意盈盈，没人看得清你究竟在盘算什么。"), ("白羽面具", "轻得像一片羽毛，走到哪里都不留声响。"),
    ("猩红玫瑰", "开得最艳的那一朵，也带着最尖的刺。"), ("黑金蝴蝶", "翅膀上落着碎金，从灯影里一闪而过。"),
    ("鸦羽面具", "安静地站在高处，什么都看在眼里。"), ("翡翠孔雀", "把骄傲收在面具后面，只偶尔展开一点点。"),
    ("幽蓝海妖", "歌声里藏着潮水，让人忍不住靠近。"), ("紫罗兰夜", "夜色最深的时候，才肯露出一点温柔。"),
    ("白瓷小丑", "笑容是画上去的，真话藏在玩笑里。"), ("琥珀猫眼", "懒洋洋地看着一切，随时准备溜走。"),
    ("乌木鹿角", "沉默而庄重，像林间无人踏过的清晨。"), ("珍珠人鱼", "来自很深很远的地方，今晚只想跳一支舞。"),
    ("烬红恶魔", "坏得坦荡，却从不伤害真心的人。"), ("霜雪天使", "冷冷清清的外表下，是一颗不肯说出口的心。"),
    ("墨绿藤蔓", "悄悄缠绕，不动声色，却越长越深。"), ("星砂面具", "每一步都落下细碎的光，像把银河带进舞池。"),
    ("古铜齿轮", "精密、克制，嘀嗒作响，等一个合拍的人。"), ("赤金狮面", "走进来的时候，整个舞厅都安静了一瞬。"),
    ("夜莺", "只在夜里歌唱，天亮就不认账。"), ("薄荷暖风", "清清凉凉，擦肩而过时带来一点春天。"),
    ("残月", "总是缺了一角，所以才格外想被人看见。"), ("铜钥匙", "什么锁都想试一试，尤其是不该开的那扇。"),
    ("烛光", "不够亮，却足够让人想靠近取暖。"), ("双面", "一半是真，一半是假，由你来猜哪边。"),
    ("雪鸮", "静静地落在枝头，转头时才让人心里一惊。"), ("灰狼", "独来独往，却在月光下比谁都想有个同伴。"),
    ("樱雾", "朦朦胧胧的粉色，让人分不清是梦还是醒。"), ("镜中人", "你看见的，也许只是你自己想看见的样子。"),
]
DESC = dict(MASKS)
SUFFIX = ["", "·贰", "·叁", "·肆", "·伍", "·陆", "·柒", "·捌"]       # 面具库被选光（人比面具多）时用，保证仍然不重复
NICK_MAX = 10
MSG_MAX = 120
KEEP = 150
INVITE_MAX = 3
DANCE_MAX = 20
GAP_MS = 1500
NARR_MAX = 80
REACTIONS = ["❤️", "👏", "😂", "🌹", "✨"]
_rate: dict = {}


def _now() -> int:
    return int(time.time() * 1000)


# ---------- 开关与设置 ----------
def is_on() -> bool:
    return get_config("masq_on", "0") == "1"


def set_on(on: bool) -> None:
    """开场/打烊；每次从关到开都刷新开场时间，玩家手机据此再弹一次入场提示。"""
    if on and not is_on():
        set_config("masq_opened_ms", str(_now()))
    set_config("masq_on", "1" if on else "0")


def opened_ms() -> int:
    v = get_config("masq_opened_ms", "0")
    return int(v) if v.isdigit() else 0


def music() -> dict:
    key = get_config("masq_music", "medieval")
    key = key if key in MUSIC else "medieval"
    return dict(MUSIC[key], key=key)


def set_music(key: str) -> bool:
    if key not in MUSIC:
        return False
    set_config("masq_music", key)
    return True


def mute_state() -> dict | None:
    return {"until": -1, "text": "舞会暂时全员禁言中，请等管理员解除"} if get_config("masq_muted", "0") == "1" else None


def set_mute(on: bool) -> None:
    set_config("masq_muted", "1" if on else "0")


def base(mask: str) -> str:
    return mask.split("·")[0]


def mask_desc(mask: str) -> str:
    return DESC.get(base(mask), "")


# ---------- 名册 ----------
def npc_ids() -> set[int]:
    with get_conn() as c:
        return {r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=1")}


def is_npc(role_id: int) -> bool:
    return role_id in npc_ids()


def member(role_id: int, create: bool = True):
    with get_conn() as c:
        row = c.execute("SELECT * FROM masq_members WHERE role_id=?", (role_id,)).fetchone()
        if not row and create:
            c.execute("INSERT INTO masq_members(role_id, offered, joined_ts) VALUES(?,?,?)", (role_id, json.dumps(offer(), ensure_ascii=False), _now()))
            row = c.execute("SELECT * FROM masq_members WHERE role_id=?", (role_id,)).fetchone()
    return row


def ready(role_id: int):
    m = member(role_id, create=False)
    return m if m and m["mask"] and m["nick"] else None


def offer(k: int = 4) -> list[str]:
    with get_conn() as c:
        taken = {r["mask"] for r in c.execute("SELECT mask FROM masq_members WHERE mask!=''")}
    pool = [n for n, _ in MASKS if n not in taken]
    for suf in SUFFIX[1:]:
        if len(pool) >= k:
            break
        pool += [n + suf for n, _ in MASKS if n + suf not in taken]
    return random.SystemRandom().sample(pool, min(k, len(pool)))


def pub(m) -> dict:
    return {"id": int(m["id"]), "mask": m["mask"], "ch": m["mask"][:1], "desc": mask_desc(m["mask"]), "nick": m["nick"]}


def nick_check(role_id: int, nick: str) -> str:
    """返回错误文字，空串 = 可用。"""
    nick = (nick or "").strip()
    if not nick:
        return "给自己起一个昵称"
    if len(nick) > NICK_MAX:
        return f"昵称最多 {NICK_MAX} 个字"
    if "@" in nick or "＠" in nick:
        return "昵称里不能有 @"
    with get_conn() as c:
        names = {r["name"] for r in c.execute("SELECT name FROM roles")} | {r["nick"] for r in c.execute("SELECT nick FROM role_nicks")}
        low = nick.lower()
        for n in names:
            n = (n or "").strip()
            if n and (n.lower() == low or (len(n) >= 2 and n.lower() in low)):
                return "昵称里不能带任何人的名字——舞会上不要暴露身份"
        if c.execute("SELECT 1 FROM masq_members WHERE nick=? AND role_id!=?", (nick, role_id)).fetchone():
            return "舞会上已经有人叫这个名字了，换一个"
    return ""


def pick_mask(role_id: int, pick: str) -> tuple[bool, str, bool]:
    """返回 (成功, 提示, 要不要前端重新抽)。"""
    me = member(role_id)
    if me["mask"]:
        return False, "你已经戴上面具了", False
    try:
        offers = json.loads(me["offered"] or "[]")
    except ValueError:
        offers = []
    if pick not in offers:
        return False, "请从给你的四副里选一副", False
    with get_conn() as c:
        if c.execute("SELECT 1 FROM masq_members WHERE mask=?", (pick,)).fetchone():
            c.execute("UPDATE masq_members SET offered=? WHERE id=?", (json.dumps(offer(), ensure_ascii=False), me["id"]))
            return False, "这副面具刚刚被别人选走了，给你换了一批新的", True
        c.execute("UPDATE masq_members SET mask=?, offered='[]' WHERE id=?", (pick, me["id"]))
    return True, "", False


def set_nick(role_id: int, nick: str) -> tuple[bool, str]:
    me = member(role_id)
    if not me["mask"]:
        return False, "先选一副面具"
    if me["nick"]:
        return False, "昵称已经定下了，要改请找管理员"
    nick = (nick or "").strip()
    bad = nick_check(role_id, nick)
    if bad:
        return False, bad
    with get_conn() as c:
        c.execute("UPDATE masq_members SET nick=? WHERE id=?", (nick, me["id"]))
    return True, ""


def admin_set_nick(role_id: int, nick: str) -> str:
    nick = (nick or "").strip()
    with get_conn() as c:
        m = c.execute("SELECT * FROM masq_members WHERE role_id=?", (role_id,)).fetchone()
    if not m:
        return "找不到这个人"
    bad = nick_check(role_id, nick)
    if bad:
        return bad
    with get_conn() as c:
        c.execute("UPDATE masq_members SET nick=? WHERE id=?", (nick, m["id"]))
    return ""


# ---------- 舞厅 ----------
def narrate(text: str, ts: int | None = None) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO masq_msgs(role_id, content, kind, created_ts) VALUES(0,?,'narr',?)", (text, ts or _now()))


def throttle(key) -> bool:
    now = _now()
    if now - _rate.get(key, 0) < GAP_MS:
        return True
    _rate[key] = now
    if len(_rate) > 5000:
        _rate.clear()
    return False


def clean_text(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    if not text:
        return "", "内容不能为空"
    if len(text) > MSG_MAX:
        return "", f"太长了，最多 {MSG_MAX} 字"
    return text, ""


def _find_mention(author_mid: int, text: str) -> int:
    best_len, best_id = 0, 0
    with get_conn() as c:
        for m in c.execute("SELECT id, mask, nick FROM masq_members WHERE mask!='' AND nick!=''"):
            if m["id"] == author_mid:
                continue
            for token in (m["mask"], m["nick"]):
                if token and ("@" + token) in text and len(token) > best_len:
                    best_len, best_id = len(token), m["id"]
    return best_id


def say(role_id: int, text: str) -> tuple[bool, str]:
    npc = is_npc(role_id)
    me = None if npc else ready(role_id)
    if not npc and not me:
        return False, "先戴上面具、起好昵称"
    mute = mute_state()
    if mute:
        return False, mute["text"]
    if throttle(("say", role_id)):
        return False, "说得太快了，歇一口气"
    text, bad = clean_text(text)
    if bad:
        return False, bad
    with get_conn() as c:
        c.execute("INSERT INTO masq_msgs(role_id, content, mention_id, created_ts) VALUES(?,?,?,?)", (role_id, text, _find_mention(me["id"] if me else 0, text), _now()))
    return True, ""


def react(role_id: int, msg_id: int, emoji: str) -> tuple[bool, str]:
    if not ready(role_id):
        return False, "先戴上面具、起好昵称"
    if emoji not in REACTIONS:
        return False, "不认识这个表情"
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM masq_msgs WHERE id=? AND deleted=0 AND kind='msg'", (msg_id,)).fetchone():
            return False, "这句话已经不在了"
        cur = c.execute("SELECT emoji FROM masq_reacts WHERE msg_id=? AND role_id=?", (msg_id, role_id)).fetchone()
        if cur and cur["emoji"] == emoji:
            c.execute("DELETE FROM masq_reacts WHERE msg_id=? AND role_id=?", (msg_id, role_id))
        else:
            c.execute("INSERT INTO masq_reacts(msg_id, role_id, emoji, created_ts) VALUES(?,?,?,?) ON CONFLICT(msg_id, role_id) DO UPDATE SET emoji=excluded.emoji, created_ts=excluded.created_ts",
                      (msg_id, role_id, emoji, _now()))
    return True, ""


def invite(role_id: int, to_member_id: int) -> tuple[bool, str]:
    mute = mute_state()
    if mute:
        return False, mute["text"]
    if not ready(role_id):
        return False, "先戴上面具、起好昵称"
    with get_conn() as c:
        to = c.execute("SELECT * FROM masq_members WHERE id=? AND mask!='' AND nick!=''", (to_member_id,)).fetchone()
        if not to or to["role_id"] == role_id:
            return False, "找不到这位舞伴"
        if c.execute("SELECT COUNT(*) FROM masq_invites WHERE from_id=?", (role_id,)).fetchone()[0] >= INVITE_MAX:
            return False, f"你的 {INVITE_MAX} 次邀请都用完了"
        if c.execute("SELECT 1 FROM masq_invites WHERE ((from_id=? AND to_id=?) OR (from_id=? AND to_id=?)) AND status IN ('pending','accepted','ended')",
                     (role_id, to["role_id"], to["role_id"], role_id)).fetchone():
            return False, "你们已经有一段舞了，或者邀请还在等回应"
        c.execute("INSERT INTO masq_invites(from_id, to_id, created_ts) VALUES(?,?,?)", (role_id, to["role_id"], _now()))
    return True, ""


def answer(role_id: int, iid: int, accept: bool) -> tuple[bool, str]:
    with get_conn() as c:
        iv = c.execute("SELECT * FROM masq_invites WHERE id=? AND to_id=?", (iid, role_id)).fetchone()
        if not iv or iv["status"] != "pending":
            return False, "这份邀请已经处理过了"
        c.execute("UPDATE masq_invites SET status=?, answered_ts=? WHERE id=?", ("accepted" if accept else "declined", _now(), iid))
    return True, ""


def dance_say(role_id: int, iid: int, text: str) -> tuple[bool, str]:
    mute = mute_state()
    if mute:
        return False, mute["text"]
    if throttle(("dance", role_id)):
        return False, "说得太快了，歇一口气"
    text, bad = clean_text(text)
    if bad:
        return False, bad
    with get_conn() as c:
        iv = c.execute("SELECT * FROM masq_invites WHERE id=? AND (from_id=? OR to_id=?)", (iid, role_id, role_id)).fetchone()
        if not iv or iv["status"] not in ("accepted", "ended"):
            return False, "这段舞还没有开始"
        if iv["cnt"] >= DANCE_MAX:
            return False, "舞曲已经终了，这段对话到此为止"
        c.execute("INSERT INTO masq_dance_msgs(invite_id, role_id, content, created_ts) VALUES(?,?,?,?)", (iid, role_id, text, _now()))
        c.execute("UPDATE masq_invites SET cnt=cnt+1, status=CASE WHEN cnt+1>=? THEN 'ended' ELSE status END WHERE id=?", (DANCE_MAX, iid))
    return True, ""


# ---------- 到点的事 ----------
def purge_npc() -> None:
    npcs = sorted(npc_ids())
    if not npcs:
        return
    q = ",".join("?" * len(npcs))
    with get_conn() as c:
        if not c.execute(f"SELECT 1 FROM masq_members WHERE role_id IN ({q}) UNION ALL SELECT 1 FROM masq_invites WHERE from_id IN ({q}) OR to_id IN ({q}) "
                         f"UNION ALL SELECT 1 FROM masq_reacts WHERE role_id IN ({q}) LIMIT 1", npcs * 4).fetchone():
            return
        c.execute(f"DELETE FROM masq_dance_msgs WHERE invite_id IN (SELECT id FROM masq_invites WHERE from_id IN ({q}) OR to_id IN ({q}))", npcs * 2)
        c.execute(f"DELETE FROM masq_invites WHERE from_id IN ({q}) OR to_id IN ({q})", npcs * 2)
        c.execute(f"DELETE FROM masq_reacts WHERE role_id IN ({q})", npcs)
        c.execute(f"DELETE FROM masq_members WHERE role_id IN ({q})", npcs)


def tick() -> None:
    """定时旁白、自动开场/打烊。玩家每次轮询、后台打开页面时顺手跑。"""
    purge_npc()
    now = _now()

    def num(k):
        v = get_config(k, "0")
        return int(v) if v.lstrip("-").isdigit() else 0
    open_at, close_at, on = num("masq_open_at"), num("masq_close_at"), is_on()
    open_due, close_due = bool(open_at and open_at <= now), bool(close_at and close_at <= now)
    if open_due and close_due and open_at < close_at:      # 两个时间都过了（比如服务停过）：这场错过了，只清掉
        set_config("masq_open_at", "0")
        set_config("masq_close_at", "0")
        return
    if open_due:
        set_config("masq_open_at", "0")
        if not on:
            if get_config("masq_open_mode", "continue") == "new":
                new_session()
                reopen()
            else:
                set_on(True)
                narrate("舞会开场了，请戴上面具入场。")
            on = True
    if close_due:
        set_config("masq_close_at", "0")
        if on:
            narrate("舞曲终了，舞会到此结束，谢谢各位。")
            set_on(False)
            on = False
    if on:
        with get_conn() as c:
            due = c.execute("SELECT * FROM masq_sched WHERE sent=0 AND at<=? ORDER BY at, id", (now,)).fetchall()
            for r in due:
                if c.execute("UPDATE masq_sched SET sent=1 WHERE id=? AND sent=0", (r["id"],)).rowcount == 1 and now - r["at"] < 30 * 60000:
                    c.execute("INSERT INTO masq_msgs(role_id, content, kind, created_ts) VALUES(0,?,'narr',?)", (r["text"], r["at"]))


# ---------- 场次 ----------
def has_data() -> bool:
    with get_conn() as c:
        return bool(c.execute("SELECT 1 FROM masq_members WHERE mask!='' LIMIT 1").fetchone() or c.execute("SELECT 1 FROM masq_msgs WHERE deleted=0 AND kind='msg' LIMIT 1").fetchone())


def _stamp(ts: int) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts / 1000)) if ts else "—"


def sessions() -> list[dict]:
    """所有场次（新的在前）：进行中/最近一场（还没存档）+ 往期。"""
    with get_conn() as c:
        past = c.execute("SELECT * FROM masq_archives ORDER BY no DESC").fetchall()
        out = []
        if has_data():
            guests = c.execute("SELECT COUNT(*) FROM masq_members WHERE mask!=''").fetchone()[0]
            msgs = c.execute("SELECT COUNT(*) FROM masq_msgs WHERE deleted=0 AND kind='msg'").fetchone()[0]
            t0 = c.execute("SELECT MIN(joined_ts) FROM masq_members").fetchone()[0] or 0
            out.append({"no": (past[0]["no"] if past else 0) + 1, "current": True, "label": f"{_stamp(t0)} ~ 现在", "guests": guests, "msgs": msgs})
        for r in past:
            out.append({"no": r["no"], "current": False, "label": f"{_stamp(r['started_ts'])} ~ {_stamp(r['ended_ts'])}", "guests": r["guests"], "msgs": r["msgs"]})
    return out


def session_text(no: int) -> str | None:
    with get_conn() as c:
        row = c.execute("SELECT transcript FROM masq_archives WHERE no=?", (no,)).fetchone()
    if row:
        return row["transcript"]
    cur = next((s for s in sessions() if s["current"] and s["no"] == no), None)
    return transcript() if cur else None


def transcript() -> str:
    """整场回放（含真名对照）：纯文本。"""
    with get_conn() as c:
        members = c.execute("SELECT m.*, r.name AS rname FROM masq_members m JOIN roles r ON r.id=m.role_id ORDER BY m.id").fetchall()
        look = {m["role_id"]: m for m in members}
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        npcs = npc_ids()

        def tag(rid):
            m = look.get(rid)
            if m and m["mask"]:
                return f"{m['rname']}（{m['mask']}「{m['nick']}」）"
            return f"{names.get(rid, '?')}（NPC 实名）" if rid in npcs else f"{names.get(rid, '?')}（未入场）"
        lines = ["匿名舞会回放", f"导出时间：{time.strftime('%Y-%m-%d %H:%M')}", "", "【客人对照】"]
        for m in members:
            lines.append(f"· {m['rname']} = {m['mask'] or '（没选面具）'}" + (f"「{m['nick']}」" if m["nick"] else ""))
        lines += ["", "【舞厅记录】"]
        reacts: dict = {}
        for r in c.execute("SELECT msg_id, role_id, emoji FROM masq_reacts ORDER BY created_ts"):
            reacts.setdefault(r["msg_id"], []).append(f"{r['emoji']}{names.get(r['role_id'], '?')}")
        for r in c.execute("SELECT * FROM masq_msgs WHERE deleted=0 ORDER BY id"):
            if r["kind"] == "narr":
                lines.append(f"{_stamp(r['created_ts'])}　—— {r['content']} ——")
                continue
            ment = ""
            if r["mention_id"]:
                t = c.execute("SELECT role_id FROM masq_members WHERE id=?", (r["mention_id"],)).fetchone()
                ment = f"　[@{names.get(t['role_id'], '?')}]" if t else ""
            lines.append(f"{_stamp(r['created_ts'])}　{tag(r['role_id'])}：{r['content']}{ment}")
            if r["id"] in reacts:
                lines.append("　　　　回应：" + "　".join(reacts[r["id"]]))
        lines += ["", "【邀请与私聊】"]
        label = {"pending": "没有回应", "accepted": "共舞", "declined": "婉拒", "ended": "舞曲终了"}
        for iv in c.execute("SELECT * FROM masq_invites ORDER BY id"):
            lines.append(f"{_stamp(iv['created_ts'])}　{tag(iv['from_id'])} 邀请 {tag(iv['to_id'])}　［{label.get(iv['status'], iv['status'])}］")
            for d in c.execute("SELECT * FROM masq_dance_msgs WHERE invite_id=? AND deleted=0 ORDER BY id", (iv["id"],)):
                lines.append(f"　　{_stamp(d['created_ts'])}　{names.get(d['role_id'], '?')}：{d['content']}")
    return "\n".join(lines) + "\n"


def new_session() -> bool:
    """新开一场：上一场（含真名对照）存档，清空面具/昵称/舞厅/回应/邀请/私聊/定时旁白。返回是否真的存了档。"""
    archived = False
    if has_data():
        text = transcript()
        with get_conn() as c:
            guests = c.execute("SELECT COUNT(*) FROM masq_members WHERE mask!=''").fetchone()[0]
            msgs = c.execute("SELECT COUNT(*) FROM masq_msgs WHERE deleted=0 AND kind='msg'").fetchone()[0]
            t0 = c.execute("SELECT MIN(joined_ts) FROM masq_members").fetchone()[0] or 0
            no = (c.execute("SELECT COALESCE(MAX(no),0) FROM masq_archives").fetchone()[0] or 0) + 1
            c.execute("INSERT INTO masq_archives(no, started_ts, ended_ts, guests, msgs, transcript) VALUES(?,?,?,?,?,?)", (no, t0, _now(), guests, msgs, text))
        archived = True
    with get_conn() as c:
        for t in ("masq_reacts", "masq_dance_msgs", "masq_invites", "masq_msgs", "masq_sched", "masq_members"):
            c.execute(f"DELETE FROM {t}")
    return archived


def reopen() -> None:
    set_config("masq_on", "1")
    set_config("masq_opened_ms", str(_now()))
    narrate("新的一场舞会开始了，请重新选择你的面具。")


# ---------- 给前端的状态 ----------
def state(role_id: int, ro: bool = False) -> dict:
    """一次返回全部状态（轮询用）。真名绝不出现在这里。"""
    tick()
    npc = is_npc(role_id)
    me = None if npc else member(role_id, create=not ro)
    st = {"ok": True, "music": music(), "now": _now(), "limits": {"msg": MSG_MAX, "nick": NICK_MAX, "dance": DANCE_MAX, "invite": INVITE_MAX}, "ro": ro, "npc": npc}
    if not npc and ro and not (me and me["mask"] and me["nick"]):
        st["stage"] = "none"
        return st
    if not npc and not me["mask"]:
        try:
            offers = json.loads(me["offered"] or "[]")
        except ValueError:
            offers = []
        with get_conn() as c:
            taken = {r["mask"] for r in c.execute("SELECT mask FROM masq_members WHERE mask!=''")}
            if not offers or any(o in taken for o in offers):       # 手里的备选被别人选走了就重抽
                offers = offer()
                c.execute("UPDATE masq_members SET offered=? WHERE id=?", (json.dumps(offers, ensure_ascii=False), me["id"]))
        st.update(stage="mask", offers=[{"mask": m, "desc": mask_desc(m)} for m in offers])
        return st
    if not npc and not me["nick"]:
        st.update(stage="nick", me=pub(me))
        return st
    mid = int(me["id"]) if me else 0
    st.update(stage="room", me=pub(me) if me else {"id": 0, "mask": "", "ch": "我", "desc": "", "nick": "NPC"})
    with get_conn() as c:
        members = c.execute("SELECT * FROM masq_members WHERE mask!='' AND nick!='' ORDER BY id").fetchall()
        by_mid = {m["id"]: m for m in members}
        by_role = {m["role_id"]: m for m in members}
        npc_names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles WHERE is_npc=1")}
        msgs = c.execute("SELECT * FROM masq_msgs WHERE deleted=0 ORDER BY id DESC LIMIT ?", (KEEP,)).fetchall()[::-1]
        ids = [r["id"] for r in msgs]
        counts: dict = {}
        mine_r: dict = {}
        by_who: dict = {}
        if ids:
            q = ",".join("?" * len(ids))
            for r in c.execute(f"SELECT msg_id, emoji, COUNT(*) AS n FROM masq_reacts WHERE msg_id IN ({q}) GROUP BY msg_id, emoji", ids):
                counts.setdefault(r["msg_id"], {})[r["emoji"]] = r["n"]
            for r in c.execute(f"SELECT msg_id, emoji FROM masq_reacts WHERE role_id=? AND msg_id IN ({q})", [role_id] + ids):
                mine_r[r["msg_id"]] = r["emoji"]
            mine_ids = [r["id"] for r in msgs if r["kind"] != "narr" and r["role_id"] == role_id]
            if mine_ids:                      # 谁贴的：只有被贴那句的作者看得到（面具+昵称）
                q2 = ",".join("?" * len(mine_ids))
                for r in c.execute(f"SELECT msg_id, role_id, emoji FROM masq_reacts WHERE msg_id IN ({q2}) ORDER BY created_ts", mine_ids):
                    if r["role_id"] in by_role:
                        by_who.setdefault(r["msg_id"], []).append({"emoji": r["emoji"], "who": pub(by_role[r["role_id"]])})
        out = []
        for r in msgs:
            if r["kind"] == "narr":
                out.append({"id": r["id"], "ts": r["created_ts"], "kind": "narr", "text": r["content"]})
                continue
            mine = r["role_id"] == role_id
            if r["role_id"] in by_role:
                who = pub(by_role[r["role_id"]])
            elif r["role_id"] in npc_names:
                who = {"mask": "", "ch": npc_names[r["role_id"]][:1], "desc": "", "nick": npc_names[r["role_id"]], "id": 0, "npc": True}
            else:
                who = {"mask": "无名客", "ch": "无", "desc": "", "nick": "", "id": 0}
            out.append({"id": r["id"], "ts": r["created_ts"], "kind": "msg", "text": r["content"], "mine": mine, **({"react_by": by_who.get(r["id"], [])} if mine else {}),
                        "who": who, "react": counts.get(r["id"], {}), "my_react": mine_r.get(r["id"], ""), "to_me": bool(mid) and r["mention_id"] == mid,
                        "mention": pub(by_mid[r["mention_id"]]) if r["mention_id"] in by_mid else None})
        st["msgs"] = out
        st["reactions"] = REACTIONS
        st["muted"] = mute_state()
        if not ro and not npc and ids and ids[-1] > (me["seen_id"] or 0):
            c.execute("UPDATE masq_members SET seen_id=? WHERE id=?", (ids[-1], mid))
        if ro or npc:
            st.update(members=[], invites_left=0, dances=[])
            return st
        st["members"] = [pub(m) for m in members if m["id"] != mid]
        sent = c.execute("SELECT COUNT(*) FROM masq_invites WHERE from_id=?", (role_id,)).fetchone()[0]
        st["invites_left"] = max(0, INVITE_MAX - sent)
        dances = []
        for iv in c.execute("SELECT * FROM masq_invites WHERE from_id=? OR to_id=? ORDER BY id DESC", (role_id, role_id)):
            outgoing = iv["from_id"] == role_id
            other = by_role.get(iv["to_id"] if outgoing else iv["from_id"])
            if not other:
                continue
            dm = []
            if iv["status"] in ("accepted", "ended"):
                dm = [{"id": r["id"], "ts": r["created_ts"], "text": r["content"], "mine": r["role_id"] == role_id}
                      for r in c.execute("SELECT * FROM masq_dance_msgs WHERE invite_id=? AND deleted=0 ORDER BY id", (iv["id"],))]
            status = "ended" if iv["status"] == "accepted" and iv["cnt"] >= DANCE_MAX else iv["status"]
            dances.append({"id": iv["id"], "dir": "out" if outgoing else "in", "status": status, "with": pub(other), "cnt": iv["cnt"], "msgs": dm, "ts": iv["created_ts"]})
        st["dances"] = dances
    return st


def inbox_row(role_id: int) -> dict | None:
    """消息列表里「匿名舞会」那一行；没开场返回 None。"""
    if not is_on():
        return None
    tick()
    me = member(role_id, create=False)
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM masq_members WHERE mask!='' AND nick!=''").fetchone()[0]
        last = c.execute("SELECT m.*, x.mask, x.nick FROM masq_msgs m LEFT JOIN masq_members x ON x.role_id=m.role_id AND m.role_id!=0 WHERE m.deleted=0 ORDER BY m.id DESC LIMIT 1").fetchone()
        newer = bool(last and c.execute("SELECT 1 FROM masq_msgs WHERE deleted=0 AND role_id!=? AND id>? LIMIT 1", (role_id, (me["seen_id"] if me else 0) or 0)).fetchone())
        pending = c.execute("SELECT COUNT(*) FROM masq_invites WHERE to_id=? AND status='pending'", (role_id,)).fetchone()[0]
    if last:
        who = "旁白" if last["kind"] == "narr" else ("我" if last["role_id"] == role_id else (f"{last['mask']}「{last['nick']}」" if last["mask"] else "无名客"))
        preview = f"{who}：" + last["content"].replace("\n", " ")
        ts = last["created_ts"]
    else:
        preview = "舞会开场了，来选一副面具吧" if not (me and me["mask"]) else "舞会开场了，戴上面具入场吧"
        ts = opened_ms() or _now()
    return {"n": n, "preview": preview, "time": time.strftime("%m-%d %H:%M", time.localtime(max(ts, opened_ms()) / 1000)), "unread": newer or bool(pending) or not (me and me["mask"])}


# ---------- 后台 ----------
def _dt_local(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M", time.localtime(ms / 1000)) if ms else ""


def parse_dt(raw: str) -> int:
    """datetime-local 的值 → 毫秒；空串 0；格式不对 -1。"""
    raw = (raw or "").strip()
    if not raw:
        return 0
    try:
        return int(time.mktime(time.strptime(raw, "%Y-%m-%dT%H:%M")) * 1000)
    except ValueError:
        return -1


def admin_view() -> dict:
    tick()
    with get_conn() as c:
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
        members = c.execute("SELECT * FROM masq_members ORDER BY id").fetchall()
        look = {m["role_id"]: m for m in members}
        npcs = npc_ids()

        def tag(rid):
            m = look.get(rid)
            if rid in npcs:
                return "NPC 实名"
            return f"{m['mask']}「{m['nick']}」" if m and m["mask"] else "（还没选面具）"
        react_by: dict = {}
        for r in c.execute("SELECT msg_id, role_id, emoji FROM masq_reacts"):
            react_by.setdefault(r["msg_id"], []).append(f"{r['emoji']}{names.get(r['role_id'], '?')}")
        msgs = [{"id": r["id"], "name": names.get(r["role_id"], ""), "who": "旁白" if r["kind"] == "narr" else tag(r["role_id"]), "text": r["content"], "time": _stamp(r["created_ts"]),
                 "narr": r["kind"] == "narr", "reacts": "　".join(react_by.get(r["id"], []))}
                for r in c.execute("SELECT * FROM masq_msgs WHERE deleted=0 ORDER BY id DESC LIMIT 100")]
        dances = []
        for iv in c.execute("SELECT * FROM masq_invites ORDER BY id DESC LIMIT 60"):
            dm = [{"id": r["id"], "name": names.get(r["role_id"], ""), "who": tag(r["role_id"]), "text": r["content"], "time": _stamp(r["created_ts"])}
                  for r in c.execute("SELECT * FROM masq_dance_msgs WHERE invite_id=? AND deleted=0 ORDER BY id", (iv["id"],))]
            dances.append({"id": iv["id"], "from": names.get(iv["from_id"], ""), "to": names.get(iv["to_id"], ""), "from_tag": tag(iv["from_id"]), "to_tag": tag(iv["to_id"]),
                           "status": iv["status"], "cnt": iv["cnt"], "msgs": dm, "time": _stamp(iv["created_ts"])})
        sched = [{"id": r["id"], "at": _dt_local(r["at"]), "text": r["text"]} for r in c.execute("SELECT * FROM masq_sched WHERE sent=0 ORDER BY at, id")]
        mem = [{"role_id": m["role_id"], "name": names.get(m["role_id"], ""), "mask": m["mask"], "nick": m["nick"]} for m in members]

    def num(k):
        v = get_config(k, "0")
        return int(v) if v.isdigit() else 0
    return {"on": is_on(), "music": music(), "tracks": MUSIC, "members": mem, "msgs": msgs, "dances": dances, "sched": sched, "has_data": has_data(), "sessions": sessions(),
            "mute": mute_state(), "open_at": _dt_local(num("masq_open_at")), "close_at": _dt_local(num("masq_close_at")), "open_mode": get_config("masq_open_mode", "continue"), "narr_max": NARR_MAX}


def delete_msg(mid: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE masq_msgs SET deleted=1 WHERE id=?", (mid,))


def delete_invite(iid: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM masq_dance_msgs WHERE invite_id=?", (iid,))
        c.execute("DELETE FROM masq_invites WHERE id=?", (iid,))


def delete_dance_msg(mid: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE masq_dance_msgs SET deleted=1 WHERE id=?", (mid,))


def add_sched(at_ms: int, text: str) -> str:
    text = (text or "").strip()
    if not text or len(text) > NARR_MAX:
        return f"旁白要写 1~{NARR_MAX} 个字"
    if at_ms <= 0:
        return "选一个发送时间"
    with get_conn() as c:
        c.execute("INSERT INTO masq_sched(at, text) VALUES(?,?)", (at_ms, text))
    return ""


def del_sched(sid: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM masq_sched WHERE id=?", (sid,))
