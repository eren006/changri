"""角色与群绑定。不依赖 QQ，只依赖数据库，方便单测。

官方机器人读不到真实群号，只能看到 group_openid（而且每个机器人各一套）。所以：
- 真实群号（real_gid）只是后台里看的标签；
- 群和角色的对应，靠「认群码」：在目标群里发「认群 码」，机器人就拿到这个群的 openid。
"""
import re
import secrets
import sqlite3
import time

from .db import get_conn, get_int

NAME_MAX = 20
PLAYER_CODE_TTL = 600        # 玩家自己生成的码：10 分钟
ADMIN_CODE_TTL = 24 * 3600   # 后台登记群号生成的码：24 小时
GID_RE = re.compile(r"\d{5,12}")


NAME_BAD_CHARS = "/／|｜@＠,，、;；"          # 指令里拿来分隔的符号：发起官约用 / 分名字，心愿用 | 分昵称，@ 是艾特
NAME_RESERVED = {"大家", "所有人", "全体", "全员", "管理员", "有人", "未知号码", "点歌台", "偷盗记录", "群号通知", "小手机", "我", "你", "自己"}


def name_problem(name: str, kind: str = "角色名") -> str:
    """名字（本名、简称）能不能用；能用返回空串，不能用返回原因。不查重名（重名各处自己查）。
    除了长度和空格，还不能带指令里的分隔符、不能叫「大家」这类会跟指令撞的词、不能是「R12」「D3」或纯数字（会被当成房间号、天数）、
    不能和别人正在用的匿名化名重名（落款会被认成你）。"""
    if not _valid_name(name):
        return f"{kind}要在 {NAME_MAX} 字以内，且不能带空格。"
    bad = next((ch for ch in name if ch in NAME_BAD_CHARS), "")
    if bad:
        return f"{kind}里不能有「{bad}」（指令里用它来分隔）。"
    if name in NAME_RESERVED:
        return f"「{name}」会跟指令里的词撞，换一个{kind}。"
    if re.fullmatch(r"[RrDd]?\d+", name):
        return f"「{name}」会被当成房间号或天数，换一个{kind}。"
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM phone_aliases WHERE alias=?", (name,)).fetchone():
            return f"「{name}」是小手机里有人在用的化名，换一个{kind}。"
    return ""


def _valid_name(name: str) -> bool:
    return 0 < len(name) <= NAME_MAX and not re.search(r"\s", name)


ROOM_GROUP_MSG = "这个群是约会用的备用群，不能当个人群。请自己另建一个群，把机器人拉进去再发。"


def create_role(name: str, bot_id: str, group_openid: str, member_openid: str, real_gid: str = "") -> tuple[bool, str]:
    """在某个群里创建角色，这个群自动成为主群（个人群）。real_gid 可不填，只是标签。"""
    why = name_problem(name)
    if why:
        return False, why
    if real_gid and not GID_RE.fullmatch(real_gid):
        return False, "群号要是 5~12 位数字（或者不填）。"
    now = int(time.time())
    from . import pool
    if pool.is_room_group(bot_id, group_openid):
        return False, ROOM_GROUP_MSG
    from .announce import special_label
    sp = special_label(bot_id, group_openid)
    if sp:                                                  # 公告群 / 后台群 / 水群 / 戏群：大家都在，不能当个人群（短信、手机码会发到这里）
        return False, f"这个群是{sp}，不能当个人群。请自己另建一个群，把机器人拉进去再发。"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT r.name FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.group_openid=?",
            (bot_id, group_openid),
        ).fetchone()
        if row:
            return False, f"这个群已经是「{row['name']}」的群了。每个角色要用自己的个人群。"
        row = conn.execute(
            "SELECT r.name FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.member_openid=?",
            (bot_id, member_openid),
        ).fetchone()
        if row:
            return False, f"你已经有角色「{row['name']}」了。"
        if conn.execute("SELECT 1 FROM role_profiles WHERE short=?", (name,)).fetchone():
            return False, f"「{name}」已经是别人的简称了，换一个名字。"
        if conn.execute("SELECT 1 FROM role_former_names WHERE name=?", (name,)).fetchone():
            return False, f"「{name}」是别人以前用过的名字（发给这个名字的短信会送到那个人那里），换一个名字。"
        try:
            cur = conn.execute("INSERT INTO roles(name, member_openid, created_ts) VALUES(?,?,?)", (name, member_openid, now))
        except sqlite3.IntegrityError:
            return False, f"「{name}」已经有人用了，换一个名字。"
        conn.execute(
            "INSERT INTO role_groups(role_id, bot_id, group_openid, real_gid, member_openid, kind, created_ts) VALUES(?,?,?,?,?,'main',?)",
            (cur.lastrowid, bot_id, group_openid, real_gid, member_openid, now),
        )
    return True, f"角色「{name}」创建好了，这个群就是你的个人群。"


