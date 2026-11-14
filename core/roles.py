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


def _valid_name(name: str) -> bool:
    return 0 < len(name) <= NAME_MAX and not re.search(r"\s", name)


def create_role(name: str, bot_id: str, group_openid: str, member_openid: str, real_gid: str = "") -> tuple[bool, str]:
    """在某个群里创建角色，这个群自动成为主群（个人群）。real_gid 可不填，只是标签。"""
    if not _valid_name(name):
        return False, f"角色名要在 {NAME_MAX} 字以内，且不能带空格。"
    if real_gid and not GID_RE.fullmatch(real_gid):
        return False, "群号要是 5~12 位数字（或者不填）。"
    now = int(time.time())
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


def find_group_binding(bot_id: str, group_openid: str):
    """这个群是不是已经绑成了某个角色的个人群。"""
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM role_groups WHERE bot_id=? AND group_openid=?", (bot_id, group_openid)).fetchone()


def bind_qq(role_id: int, qq: str) -> str:
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


def get_role(role_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()


def list_all() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT id, name FROM roles ORDER BY name").fetchall()


def get_role_by_name(name: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()


def list_groups(role_id: int) -> list:
    """主群在前，备用群按登记先后。包含还没认群的群位（group_openid 为空）。"""
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


def make_player_code(role_id: int, kind: str) -> str:
    """玩家在已绑定的群里生成码：kind='backup' 加备用群，'main' 换主群。同一角色同一时间只留一个。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM bind_codes WHERE (role_id=? AND slot_id IS NULL) OR expires_ts<?", (role_id, int(time.time())))
        return _new_code(conn, role_id, kind, None, PLAYER_CODE_TTL)


def add_slot(role_id: int, real_gid: str, kind: str) -> tuple[bool, str]:
    """后台登记一个群号（群位）并生成认群码。备用群受数量上限限制；主群位会在认群时替换现有主群。"""
    if not GID_RE.fullmatch(real_gid):
        return False, "群号要是 5~12 位数字。"
    if kind not in ("main", "backup"):
        return False, "类型不对。"
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
            return False, "没有这个角色。"
        if kind == "backup":
            n = conn.execute("SELECT COUNT(*) FROM role_groups WHERE role_id=? AND kind='backup'", (role_id,)).fetchone()[0]
            if n >= get_int("max_backup_groups", 3):
                return False, "备用群已经到上限了。"
            cur = conn.execute(
                "INSERT INTO role_groups(role_id, real_gid, kind, created_ts) VALUES(?,?,'backup',?)",
                (role_id, real_gid, int(time.time())),
            )
            slot_id = cur.lastrowid
        else:
            # 主群位：每个角色只有一个主群，已有的就改它的群号标签，认群时再替换 openid
            row = conn.execute("SELECT id FROM role_groups WHERE role_id=? AND kind='main'", (role_id,)).fetchone()
            if row:
                slot_id = row["id"]
                conn.execute("UPDATE role_groups SET real_gid=? WHERE id=?", (real_gid, slot_id))
            else:
                slot_id = conn.execute(
                    "INSERT INTO role_groups(role_id, real_gid, kind, created_ts) VALUES(?,?,'main',?)",
                    (role_id, real_gid, int(time.time())),
                ).lastrowid
        code = _new_code(conn, role_id, kind, slot_id, ADMIN_CODE_TTL)
    return True, code


def consume_bind_code(code: str, bot_id: str, group_openid: str, member_openid: str, expect_kind: str | None = None) -> tuple[bool, str]:
    """在群里用认群码：把这个群的 openid 绑到对应的群位上。expect_kind 不为空时，码的类型必须一致。"""
    now = int(time.time())
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bind_codes WHERE code=? AND expires_ts>=?", (code, now)).fetchone()
        if not row:
            return False, "认群码不对或已过期。"
        if expect_kind and row["kind"] != expect_kind:
            return False, ("这是个人群的码，换个人群请用「换主群 码」。" if row["kind"] == "main"
                           else "这是备用群的码，绑备用群请用「绑定备用群 码」。")
        exist = conn.execute(
            "SELECT r.name FROM role_groups g JOIN roles r ON r.id=g.role_id WHERE g.bot_id=? AND g.group_openid=?", (bot_id, group_openid)
        ).fetchone()
        if exist:
            return False, f"这个群已经绑给「{exist['name']}」了。"
        role = conn.execute("SELECT name FROM roles WHERE id=?", (row["role_id"],)).fetchone()
        if row["slot_id"]:  # 后台登记的群位：补上 openid
            conn.execute(
                "UPDATE role_groups SET bot_id=?, group_openid=?, member_openid=? WHERE id=?",
                (bot_id, group_openid, member_openid, row["slot_id"]),
            )
        elif row["kind"] == "backup":
            n = conn.execute("SELECT COUNT(*) FROM role_groups WHERE role_id=? AND kind='backup'", (row["role_id"],)).fetchone()[0]
            if n >= get_int("max_backup_groups", 3):
                return False, "备用群已经到上限了，先用「解绑备用群 序号」解绑一个。"
            conn.execute(
                "INSERT INTO role_groups(role_id, bot_id, group_openid, member_openid, kind, created_ts) VALUES(?,?,?,?,'backup',?)",
                (row["role_id"], bot_id, group_openid, member_openid, now),
            )
        else:  # 玩家换主群
            conn.execute(
                "UPDATE role_groups SET bot_id=?, group_openid=?, member_openid=?, real_gid='' WHERE role_id=? AND kind='main'",
                (bot_id, group_openid, member_openid, row["role_id"]),
            )
        conn.execute("DELETE FROM bind_codes WHERE code=?", (code,))
    what = "个人群" if row["kind"] == "main" else "备用群"
    return True, f"已把这个群绑为「{role['name']}」的{what}。"


def unbind_backup(role_id: int, index: int) -> tuple[bool, str]:
    """index 是「我的群」列表里的序号（从 1 开始，1 一定是主群，主群不能解绑）。"""
    groups = list_groups(role_id)
    if index < 1 or index > len(groups) or groups[index - 1]["kind"] == "main":
        return False, "序号不对。主群不能解绑，只能解绑备用群。"
    with get_conn() as conn:
        conn.execute("DELETE FROM role_groups WHERE id=?", (groups[index - 1]["id"],))
    return True, f"已解绑第 {index} 个群。"


def delete_role(role_id: int) -> None:
    from . import sheets
    sheets.remove_files(role_id)                       # 公知/二表的图片文件跟着删
    with get_conn() as conn:
        conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
