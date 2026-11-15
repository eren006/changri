"""前置电话（额外扩展，默认关，独立于小手机）：
1. 管理员打开后，玩家用「提交前置电话 内容」（或小手机里的表单）交一条自我介绍，每条有一个全局唯一的编号（不管以后分到哪个组都不变）。
2. 刚提交的都是「未分组」。管理员在后台（或用「前置电话分组 A组 3 5 8」）把它们手动归成组，
   再把某个组设成「可选」（后台按钮或「可选 A组」）：这个组里的前置电话才会出现在「查看前置电话」里。
3. 玩家「拨打前置电话 12」：自动开一个电话房间（群池里有空群就分群，没有就走网页聊天室，跟普通电话一样），不占时间线。
可选「双方共同选择」（默认关）：开着时，拨打只算「选了 TA」，对方也选了你（拨了你的编号）电话才接通；
上限：每人最多主动拨出几次、每人一共最多通几次电话（拨出 + 被拨），都可以设 0 = 不限；对方通话数满了也不能再拨给 TA。"""
import time

from .db import get_config, get_conn, get_int, set_config

TEXT_MAX = 300
KIND = "前置电话"


def cfg() -> dict:
    """设置在「前置电话」设置页：precall_dial_limit / precall_total_limit / precall_mutual。"""
    return {"dial_limit": get_int("precall_dial_limit", 3), "total_limit": get_int("precall_total_limit", 0),
            "mutual": get_config("precall_mutual", "0") == "1"}


def is_on() -> bool:
    from . import extensions
    return extensions.is_enabled("precall")


def set_on(on: bool) -> None:
    set_config("ext:precall", "1" if on else "0")


# ---------- 提交 ----------

