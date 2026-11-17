"""特殊道具（系统自带，默认就在物品库里，谁背包里有谁就能用）：照搬老长日系统长日RPG.js 的 SPEC_001–009。
部分规则改编自「长日将尽」CC BY-NC-SA 作品。

玩家发「使用 道具名 目标」（plugins/rpg.py 的「使用」遇到特殊道具会转到这里）：
- 追踪器 角色名 [钟点]：查 TA 今天这个钟点（不写 = 现在）在哪场约会，地点、和谁（设置可关）。有成功率，失败也算用掉。
- 万能钥匙 地点名：直接拿到这个地点的钥匙（地点系统里上锁的地方）。
- 望远镜 / 羽毛笔 角色名：写信综的道具，要开着「写信综」（core/letters.py）。
- 捕鼠器 角色名 钟点：TA 今天这个钟点的一小时被锁住，不能约会、打电话（记成上帝模式的时间锁定）。会告诉 TA。
- 窃听器 角色名：TA 之后在电话房间里说的话，抄给你（条数、干扰率在设置里）。
- 截信器 角色名：TA 之后发出的短信抄给你。回音壁 角色名：TA 之后收到的短信抄给你。
- 拆信刀 角色名：偷看 TA 信箱里的一封心动信（已收到的或今晚待派送的），对方不知道；信箱空的不扣道具。
抄录先写进 tap_outbox，plugins/play.py 的定时任务每 30 秒发一次（记录发言的地方是同步的，发不了消息）。"""
import random
import re
import time

from . import roles, rpg, season
from .db import get_config, get_conn, get_int, set_config

SPECIALS = [
    ("SPEC_001", "追踪器", "一枚微型追踪器：使用 追踪器 角色名 [钟点]，看 TA 那时在哪场约会、在哪、和谁。"),
    ("SPEC_002", "万能钥匙", "能打开任何一扇锁住的门：使用 万能钥匙 地点名，直接拿到那个地点的钥匙。"),
    ("SPEC_003", "望远镜", "写信综：使用 望远镜 角色名，TA 寄出的下一封信抄一份给你。"),
    ("SPEC_004", "羽毛笔", "写信综：使用 羽毛笔 角色名，TA 的下一封信先送到你手里，改完才寄出去。"),
    ("SPEC_005", "捕鼠器", "使用 捕鼠器 角色名 钟点（如 14）：TA 今天这一小时不能约会、打电话。"),
    ("SPEC_006", "窃听器", "使用 窃听器 角色名：截听 TA 之后在电话里说的话，信号偶尔有干扰。"),
    ("SPEC_007", "截信器", "使用 截信器 角色名：截下 TA 之后发出的短信，内容偶尔失真。"),
    ("SPEC_008", "回音壁", "使用 回音壁 角色名：TA 之后收到的短信，你也能看到。"),
    ("SPEC_009", "拆信刀", "使用 拆信刀 角色名：悄悄拆开 TA 信箱里的一封心动信看一眼，对方不会察觉。"),
]
CODES = {code for code, _, _ in SPECIALS}
rng = random.Random()


def ensure(force: bool = False) -> list[str]:
    """把特殊道具建进物品库。只自动建一次（之后管理员删了就是删了）；force 时缺的补回来。"""
    if not force and get_config("specials_seeded", "") == "1":
        return []
    set_config("specials_seeded", "1")          # 先记上：下面 find_item 会再进 seed_defaults → ensure，别绕圈
    made = []
    for code, name, desc in SPECIALS:
        if not rpg.find_item(code) and not rpg.find_item(name):
            if rpg.save_item(code, name, "item", desc)[0]:
                made.append(name)
    return made


def is_special(item) -> bool:
    return bool(item) and item["code"] in CODES


def _take(me_id: int, code: str, name: str) -> str:
    ok, why = rpg.adjust(me_id, code, -1, f"使用{name}")
    return "" if ok else f"你的背包里没有可用的「{name}」。"


def _has(me_id: int, code: str) -> bool:
    return any(x["item_code"] == code and x["count"] > 0 for x in rpg.inventory(me_id))


def _target(name: str, me) -> tuple[dict | None, str]:
    if not name:
        return None, "要写对谁用：使用 道具名 角色名"
    t = roles.get_role_by_name(name)
    if not t:
        return None, f"找不到角色「{name}」。"
    if t["id"] == me["id"]:
        return None, "不能对自己用。"
    return t, ""


def blur(text: str, pct: int) -> str:
    """按干扰率决定这条有没有被干扰；被干扰的大约三成字看不清。"""
    if pct <= 0 or rng.random() * 100 >= pct:
        return text
    return "".join("▒" if (not ch.isspace() and rng.random() < 0.3) else ch for ch in text) + "（信号有干扰）"


