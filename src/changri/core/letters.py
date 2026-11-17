"""写信综（个性化模块，默认关）：正式信件 + 写信币赏金 + 望远镜 / 羽毛笔。照搬老长日系统的「长日写信综」。
部分规则改编自「长日将尽」CC BY-NC-SA 作品。

- 发送信件：【收件人】【内容】必填，【日期】【附件】【署名】选填（署名默认是自己的角色名）。每天封数、冷却、
  赏金（每封给几个写信币）、拿赏金的最低字数在设置「写信综」里。「今天」按季度天数算（season.day_key）。
- 望远镜：用在某人身上，TA 寄出的下一封信抄一份给你。羽毛笔：用在某人身上，TA 的下一封信先截到你手里，
  你「羽毛笔修改 序号 新内容」改完才发出去（寄给你自己的信不截）。两种都有时，先用的先生效。
- 写信币、望远镜、羽毛笔第一次用到时自动在物品库里建好（ensure_items）。"""
import re
import time

from . import rpg, season
from .db import get_conn, get_int

COIN, TELESCOPE, QUILL = "写信币", "望远镜", "羽毛笔"
TAG_RE = r"【{tag}】([\s\S]*?)(?=【|$)"


def ensure_items() -> None:
    """写信币 + 望远镜、羽毛笔（后两个是系统自带的特殊道具，core/specials.py；被删了这里补回来）。"""
    if not rpg.find_item(COIN):
        rpg.save_item("", COIN, "currency", "写信综：寄出够字数的信得到的赏金。")
    if not (rpg.find_item(TELESCOPE) and rpg.find_item(QUILL)):
        from . import specials
        specials.ensure(force=True)


def settings() -> dict:
    return {"limit": get_int("wl_daily_limit", 5), "reward": get_int("wl_reward", 0), "min_chars": get_int("wl_min_chars", 0),
            "cooldown": get_int("wl_cooldown_min", 0)}


def parse(text: str) -> dict:
    def tag(*names):
        for n in names:
            m = re.search(TAG_RE.format(tag=n), text)
            if m:
                return m.group(1).strip()
        return ""
    return {"to": tag("收件人", "发送对象"), "content": tag("内容"), "date": tag("日期"), "attachment": tag("附件"), "sign": tag("署名")}


TEMPLATE = "发送信件\n【收件人】小明\n【内容】亲爱的小明，今天天气真好……\n【日期】2026年4月28日（选填）\n【附件】随信附上一片银杏叶（选填）\n【署名】小红（选填）"