def find_role_for_sender(bot_id: str, group_openid: str, member_openid: str):
    """认人：openid 是「每个机器人各一套」，所以一定带上 bot_id。
    先按「这个机器人 + 这个群 + 这个人」找，找不到再按「这个机器人 + 这个人」找（同一个机器人下 openid 是否跨群一致不确定，两种都兼容）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT r.* FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.group_openid=? AND g.member_openid=?",
            (bot_id, group_openid, member_openid),
        ).fetchone()
        if row:
            return row
        return conn.execute(
            "SELECT r.* FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.member_openid=? ORDER BY g.id LIMIT 1",
            (bot_id, member_openid),
        ).fetchone()


def is_own_group(role_id: int, bot_id: str, group_openid: str) -> bool:
    """这个群是不是这个角色自己的个人群。手机码、换绑码只在这里给：在房间群、水群里回出来，群里别人就能拿去登录小手机、抢走角色。"""
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM role_groups WHERE role_id=? AND bot_id=? AND group_openid=?", (role_id, bot_id, group_openid)).fetchone() is not None


def find_group_binding(bot_id: str, group_openid: str):
    """这个群是不是已经绑成了某个角色的个人群。"""
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM role_groups WHERE bot_id=? AND group_openid=?", (bot_id, group_openid)).fetchone()


QQ_TAKEN = "这个 QQ 号已经认在别人名下了。如果真是你的号（比如换了个人群、换了号认），请找管理员在后台「QQ 小号与群号组（双骰）」页里先解绑，再来绑。"


def bind_qq(role_id: int, qq: str, force: bool = False) -> str:
    """把一个真实 QQ 号认到这个角色的主人名下：按「人」认，之后这个人建的新角色也自动算他的；永久保存，后台可看可删。
    返回空串表示成功。"""
    import re
    import time
    qq = qq.strip()
    if not re.fullmatch(r"\d{5,12}", qq):
        return "QQ 号是 5 到 12 位数字"
    with get_conn() as conn:
        row = conn.execute("SELECT member_openid FROM roles WHERE id=?", (role_id,)).fetchone()
        if not row:
            return "没有这个角色"
        if not row["member_openid"]:
            return "这个角色还没有认群，先在它的个人群里发「认群 码」再绑 QQ"
        old = conn.execute("SELECT member_openid FROM person_qq WHERE qq=?", (qq,)).fetchone()
        if old and old["member_openid"] != row["member_openid"] and not force:     # force：管理员在后台改绑      # 不能把别人认过的号改到自己名下（以前会静默覆盖：对方说话就算成你的）
            return QQ_TAKEN
        conn.execute("INSERT INTO person_qq(qq, member_openid, created_ts) VALUES(?,?,?) "
                     "ON CONFLICT(qq) DO UPDATE SET member_openid=excluded.member_openid", (qq, row["member_openid"], int(time.time())))
    return ""


def roles_by_qq(qq: str) -> list:
    """这个 QQ 号对应的人，名下所有角色（包括绑定之后才建的）。"""
    with get_conn() as conn:
        return conn.execute("SELECT r.* FROM person_qq q JOIN roles r ON r.member_openid=q.member_openid WHERE q.qq=? ORDER BY r.id", (str(qq),)).fetchall()


def qq_bindings() -> list[dict]:
    """后台用：所有 QQ 号绑定，带上现在名下的角色名。"""
    with get_conn() as conn:
        out = []
        for q in conn.execute("SELECT * FROM person_qq ORDER BY created_ts DESC"):
            d = dict(q)
            d["roles"] = [r["name"] for r in conn.execute("SELECT name FROM roles WHERE member_openid=? ORDER BY id", (q["member_openid"],))]
            out.append(d)
        return out


def unbind_qq(qq: str) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM person_qq WHERE qq=?", (qq,)).rowcount > 0


def qqs_of_role(role_id: int) -> list[str]:
    """这个角色的主人绑过的 QQ 号。"""
    with get_conn() as conn:
        return [r["qq"] for r in conn.execute("SELECT q.qq FROM person_qq q JOIN roles r ON r.member_openid=q.member_openid WHERE r.id=?", (role_id,))]


def code_role(code: str) -> int | None:
    """认群码对应的角色（过期、不存在返回 None）。"""
    with get_conn() as conn:
        r = conn.execute("SELECT role_id FROM bind_codes WHERE code=? AND expires_ts>=?", (code, int(time.time()))).fetchone()
    return r["role_id"] if r else None


def has_group(role_id: int) -> bool:
    """这个角色有没有认好的个人群。"""
    with get_conn() as conn:
        return bool(conn.execute("SELECT 1 FROM role_groups WHERE role_id=? AND COALESCE(group_openid,'')!=''", (role_id,)).fetchone())


def npc_only_qqs() -> set[str]:
    """绑过 QQ、名下角色全是 NPC 的那些 QQ 号（扮 NPC 的号）。"""
    with get_conn() as conn:
        return {r["qq"] for r in conn.execute(
            "SELECT q.qq FROM person_qq q JOIN roles r ON r.member_openid=q.member_openid GROUP BY q.qq HAVING MIN(r.is_npc)=1")}


def get_role(role_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()


def list_all() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT id, name FROM roles ORDER BY name").fetchall()


def get_role_by_name(name: str):
    """按本名找角色；找不到再按简称找（玩家「修改简称」设的，见 charprofile）。指令里填名字的地方都走这里，简称就处处能用。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()
        if row or not name:
            return row
        row = conn.execute("SELECT r.* FROM role_profiles p JOIN roles r ON r.id=p.role_id WHERE p.short=?", (name,)).fetchone()
        if row:
            return row
        return conn.execute("SELECT r.* FROM role_former_names f JOIN roles r ON r.id=f.role_id WHERE f.name=? ORDER BY f.ts DESC LIMIT 1",
                            (name,)).fetchone()             # 改过名：写旧名字照样找到这个人（旧名字别人拿不走，见 former_owner）