def use(me, item, args: list[str]) -> tuple[bool, str]:
    """用一个特殊道具。返回 (成功, 回复)。没成功的不扣道具（追踪器定位失败除外：照旧系统，算用掉）。"""
    code, name = item["code"], item["name"]
    if not _has(me["id"], code):
        return False, f"你的背包里没有可用的「{name}」。"
    first = args[0] if args else ""
    if code == "SPEC_002":
        return _key(me, " ".join(args).strip())
    if code in ("SPEC_003", "SPEC_004"):
        from . import extensions, letters
        if not extensions.is_enabled("letters"):
            return False, f"「{name}」是写信综的道具，要管理员先打开「写信综」。"
        t, why = _target(first, me)
        return (False, why) if why else letters.apply_item(me, t, "telescope" if code == "SPEC_003" else "quill")
    t, why = _target(first, me)
    if why:
        return False, why
    if code == "SPEC_001":
        return _track(me, t, args[1] if len(args) > 1 else "")
    if code == "SPEC_005":
        return _mousetrap(me, t, args[1] if len(args) > 1 else "")
    if code in ("SPEC_006", "SPEC_007", "SPEC_008"):
        return _tap(me, t, code, name)
    if code == "SPEC_009":
        return _letter_knife(me, t)
    return False, f"「{name}」还不能用。"


def _hour(text: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2})(?::?\d{2})?", text.strip())
    return int(m[1]) if m and 0 <= int(m[1]) <= 23 else None


def _track(me, t, when: str) -> tuple[bool, str]:
    day = season.current_day()
    if day is None:
        return False, "现在不在游戏天数里（没有进行中的季度，或者还在筹备期），追踪器用不了。"
    h = time.localtime().tm_hour if not when else _hour(when)
    if h is None:
        return False, "钟点写成 14 或 14:30。"
    a, b = f"{h:02d}:00", ("23:59" if h == 23 else f"{h + 1:02d}:00")
    why = _take(me["id"], "SPEC_001", "追踪器")
    if why:
        return False, why
    with get_conn() as c:
        room = c.execute("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.day=? AND r.status!='cancelled' "
                         "AND r.start_hm<? AND ? < r.end_hm ORDER BY r.start_hm LIMIT 1", (t["id"], day, b, a)).fetchone()
        mates = [x["name"] for x in c.execute("SELECT ro.name FROM room_members m JOIN roles ro ON ro.id=m.role_id WHERE m.room_id=? AND m.role_id!=?",
                                              (room["id"], t["id"]))] if room else []
    if not room:
        return True, f"🔍 没发现「{t['name']}」在 D{day} {a}-{b} 的行踪。（追踪器已用掉）"
    if rng.random() * 100 >= get_int("item_tracker_success_rate", 70):
        return True, "🔍 信号干扰，定位失败。（追踪器已用掉）"
    text = f"🔍 追踪到「{t['name']}」在 D{day} {room['start_hm']}-{room['end_hm']} 出现在「{room['place'] or '某处'}」"
    if mates and get_config("item_tracker_show_partner", "1") == "1":
        text += f"，和 {'、'.join(mates)} 在一起"
    return True, text + "。（追踪器已用掉）"


def _key(me, place: str) -> tuple[bool, str]:
    if not place:
        return False, "要写哪个地点：使用 万能钥匙 地点名"
    p = next((x for x in rpg.places() if x["name"] == place), None)
    if not p:
        names = "、".join(x["name"] for x in rpg.places()) or "（还没有地点）"
        return False, f"没有叫「{place}」的地点。现有地点：{names}"
    if any(k["id"] == p["id"] for k in rpg.keys(me["id"])):
        return False, f"你已经有「{place}」的钥匙了。"
    why = _take(me["id"], "SPEC_002", "万能钥匙")
    if why:
        return False, why
    rpg.set_key(me["id"], p["id"], True)
    return True, f"🔑 拿到了「{place}」的钥匙！（万能钥匙已用掉）"


def _mousetrap(me, t, when: str) -> tuple[bool, str]:
    day = season.current_day()
    if day is None:
        return False, "现在不在游戏天数里，捕鼠器用不了。"
    h = _hour(when)
    if h is None:
        return False, "要写锁哪个钟点：使用 捕鼠器 角色名 14"
    why = _take(me["id"], "SPEC_005", "捕鼠器")
    if why:
        return False, why
    a, b = f"{h:02d}:00", ("23:59" if h == 23 else f"{h + 1:02d}:00")
    with get_conn() as c:
        c.execute("INSERT INTO time_locks(role_id, day, start_hm, end_hm, note, ts) VALUES(?,?,?,?,?,?)", (t["id"], day, a, b, "捕鼠器", int(time.time())))
        c.execute("INSERT INTO tap_outbox(owner_id, text, ts) VALUES(?,?,?)",
                  (t["id"], f"🪤 你在 D{day} {a}-{b} 踩中了捕鼠器，这段时间不能约会、打电话。", int(time.time())))
    return True, f"🪤 捕鼠器放好了：「{t['name']}」在 D{day} {a}-{b} 动弹不得。（捕鼠器已用掉）"