def entry_of(role_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM precall_entries WHERE role_id=?", (role_id,)).fetchone()


def submit(role_id: int, text: str) -> tuple[bool, str, int]:
    """提交或修改自己的前置电话。返回 (成功, 提示, 编号)。改内容不改编号、不改所在的组。"""
    if not is_on():
        return False, "前置电话现在没有开放。", 0
    text = "\n".join(l.strip() for l in (text or "").strip().splitlines() if l.strip())[:TEXT_MAX]
    if not text:
        return False, "写点内容吧。", 0
    with get_conn() as c:
        row = c.execute("SELECT no FROM precall_entries WHERE role_id=?", (role_id,)).fetchone()
        if row:
            c.execute("UPDATE precall_entries SET text=?, ts=? WHERE role_id=?", (text, int(time.time()), role_id))
            return True, f"已修改你的前置电话（编号 #{row['no']}）。", row["no"]
        no = c.execute("INSERT INTO precall_entries(role_id, text, ts) VALUES(?,?,?)", (role_id, text, int(time.time()))).lastrowid
    return True, f"已提交，你的前置电话编号是 #{no}。管理员分好组、设为可选之后，别人才能看到。", no


def entry_of_no(no: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM precall_entries WHERE no=?", (no,)).fetchone()


def withdraw(role_id: int) -> bool:
    with get_conn() as c:
        return c.execute("DELETE FROM precall_entries WHERE role_id=?", (role_id,)).rowcount > 0


# ---------- 分组（管理员） ----------

def groups() -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT g.name, g.open, (SELECT COUNT(*) FROM precall_entries e WHERE e.grp=g.name) AS n FROM precall_groups g ORDER BY g.rowid").fetchall()
    return [dict(r) for r in rows]


def assign(numbers: list[int], group: str) -> tuple[int, str]:
    """把这些编号归到 group（空 = 移回未分组）。返回 (成功几条, 提示)。"""
    group = (group or "").strip()[:12]
    if group and any(ch in group for ch in " \t\r\n"):
        return 0, "组名里不要有空格。"
    with get_conn() as c:
        if group and not c.execute("SELECT 1 FROM precall_groups WHERE name=?", (group,)).fetchone():
            c.execute("INSERT INTO precall_groups(name, open) VALUES(?,0)", (group,))
        n = 0
        for no in dict.fromkeys(numbers):
            n += c.execute("UPDATE precall_entries SET grp=? WHERE no=?", (group, no)).rowcount
    return n, (f"已把 {n} 条归到「{group}」。" if group else f"已把 {n} 条移回未分组。")


def set_group_open(group: str, on: bool) -> bool:
    with get_conn() as c:
        return c.execute("UPDATE precall_groups SET open=? WHERE name=?", (1 if on else 0, group.strip())).rowcount > 0


def delete_group(group: str) -> None:
    with get_conn() as c:
        c.execute("UPDATE precall_entries SET grp='' WHERE grp=?", (group,))
        c.execute("DELETE FROM precall_groups WHERE name=?", (group,))


def set_hidden(no: int, hidden: bool) -> None:
    with get_conn() as c:
        c.execute("UPDATE precall_entries SET hidden=? WHERE no=?", (1 if hidden else 0, no))


def remove(no: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM precall_entries WHERE no=?", (no,))


def all_entries() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT e.*, r.name FROM precall_entries e JOIN roles r ON r.id=e.role_id ORDER BY e.no")]


# ---------- 玩家看与拨 ----------

def visible() -> list[dict]:
    """玩家能看到的：已经「可选」的组里、没被隐藏的前置电话，按组、编号排。"""
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT e.no, e.text, e.grp, e.role_id FROM precall_entries e JOIN precall_groups g ON g.name=e.grp "
                                           "WHERE g.open=1 AND e.hidden=0 ORDER BY g.rowid, e.no")]


def dialed(role_id: int) -> int:
    """我主动拨出（选）过几个人，包括还在等对方回选的。"""
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM precall_picks WHERE from_id=?", (role_id,)).fetchone()[0]


def total(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM precall_calls WHERE from_id=? OR to_id=?", (role_id, role_id)).fetchone()[0]


def usage(role_id: int) -> str:
    k = cfg()
    lim = lambda n, cap: f"{n}/{cap}" if cap else f"{n}（不限）"      # noqa: E731
    return f"你已拨出 {lim(dialed(role_id), k['dial_limit'])}，一共通话 {lim(total(role_id), k['total_limit'])}"


def check_dial(role_id: int, no: int):
    """能拨返回 (目标 entry, "")，不能拨返回 (None, 原因)。"""
    if not is_on():
        return None, "前置电话现在没有开放。"
    k = cfg()
    with get_conn() as c:
        e = c.execute("SELECT e.* FROM precall_entries e JOIN precall_groups g ON g.name=e.grp WHERE e.no=? AND g.open=1 AND e.hidden=0", (no,)).fetchone()
    if not e:
        return None, f"没有 #{no} 这个前置电话（或者它还没开放选择）。"
    if e["role_id"] == role_id:
        return None, "不能拨给自己。"
    with get_conn() as c:
        if c.execute("SELECT 1 FROM precall_calls WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)", (role_id, e["role_id"], e["role_id"], role_id)).fetchone():
            return None, "你们已经通过话了。"
        if c.execute("SELECT 1 FROM precall_picks WHERE from_id=? AND to_id=?", (role_id, e["role_id"])).fetchone():
            return None, "你已经选过 TA 了，等 TA 也选你电话才会接通。"
    if k["dial_limit"] and dialed(role_id) >= k["dial_limit"]:
        return None, f"你最多主动拨出 {k['dial_limit']} 次，已经用完了。"
    if k["total_limit"] and total(role_id) >= k["total_limit"]:
        return None, f"你一共最多通 {k['total_limit']} 次电话，已经满了。"
    if k["total_limit"] and total(e["role_id"]) >= k["total_limit"]:
        return None, f"#{no} 的通话次数已经满了，拨不进去。"
    return e, ""


def dial(role_id: int, no: int) -> tuple[bool, str, int]:
    """拨打：校验 → 记一笔「选」→ 接通（双方共同选择关着：直接接通；开着：对方也选了我才接通）→ 建电话房间。
    返回 (成功, 提示, 房间号)；还在等对方回选时房间号是 0。"""
    from . import blocks, rooms
    e, why = check_dial(role_id, no)
    if not e:
        return False, why, 0
    blk = blocks.get_block(e["role_id"], role_id)
    if blk and not blk["silent"]:
        return False, "对方已拒绝你的联络。", 0
    with get_conn() as c:
        c.execute("INSERT INTO precall_picks(from_id, to_id, entry_no, ts) VALUES(?,?,?,?)", (role_id, e["role_id"], no, int(time.time())))
        back = c.execute("SELECT 1 FROM precall_picks WHERE from_id=? AND to_id=?", (e["role_id"], role_id)).fetchone()
    if cfg()["mutual"] and not back:
        return True, f"已选择 #{no}。对方也选了你，电话才会接通。", 0
    rid = rooms.create_call(role_id, e["role_id"], f"#{no}")
    with get_conn() as c:
        c.execute("INSERT INTO precall_calls(from_id, to_id, entry_no, room_id, ts) VALUES(?,?,?,?,?)", (role_id, e["role_id"], no, rid, int(time.time())))
    return True, f"已拨打 #{no}。", rid


def calls() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT p.*, a.name AS an, b.name AS bn, r.status FROM precall_calls p JOIN roles a ON a.id=p.from_id JOIN roles b ON b.id=p.to_id "
                                           "LEFT JOIN rooms r ON r.id=p.room_id ORDER BY p.id DESC")]


def clear_all() -> None:
    with get_conn() as c:
        for t in ("precall_calls", "precall_picks", "precall_entries", "precall_groups"):
            c.execute(f"DELETE FROM {t}")
