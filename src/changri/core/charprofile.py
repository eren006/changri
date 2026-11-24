"""角色档案：性别、年龄、皮相、签名、简称（照长日系统 / 加百列的写法）。

- 玩家在自己群里发「修改性别 男」「修改年龄 20」「修改皮相 明星名」「修改签名 …」「修改简称 …」，
  可以一次发多行一起改（每行一条）。
- 皮相没自己填过时按性别给默认值（男 亨利卡维尔 / 女 刘亦菲），改了性别默认皮相跟着换。
- 签名 12 小时只能改一次，皮相 2 小时只能改一次。
- 「修改名字 新名字」改本名（跟别人的本名、简称都不能重复）；房间、短信这些都按角色编号记，改名不影响；
  点歌是按名字记的，改名 / 改简称时点给旧名字的歌跟着改到新名字上。本名 24 小时只能改一次（第一次不限）。
- 简称：数字、英文字母、汉字，1–10 个字，跟所有人的本名、简称都不能重复；短信、约会这些要填名字的地方都能用它代替本名
  （roles.get_role_by_name 找不到本名时按简称再找一遍，所以各个指令不用单独适配）。填回自己的本名 = 清除简称。
和小手机里的「微信名」是两回事：微信名只影响显示，简称是填指令用的。
"""
import re
import time

from .db import get_conn

PREFIXES = ("修改名字", "修改性别", "修改年龄", "修改皮相", "修改签名", "修改简称")
DEFAULT_LOOKS = {"男": "亨利卡维尔", "女": "刘亦菲"}
BIO_COOLDOWN = 12 * 3600
NAME_COOLDOWN = 24 * 3600          # 本名 24 小时只能改一次（第一次改不限，方便建完角色马上改错字）；管理员改不受限
LOOK_COOLDOWN = 2 * 3600
LOOK_MAX = 20
BIO_MAX = 100
SHORT_RE = re.compile(r"[0-9A-Za-z一-龥]{1,10}")


def get(role_id: int) -> dict:
    """{gender, age, look, bio, bio_ts, short}，没存过就是默认值。look 没自己填过时按性别给默认皮相。"""
    with get_conn() as c:
        r = c.execute("SELECT * FROM role_profiles WHERE role_id=?", (role_id,)).fetchone()
    p = dict(r) if r else {"role_id": role_id, "gender": "女", "age": 18, "look": "", "look_ts": 0, "bio": "", "bio_ts": 0, "short": ""}
    p["look"] = p["look"] or DEFAULT_LOOKS.get(p["gender"], "")
    return p


def _save(role_id: int, **patch) -> None:
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO role_profiles(role_id) VALUES(?)", (role_id,))
        c.execute(f"UPDATE role_profiles SET {', '.join(k + '=?' for k in patch)} WHERE role_id=?", (*patch.values(), role_id))


def short_owner(short: str):
    """这个简称是谁的（roles 行），没有返回 None。"""
    if not short:
        return None
    with get_conn() as c:
        return c.execute("SELECT r.* FROM role_profiles p JOIN roles r ON r.id=p.role_id WHERE p.short=?", (short,)).fetchone()


def name_taken(name: str, except_id: int = 0) -> bool:
    """本名或简称里有没有别人在用这个名字。"""
    with get_conn() as c:
        return bool(c.execute("SELECT 1 FROM roles WHERE name=? AND id!=?", (name, except_id)).fetchone()
                    or c.execute("SELECT 1 FROM role_profiles WHERE short=? AND role_id!=?", (name, except_id)).fetchone())


def set_short(role, val: str) -> str:
    if not val:
        return "❌ 请输入简称，例：修改简称 小明"
    old = get(role["id"]).get("short") or ""
    if val == role["name"]:
        _save(role["id"], short="")
        _move_songs(old, role["name"])
        return f"✅ 简称已清除，填名字时用本名「{role['name']}」。"
    if not SHORT_RE.fullmatch(val):
        return "❌ 简称只能是数字、英文字母、汉字，长度 1–10。"
    from . import roles
    why = roles.name_problem(val, "简称")
    if why:
        return f"❌ {why}"
    if name_taken(val, role["id"]):
        return f"❌ 「{val}」已经是别人的名字或简称了，换一个。"
    _save(role["id"], short=val)
    _move_songs(old, val)
    return f"✅ 简称已设为「{val}」，以后短信、约会等填名字的地方都能用它代替「{role['name']}」。"