TAP_KIND = {"SPEC_006": "phone", "SPEC_007": "sms_out", "SPEC_008": "sms_in"}
TAP_WORD = {"phone": ("📡", "截听", "电话"), "sms_out": ("📱", "截下", "发出的短信"), "sms_in": ("🪞", "看到", "收到的短信")}


def _tap(me, t, code: str, name: str) -> tuple[bool, str]:
    why = _take(me["id"], code, name)
    if why:
        return False, why
    n = max(1, get_int("tap_count", 10))
    pct = min(100, max(0, get_int("tap_blur", 30)))
    kind = TAP_KIND[code]
    with get_conn() as c:
        c.execute("DELETE FROM item_taps WHERE kind=? AND target_id=? AND owner_id=?", (kind, t["id"], me["id"]))
        c.execute("INSERT INTO item_taps(kind, target_id, owner_id, remain, blur, ts) VALUES(?,?,?,?,?,?)", (kind, t["id"], me["id"], n, pct, int(time.time())))
    icon, verb, what = TAP_WORD[kind]
    return True, f"{icon} {name}装好了：之后会{verb}「{t['name']}」{what}，最多 {n} 条，干扰率 {pct}%。（{name}已用掉）"


def _letter_knife(me, t) -> tuple[bool, str]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM lovemails WHERE to_role_id=?", (t["id"],)).fetchall()
    if not rows:
        return False, f"📭 「{t['name']}」的信箱里还没有心动信，拆信刀先留着吧。（没用掉）"
    why = _take(me["id"], "SPEC_009", "拆信刀")
    if why:
        return False, why
    m = rng.choice(rows)
    text = (m["content"] or "").strip() or "（只有图片）"
    if len(text) > 220:
        text = text[:220] + "……（后面看不清了）"
    when = "今晚才会收到" if m["status"] != "sent" else "收到"
    return True, (f"✉️ 你用拆信刀悄悄拆开了「{t['name']}」{when}的一封心动信：\n「{text}」\n📝 署名：{m['signature'] or '（无）'}\n"
                  "信已原样封好，对方不会察觉。（拆信刀已用掉）")


# ---------- 抄录：在别处记下内容时调这几个，内容进 tap_outbox ----------

def _fanout(kind: str, target_id: int, make_text) -> None:
    with get_conn() as c:
        taps = c.execute("SELECT * FROM item_taps WHERE kind=? AND target_id=? AND remain>0", (kind, target_id)).fetchall()
        for tp in taps:
            c.execute("INSERT INTO tap_outbox(owner_id, text, ts) VALUES(?,?,?)", (tp["owner_id"], make_text(tp), int(time.time())))
            if tp["remain"] <= 1:
                c.execute("DELETE FROM item_taps WHERE id=?", (tp["id"],))
            else:
                c.execute("UPDATE item_taps SET remain=remain-1 WHERE id=?", (tp["id"],))


def on_room_line(room_id: int, role_id: int | None, name: str, text: str) -> None:
    """房间里有人说话：电话房间里被装了窃听器的人说的，抄给装的人。"""
    if not role_id:
        return
    try:
        with get_conn() as c:
            r = c.execute("SELECT kind FROM rooms WHERE id=?", (room_id,)).fetchone()
        if r and r["kind"] == "电话":
            _fanout("phone", role_id, lambda tp: f"📡【窃听器】{name} 在电话里说：\n{blur(text, tp['blur'])}")
    except Exception:  # noqa: BLE001 抄录出错不能影响记发言
        pass


def on_sms(from_id: int, to_id: int, from_shown: str, to_name: str, content: str) -> None:
    """短信送出去了：发件人身上的截信器、收件人身上的回音壁各抄一份。"""
    if not content.strip():
        return
    try:
        _fanout("sms_out", from_id, lambda tp: f"📱【截信器】{from_shown} 发给 {to_name} 的短信：\n{blur(content, tp['blur'])}")
        _fanout("sms_in", to_id, lambda tp: f"🪞【回音壁】{to_name} 收到 {from_shown} 的短信：\n{blur(content, tp['blur'])}")
    except Exception:  # noqa: BLE001
        pass


def take_outbox(limit: int = 20) -> list:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM tap_outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        if rows:
            c.execute(f"DELETE FROM tap_outbox WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
    return rows


def taps_on(role_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT t.*, r.name AS target_name FROM item_taps t JOIN roles r ON r.id=t.target_id WHERE t.owner_id=? ORDER BY t.id", (role_id,)).fetchall()