def former_owner(name: str, except_id: int = 0):
    """这个名字以前是谁的本名或简称（改掉了的）。返回那个角色（只看 except_id 以外的人），没有返回 None。"""
    with get_conn() as conn:
        return conn.execute("SELECT r.* FROM role_former_names f JOIN roles r ON r.id=f.role_id WHERE f.name=? AND f.role_id!=? LIMIT 1",
                            (name, except_id)).fetchone()


def remember_former_name(role_id: int, name: str) -> None:
    if name:
        with get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO role_former_names(role_id, name, ts) VALUES(?,?,?)", (role_id, name, int(time.time())))


def list_groups(role_id: int) -> list:
    """角色的群（现在只有一个个人群）。包含还没认群的群位（group_openid 为空）。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM role_groups WHERE role_id=? ORDER BY (kind='main') DESC, id", (role_id,)
        ).fetchall()


def _new_code(conn, role_id: int, kind: str, slot_id, ttl: int) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    while conn.execute("SELECT 1 FROM bind_codes WHERE code=?", (code,)).fetchone():
        code = f"{secrets.randbelow(10**6):06d}"
    conn.execute(
        "INSERT INTO bind_codes(code, role_id, kind, slot_id, expires_ts) VALUES(?,?,?,?,?)",
        (code, role_id, kind, slot_id, int(time.time()) + ttl),
    )
    return code


def make_player_code(role_id: int) -> str:
    """玩家在已绑定的群里生成换绑码。同一角色同一时间只留一个。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM bind_codes WHERE (role_id=? AND slot_id IS NULL) OR expires_ts<?", (role_id, int(time.time())))
        return _new_code(conn, role_id, "main", None, PLAYER_CODE_TTL)


def add_slot(role_id: int, real_gid: str) -> tuple[bool, str]:
    """后台登记个人群的群号（群位）并生成认群码；认群时会替换现有的个人群。"""
    if not GID_RE.fullmatch(real_gid):
        return False, "群号要是 5~12 位数字。"
    return _slot_code(role_id, real_gid)