def _move_songs(old: str, new: str) -> None:
    """点歌按名字记（song_requests.to_name）：改名 / 改简称时，点给旧名字的歌挪到新名字上，不然点歌台里就没了。"""
    if old and new and old != new:
        with get_conn() as c:
            c.execute("UPDATE song_requests SET to_name=? WHERE to_name=?", (new, old))


def rename(role, val: str, now: int | None = None, admin: bool = False) -> str:
    from . import roles
    if not val:
        return "❌ 请输入新名字，例：修改名字 林晚"
    if val == role["name"]:
        return "❌ 新名字和现在的一样。"
    why = roles.name_problem(val)
    if why:
        return f"❌ {why}"
    if name_taken(val, role["id"]):
        return f"❌ 「{val}」已经是别人的名字或简称了，换一个。"
    now = now or int(time.time())
    last = get(role["id"]).get("name_ts") or 0
    if last and now - last < NAME_COOLDOWN and not admin:
        return f"⏳ 名字 24 小时只能改一次，还要等 {-(-(NAME_COOLDOWN - (now - last)) // 3600)} 小时。"
    with get_conn() as c:
        c.execute("UPDATE roles SET name=? WHERE id=?", (val, role["id"]))
        c.execute("UPDATE role_profiles SET short='' WHERE role_id=? AND short=?", (role["id"], val))   # 改成自己的简称：简称就不用了
    if not admin:
        _save(role["id"], name_ts=now)
    _move_songs(role["name"], val)
    return f"✅ 名字已从「{role['name']}」改为「{val}」。"


def edit_line(role, line: str, now: int | None = None, admin: bool = False) -> str:
    """处理一行「修改xx 值」，返回回执。"""
    line = line.strip()
    key, val = line[:4], line[4:].strip()
    if key == "修改名字":
        return rename(role, val, now, admin)
    if key == "修改性别":
        if val not in ("男", "女"):
            return "❌ 性别只能填：男 / 女"
        _save(role["id"], gender=val)
        return f"✅ 性别已改为：{val}"
    if key == "修改年龄":
        if not re.fullmatch(r"\d{1,5}", val) or int(val) > 10000:
            return "❌ 年龄要填 0–10000 的整数，例：修改年龄 20"
        _save(role["id"], age=int(val))
        return f"✅ 年龄已改为：{int(val)}"
    if key == "修改皮相":
        if not val:
            return "❌ 请输入明星名，例：修改皮相 刘亦菲"
        if len(val) > LOOK_MAX or "\n" in val:
            return f"❌ 皮相最多 {LOOK_MAX} 个字。"
        now = now or int(time.time())
        last = get(role["id"]).get("look_ts") or 0
        if last and now - last < LOOK_COOLDOWN and not admin:
            return f"⏳ 皮相 2 小时只能改一次，还要等 {-(-(LOOK_COOLDOWN - (now - last)) // 60)} 分钟。"
        _save(role["id"], look=val, look_ts=now)
        return f"✅ 皮相已改为：{val}"
    if key == "修改签名":
        if not val:
            return "❌ 请输入签名内容，例：修改签名 愿岁月温柔以待"
        if len(val) > BIO_MAX:
            return f"❌ 签名最多 {BIO_MAX} 个字。"
        now = now or int(time.time())
        last = get(role["id"])["bio_ts"]
        if last and now - last < BIO_COOLDOWN and not admin:
            return f"⏳ 签名 12 小时只能改一次，还要等 {-(-(BIO_COOLDOWN - (now - last)) // 60)} 分钟。"
        _save(role["id"], bio=val, bio_ts=now)
        return f"✅ 签名已改为：{val}"
    if key == "修改简称":
        return set_short(role, val)
    return f"❌ 认不出这一行：{line}"


def edit(role, text: str, admin: bool = False) -> str:
    """整条消息：一行一条，每行都要以「修改性别/年龄/皮相/签名/简称」开头。签名只能单独一行写，不跨行。"""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) > 1 and not all(x.startswith(PREFIXES) for x in lines):
        return "❌ 一次改多项时，每一行都要以「修改名字 / 修改性别 / 修改年龄 / 修改皮相 / 修改签名 / 修改简称」开头。"
    from . import roles
    out = []
    for x in lines:
        out.append(edit_line(role, x, admin=admin))
        role = roles.get_role(role["id"]) or role          # 同一条里先改了名字，后面几行按新名字算
    return "\n".join(out)