def day_count(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM letters WHERE from_id=? AND day=?", (role_id, season.day_key())).fetchone()[0]


def cooldown_left(role_id: int) -> int:
    cd = settings()["cooldown"] * 60
    if cd <= 0:
        return 0
    with get_conn() as c:
        last = c.execute("SELECT MAX(ts) FROM letters WHERE from_id=?", (role_id,)).fetchone()[0] or 0
    return max(0, last + cd - int(time.time()))


def render(to_name: str, content: str, date_tag: str, attachment: str, sign: str, note: str = "") -> str:
    """信的样子。抬头「寄给 某人 的信」会让出图时画成信纸。"""
    out = f"✉️ 寄给 {to_name} 的信{note}\n"
    if date_tag:
        out += f"📅 {date_tag}\n"
    out += f"\n「{content}」\n\n—— {sign}"
    if attachment:
        out += f"\n\n附件：\n--------------------\n{attachment}"
    return out


def _first_effect(c, target_id: int):
    return c.execute("SELECT * FROM letter_effects WHERE target_id=? ORDER BY ts, id LIMIT 1", (target_id,)).fetchone()


def compose(me, to, p: dict) -> tuple[bool, str, dict]:
    """检查 + 记下一封信。返回 (成功, 给发件人的话, 要发的东西)。
    要发的东西：{"deliver": (收件人id, 文字) or None, "copy": (望远镜的人id, 文字) or None, "held": (羽毛笔的人id, 文字) or None}"""
    ensure_items()
    s = settings()
    if not p["content"]:
        return False, "信件内容不能为空。\n" + TEMPLATE, {}
    if to["id"] == me["id"]:
        return False, "不能寄给自己。", {}
    used = day_count(me["id"])
    if s["limit"] > 0 and used >= s["limit"]:
        return False, f"📪 今天已经寄了 {used}/{s['limit']} 封信。", {}
    left = cooldown_left(me["id"])
    if left:
        return False, f"⏳ 寄信冷却中，还要等 {(left + 59) // 60} 分钟。", {}
    sign = p["sign"][:20] or me["name"]
    text = render(to["name"], p["content"], p["date"], p["attachment"], sign)
    out = {"deliver": None, "copy": None, "held": None}
    now = int(time.time())
    with get_conn() as c:
        eff = _first_effect(c, me["id"])
        status, held_by = "sent", None
        if eff and eff["kind"] == "quill" and eff["applier_id"] != to["id"]:
            status, held_by = "held", eff["applier_id"]
        lid = c.execute("INSERT INTO letters(from_id,to_id,content,date_tag,attachment,signature,day,status,held_by,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (me["id"], to["id"], p["content"], p["date"], p["attachment"], sign, season.day_key(), status, held_by, now)).lastrowid
        if eff:
            c.execute("DELETE FROM letter_effects WHERE id=?", (eff["id"],))
        if status == "held":
            idx = c.execute("SELECT COUNT(*) FROM letters WHERE status='held' AND held_by=? AND id<=?", (held_by, lid)).fetchone()[0]
            out["held"] = (held_by, f"✏️【羽毛笔截获】第 {idx} 封：{me['name']} 寄给 {to['name']}\n{text}\n\n发「羽毛笔修改 {idx} 新内容」改好后才会送出去。")
        else:
            out["deliver"] = (to["id"], text)
            if eff and eff["kind"] == "telescope":
                out["copy"] = (eff["applier_id"], f"🔭【望远镜抄录】{me['name']} 寄出了一封信：\n{text}")
    reply = f"✉️ 信件已寄给「{to['name']}」。\n🖋️ 落款：{sign}\n📅 {season.day_label() if season.running() else '今天'}（今天还能寄 "
    reply += (f"{s['limit'] - used - 1}/{s['limit']} 封）" if s["limit"] > 0 else "不限）")
    chars = len(re.sub(r"\s", "", p["content"]))
    if s["reward"] > 0:
        if chars >= s["min_chars"]:
            ok, _ = rpg.adjust(me["id"], COIN, s["reward"], f"写信综赏金 L{lid}")
            reply += f"\n💰 写信币 +{s['reward']}" if ok else "\n⚠️ 写信币没发出去，请管理员看看物品库里有没有「写信币」。"
        else:
            reply += f"\n📝 字数不到 {s['min_chars']}，这封没有赏金。"
    return True, reply, out


def apply_item(me, target, kind: str) -> tuple[bool, str]:
    """使用望远镜 / 羽毛笔：从背包扣一个，记下效果。"""
    ensure_items()
    name = TELESCOPE if kind == "telescope" else QUILL
    if target["id"] == me["id"]:
        return False, f"{name}不能用在自己身上。"
    ok, why = rpg.adjust(me["id"], name, -1, f"写信综：用在 {target['name']} 身上")
    if not ok:
        return False, f"你的背包里没有「{name}」。" if "不够" in why or "没有" in why else why
    with get_conn() as c:
        c.execute("INSERT INTO letter_effects(kind,target_id,applier_id,ts) VALUES(?,?,?,?)", (kind, target["id"], me["id"], int(time.time())))
    return True, (f"🔭 望远镜对准了 {target['name']}：TA 寄出的下一封信会抄一份给你。" if kind == "telescope"
                  else f"🪶 羽毛笔悄悄伸向了 {target['name']}：TA 的下一封信会先送到你手里，改完才寄出去。")


def held(role_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT l.*, f.name AS from_name, t.name AS to_name FROM letters l JOIN roles f ON f.id=l.from_id JOIN roles t ON t.id=l.to_id "
                         "WHERE l.status='held' AND l.held_by=? ORDER BY l.id", (role_id,)).fetchall()


def quill_edit(role_id: int, idx: int, new_content: str) -> tuple[bool, str, tuple | None]:
    """改好截下的第 idx 封信并放行。返回 (成功, 回复, (收件人id, 信的文字))。"""
    rows = held(role_id)
    if not 1 <= idx <= len(rows):
        return False, f"序号不对，现在有 {len(rows)} 封待修改。" if rows else "没有待修改的信。", None
    new_content = new_content.strip()
    if not new_content:
        return False, "要写改好的内容：羽毛笔修改 序号 新内容", None
    r = rows[idx - 1]
    with get_conn() as c:
        c.execute("UPDATE letters SET content=?, status='sent', edited=1 WHERE id=?", (new_content, r["id"]))
    return True, f"✏️ 已改好，寄给 {r['to_name']} 的信放行了。", (r["to_id"], render(r["to_name"], new_content, r["date_tag"], r["attachment"], r["signature"]))


def held_list(role_id: int) -> str:
    rows = held(role_id)
    if not rows:
        return "✏️ 没有待修改的信。"
    return f"✏️ 待修改的信（{len(rows)} 封）：\n" + "\n".join(
        f"{i}. {r['from_name']} → {r['to_name']}：{r['content'][:20]}{'…' if len(r['content']) > 20 else ''}" for i, r in enumerate(rows, 1)
    ) + "\n\n发「羽毛笔修改 序号 新内容」改好后放行。"


def status(role_id: int) -> str:
    s = settings()
    used = day_count(role_id)
    left = cooldown_left(role_id)
    lines = ["📊 写信综", f"📅 {season.day_label() if season.running() else '今天'}",
             f"├ 每天能寄：{s['limit'] if s['limit'] > 0 else '不限'}" + (f"（今天已寄 {used}，还剩 {max(0, s['limit'] - used)}）" if s["limit"] > 0 else f"（今天已寄 {used}）"),
             f"├ 写信币赏金：{str(s['reward']) + ' 个/封' if s['reward'] > 0 else '不发'}" + (f"（至少 {s['min_chars']} 字）" if s["reward"] > 0 and s["min_chars"] else "")]
    lines.append(f"└ 寄信冷却：{s['cooldown']} 分钟（{'还要等 ' + str((left + 59) // 60) + ' 分钟' if left else '可以寄'}）" if s["cooldown"] > 0 else "└ 寄信冷却：无")
    n = len(held(role_id))
    if n:
        lines.append(f"✏️ 你截下了 {n} 封信待修改，发「羽毛笔修改」看看。")
    return "\n".join(lines)


GUIDE = ("📖 写信综\n"
         "① 寄信：在自己的个人群发\n" + TEMPLATE + "\n"
         "② 看额度：信件状态\n"
         "③ 道具（物品库里的「望远镜」「羽毛笔」，可以在商城上架或由管理员发放）：\n"
         "· 使用望远镜 角色名：TA 寄出的下一封信抄一份给你\n"
         "· 使用羽毛笔 角色名：TA 的下一封信先送到你手里，「羽毛笔修改 序号 新内容」改完才寄出去；只发「羽毛笔修改」看清单\n"
         "④ 写信币：管理员可以设成挂心愿、发起约会要花写信币，寄信赚的写信币就有用处了。\n"
         "管理员在后台「功能与设置 → 写信综」调每天封数、冷却、写信币赏金、最低字数和心愿 / 约会的花费。")


# ---------- 花写信币才能挂心愿、发起约会（开着写信综、设置里填了数才收） ----------

COST_KEY = {"wish": ("wl_wish_cost", "挂心愿"), "date": ("wl_date_cost", "发起约会")}


def cost(kind: str) -> int:
    from . import extensions
    if not extensions.is_enabled("letters"):
        return 0
    return max(0, get_int(COST_KEY[kind][0], 0))


def coins(role_id: int) -> int:
    return sum(x["count"] for x in rpg.inventory(role_id) if x["name"] == COIN)


def check_cost(role_id: int, kind: str) -> str:
    """够付返回空串，不够返回原因（还没扣）。"""
    c = cost(kind)
    if c <= 0:
        return ""
    have = coins(role_id)
    return "" if have >= c else f"{COST_KEY[kind][1]}要花 {c} 个写信币，你只有 {have} 个（寄信可以赚写信币）。"


def paid_note(role_id: int, kind: str) -> str:
    """刚付过之后给玩家看的一句（没收就是空串）。心愿是在 wishes.post 里扣的，回复在别处拼，用这个补提示。"""
    c = cost(kind)
    return f"（花了 {c} 个写信币，还剩 {coins(role_id)} 个）" if c > 0 else ""


def pay(role_id: int, kind: str, detail: str = "") -> str:
    """真正扣掉。返回给玩家看的一句（没收就是空串）。"""
    c = cost(kind)
    if c <= 0:
        return ""
    ok, _ = rpg.adjust(role_id, COIN, -c, detail or COST_KEY[kind][1])
    return f"（花了 {c} 个写信币，还剩 {coins(role_id)} 个）" if ok else ""