def npc_bind_code(role_id: int) -> str:
    """给还没有个人群的 NPC 开一个群位并生成认群码（24 小时）：在给它用的群里发「认群 码」，之后跟玩家一样在那个群里发指令。"""
    return _slot_code(role_id, "")[1]


def _slot_code(role_id: int, real_gid: str) -> tuple[bool, str]:
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
            return False, "没有这个角色。"
        # 每个角色只有一个个人群。新群号先记在认群码上，认群时才写到个人群上：
        # 旧群还绑着的时候不能先改群号标签，不然页面上新群号会立刻显示「已认群」。
        row = conn.execute("SELECT id, group_openid FROM role_groups WHERE role_id=? AND kind='main'", (role_id,)).fetchone()
        if row:
            slot_id = row["id"]
            if real_gid and not row["group_openid"]:          # 还没认过群的群位：直接改标签（本来就显示「等待认群」）
                conn.execute("UPDATE role_groups SET real_gid=? WHERE id=?", (real_gid, slot_id))
        else:
            slot_id = conn.execute(
                "INSERT INTO role_groups(role_id, real_gid, kind, created_ts) VALUES(?,?,'main',?)",
                (role_id, real_gid, int(time.time())),
            ).lastrowid
        conn.execute("DELETE FROM bind_codes WHERE role_id=? AND slot_id IS NOT NULL", (role_id,))   # 同一个人只留最新的一个后台认群码
        code = _new_code(conn, role_id, "main", slot_id, ADMIN_CODE_TTL)
        conn.execute("UPDATE bind_codes SET real_gid=? WHERE code=?", (real_gid, code))
    return True, code


def pending_code(role_id: int):
    """后台生成的、还没用掉的认群码（换绑群号 / 新 NPC）。没有返回 None。"""
    with get_conn() as conn:
        return conn.execute("SELECT code, real_gid, expires_ts FROM bind_codes WHERE role_id=? AND slot_id IS NOT NULL AND expires_ts>=? "
                            "ORDER BY expires_ts DESC LIMIT 1", (role_id, int(time.time()))).fetchone()


def consume_bind_code(code: str, bot_id: str, group_openid: str, member_openid: str) -> tuple[bool, str]:
    """在群里用认群码：把这个群的 openid 绑到对应角色的个人群上（已有就替换）。"""
    now = int(time.time())
    from . import pool
    if pool.is_room_group(bot_id, group_openid):
        return False, ROOM_GROUP_MSG
    from .announce import special_label
    sp = special_label(bot_id, group_openid)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bind_codes WHERE code=? AND expires_ts>=?", (code, now)).fetchone()
        if not row:
            return False, "认群码不对或已过期。"
        role = conn.execute("SELECT name, is_npc FROM roles WHERE id=?", (row["role_id"],)).fetchone()
        if sp and not role["is_npc"]:                       # 公告群 / 后台群 / 水群 / 戏群：大家都在，不能当玩家的个人群（短信、手机码会发到这里）；
            return False, f"这个群是{sp}，不能当个人群。请自己另建一个群，把机器人拉进去再发。"   # NPC 照旧可以（工作人员常在后台群一起扮 NPC）
        here = conn.execute(
            "SELECT r.id, r.name, r.is_npc FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.group_openid=? ORDER BY g.id",
            (bot_id, group_openid)).fetchall()
        here = [x for x in here if x["id"] != row["role_id"]]
        if here:
            # 玩家一人一个群；NPC 可以几个绑在同一个群里（扮 NPC 的人通常都在一个群，互相看得到）——前提是群里绑着的全是 NPC
            if not role["is_npc"] or not all(x["is_npc"] for x in here):
                who = next((x for x in here if not x["is_npc"]), here[0])
                return False, f"这个群已经绑给「{who['name']}」了。" + ("" if role["is_npc"] else "玩家要一人一个群。")
            mine = conn.execute("SELECT r.name FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.group_openid=? AND g.member_openid=? AND r.id!=?",
                                (bot_id, group_openid, member_openid, row["role_id"])).fetchone()
            if mine:      # 同一个人在同一个群里扮两个 NPC：群里发指令时分不清是哪一个在说话
                return False, f"你在这个群里已经扮着「{mine['name']}」了。同一个人在同一个群里只能认一个 NPC，另一个请换个群认。"
        # 后台建的 NPC 本来没有主人：谁来认群，谁就是它的主人（绑 QQ、认人都按主人走）
        conn.execute("UPDATE roles SET member_openid=? WHERE id=? AND member_openid=''", (member_openid, row["role_id"]))
        if row["slot_id"]:  # 后台登记的群位：补上 openid，换上登记的新群号
            conn.execute(
                "UPDATE role_groups SET bot_id=?, group_openid=?, member_openid=?, real_gid=CASE WHEN ?!='' THEN ? ELSE real_gid END WHERE id=?",
                (bot_id, group_openid, member_openid, row["real_gid"], row["real_gid"], row["slot_id"]),
            )
        else:  # 玩家换绑
            conn.execute(
                "UPDATE role_groups SET bot_id=?, group_openid=?, member_openid=?, real_gid='' WHERE role_id=? AND kind='main'",
                (bot_id, group_openid, member_openid, row["role_id"]),
            )
        conn.execute("DELETE FROM bind_codes WHERE code=?", (code,))
    return True, f"已把这个群绑为「{role['name']}」的个人群。"