def summary_lines(role) -> list[str]:
    """给「我的角色」「创建回执」看的档案几行。"""
    p = get(role["id"])
    out = [f"👤 性别：{p['gender']}　年龄：{p['age']}", f"🌸 皮相：{p['look']}"]
    if p["short"]:
        out.append(f"🏷 简称：{p['short']}")
    if p["bio"]:
        out.append(f"✏️ 签名：{p['bio']}")
    return out


HELP = ("💡 可以发这些修改档案（可以一次发多行一起改，每行一条）：\n"
        "  修改名字 新名字（24 小时能改一次）\n"
        "  修改性别 男/女\n"
        "  修改年龄 数字\n"
        "  修改皮相 明星名（2 小时能改一次）\n"
        "  修改签名 你的签名（12 小时能改一次）\n"
        "  修改简称 简称（名字长的话，短信、约会等填名字的地方都能用简称代替本名）\n"
        "例：\n  修改年龄 20\n  修改简称 阿明")


def _all() -> list[dict]:
    """所有角色 + 档案 + 有没有交二表，按建角色先后。"""
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT r.id, r.name, r.is_npc, EXISTS(SELECT 1 FROM sheets s WHERE s.role_id=r.id AND s.kind='erbiao') AS erbiao "
            "FROM roles r ORDER BY r.id")]
    for r in rows:
        r.update({k: v for k, v in get(r["id"]).items() if k != "role_id"})
    return rows


def look_wall() -> str:
    """皮相墙：男生 / 女生 / NPC 三栏，嘉宾行首标二表交没交。（官方机器人发不了群公告，所以做成「查看皮相墙」指令）"""
    rows = _all()
    if not rows:
        return "还没有角色。"
    col = {"男": [], "女": []}
    npcs = []
    for r in rows:
        if r["is_npc"]:
            npcs.append(f"{r['name']}｜{r['look']}")
        else:
            col.setdefault(r["gender"], []).append(f"{'✅' if r['erbiao'] else '⬜'} {r['name']}｜{r['look']}")
    out = ["🌸 皮相墙 🌸", "", "男生", *(col["男"] or ["（暂无）"]), "", "女生", *(col["女"] or ["（暂无）"])]
    if npcs:
        out += ["", "NPC", *npcs]
    return "\n".join(out + ["", "✅ 已提交二表　⬜ 尚未提交"])


def roster() -> str:
    """玩家名单：嘉宾和 NPC 分两栏，每人一段（有简称的先写简称）。"""
    rows = _all()
    if not rows:
        return "还没有角色。"
    parts = {0: [], 1: []}
    for r in rows:
        lst = parts[1 if r["is_npc"] else 0]
        head = f"{len(lst) + 1}. " + (f"{r['short']}（全名：{r['name']}）" if r["short"] else r["name"])
        lines = [head, f"    {r['gender']} / {r['age']}岁 / 皮相：{r['look']}"]
        if r["bio"]:
            lines.append(f"    签名：{r['bio']}")
        lst.append("\n".join(lines))
    out = [f"👥 玩家名单（{len(parts[0])} 人）", *(parts[0] or ["（暂无）"])]
    if parts[1]:
        out += [f"🎭 NPC（{len(parts[1])} 人）", *parts[1]]
    return "\n\n".join(out)


BTN_NAME_MAX = 3


def btn_name(name: str, short: str = "") -> str:
    """按钮上放人名：有简称用简称，没有用名字，最多前 3 个字（作者定，2026-11-23：4 个字一行放 4 个时手机上会被截成「菲尼克…」）。点了发的指令里照样用全名。"""
    return (short or name or "")[:BTN_NAME_MAX]


def btn_name_of(name: str) -> str:
    """只知道全名时：查这个人的简称再套 btn_name。找不到这个人就按名字截。"""
    from . import roles
    r = roles.get_role_by_name(name)
    return btn_name(name, get(r["id"])["short"] if r else "")


def roster_targets(exclude_id: int | None = None) -> list[tuple[str, str]]:
    """「玩家名单」下面一人一个按钮用：(按钮上的字, 全名)。嘉宾在前、NPC 在后，不含自己。"""
    rows = [r for r in _all() if r["id"] != exclude_id]
    rows.sort(key=lambda r: bool(r["is_npc"]))
    return [(btn_name(r["name"], r["short"]), r["name"]) for r in rows]