def delete_role(role_id: int) -> dict | None:
    """彻底删除一个角色（录错了的那种）：不可恢复。
    挂了外键的表（群、短信、关系线、背包、朋友圈、公知……）随 roles 一起级联删；
    没挂外键的表（假面舞会、发言记录、奖励、次数重置、混乱寄信误投、心愿接单）这里手动清。
    TA 发起的房间整场删掉（还开着的，占着的群放回池子等清理）；TA 只是参加的房间，把 TA 从里面拿掉，别人的时间线不动。
    这个人名下没有别的角色了，绑过的 QQ 号也一起解除。返回删了什么（给后台提示用）；没有这个角色返回 None。"""
    from . import sheets
    with get_conn() as conn:
        role = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not role:
            return None
    sheets.remove_files(role_id)                       # 公知/二表的图片文件跟着删（库里的行随外键删）
    with get_conn() as conn:
        own_rooms = conn.execute("SELECT id, status, pool_id FROM rooms WHERE creator_id=?", (role_id,)).fetchall()
        for r in own_rooms:
            if r["status"] == "open" and r["pool_id"]:
                conn.execute("UPDATE pool_groups SET state='cleaning', room_id=NULL WHERE id=?", (r["pool_id"],))
            for t in ("room_lines", "room_members", "join_requests", "reward_log", "reward_adjust"):
                conn.execute(f"DELETE FROM {t} WHERE room_id=?", (r["id"],))
            conn.execute("DELETE FROM rooms WHERE id=?", (r["id"],))
        joined = conn.execute("SELECT COUNT(*) FROM room_members WHERE role_id=?", (role_id,)).fetchone()[0]
        for (iid,) in conn.execute("SELECT id FROM masq_invites WHERE from_id=? OR to_id=?", (role_id, role_id)).fetchall():
            conn.execute("DELETE FROM masq_dance_msgs WHERE invite_id=?", (iid,))
            conn.execute("DELETE FROM masq_invites WHERE id=?", (iid,))
        for sql in ("DELETE FROM room_lines WHERE role_id=?",
                    "DELETE FROM masq_reacts WHERE role_id=?",
                    "DELETE FROM masq_dance_msgs WHERE role_id=?",
                    "DELETE FROM masq_msgs WHERE role_id=?",
                    "UPDATE masq_msgs SET mention_id=0 WHERE mention_id=?",
                    "DELETE FROM count_resets WHERE role_id=?",
                    "DELETE FROM reward_log WHERE role_id=?",
                    "DELETE FROM reward_adjust WHERE role_id=?",
                    "DELETE FROM chaos_letters WHERE true_to_id=?",
                    "UPDATE wishes SET taker_id=NULL WHERE taker_id=?"):
            conn.execute(sql, (role_id,))
        conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
        qqs = 0
        if role["member_openid"] and not conn.execute("SELECT 1 FROM roles WHERE member_openid=?", (role["member_openid"],)).fetchone():
            qqs = conn.execute("DELETE FROM person_qq WHERE member_openid=?", (role["member_openid"],)).rowcount
    return {"name": role["name"], "rooms": len(own_rooms), "joined": joined, "qqs": qqs}
