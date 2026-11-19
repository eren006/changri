"""论坛：发帖、回帖（可引用楼层）、帖子和楼层的赞/踩。署名随便写，真实角色只在后台看得到。"""
import re
import time

from .db import get_conn

SNIP = 30


def pid(n: int) -> str:
    return f"{n:03d}"


def new_post(role_id: int, sign: str, content: str) -> int:
    with get_conn() as c:
        return c.execute("INSERT INTO posts(role_id, sign, content, created_ts) VALUES(?,?,?,?)", (role_id, sign, content, int(time.time()))).lastrowid


def get_post(post_id: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def new_reply(post_id: int, role_id: int, sign: str, content: str, quote_floor: int = 0) -> tuple[bool, str, int]:
    from . import godmode
    closed = godmode.check(role_id, "forum")                 # 上帝模式：全体或个人关了论坛
    if closed:
        return False, closed, 0
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            return False, "没有这个帖子。", 0
        quote = ""
        if quote_floor:
            q = c.execute("SELECT sign, content FROM replies WHERE post_id=? AND floor=?", (post_id, quote_floor)).fetchone()
            if not q:
                return False, f"这个帖子没有第 {quote_floor} 楼。", 0
            quote = f"{q['sign']}：{q['content'][:SNIP]}{'…' if len(q['content']) > SNIP else ''}"
        floor = (c.execute("SELECT MAX(floor) FROM replies WHERE post_id=?", (post_id,)).fetchone()[0] or 1) + 1   # 1 楼是主帖，回复从 2 楼起
        c.execute("INSERT INTO replies(post_id, floor, role_id, sign, quote, content, created_ts) VALUES(?,?,?,?,?,?,?)",
                  (post_id, floor, role_id, sign, quote, content, int(time.time())))
    return True, "", floor


def vote(ref: str, role_id: int, val: int) -> tuple[bool, str]:
    """同一个人对同一个目标只能有一票；再投同一边提示已投，投另一边改票。"""
    from . import godmode
    closed = godmode.check(role_id, "forum")
    if closed:
        return False, closed
    with get_conn() as c:
        row = c.execute("SELECT val FROM votes WHERE ref=? AND role_id=?", (ref, role_id)).fetchone()
        if row and row["val"] == val:
            return False, "你已经投过这一边了。"
        c.execute("INSERT INTO votes(ref, role_id, val) VALUES(?,?,?) ON CONFLICT(ref, role_id) DO UPDATE SET val=excluded.val", (ref, role_id, val))
    return True, ""


def reply_id(post_id: int, floor: int) -> int | None:
    with get_conn() as c:
        r = c.execute("SELECT id FROM replies WHERE post_id=? AND floor=?", (post_id, floor)).fetchone()
    return r["id"] if r else None


def tally(ref: str) -> tuple[int, int]:
    with get_conn() as c:
        r = c.execute("SELECT COALESCE(SUM(val=1),0) up, COALESCE(SUM(val=-1),0) down FROM votes WHERE ref=?", (ref,)).fetchone()
    return r["up"], r["down"]


def list_posts(limit: int = 30) -> list:
    with get_conn() as c:
        return c.execute("SELECT p.*, (SELECT COUNT(*) FROM replies WHERE post_id=p.id) AS n FROM posts p ORDER BY p.id DESC LIMIT ?", (limit,)).fetchall()


def thread(post_id: int) -> tuple | None:
    p = get_post(post_id)
    if not p:
        return None
    with get_conn() as c:
        rs = c.execute("SELECT * FROM replies WHERE post_id=? ORDER BY floor", (post_id,)).fetchall()
    return p, rs


def parse_post_ref(text: str) -> int | None:
    """贴号：001、1、#1、[1] 都认（写法跟全部编号一样，textutil.parse_id）。"""
    from .textutil import parse_id
    n = parse_id(text)
    return n if n and n < 10 ** 6 else None


def delete_post(post_id: int) -> bool:
    with get_conn() as c:
        rids = [r["id"] for r in c.execute("SELECT id FROM replies WHERE post_id=?", (post_id,))]
        n = c.execute("DELETE FROM posts WHERE id=?", (post_id,)).rowcount
        c.execute("DELETE FROM votes WHERE ref=?", (f"p{post_id}",))
        for rid in rids:
            c.execute("DELETE FROM votes WHERE ref=?", (f"r{rid}",))
    return n > 0


def delete_floor(post_id: int, floor: int) -> bool:
    """楼层号保留（别的楼的引用和编号不乱），内容换成占位。"""
    if floor < 2:
        return False
    with get_conn() as c:
        r = c.execute("SELECT id FROM replies WHERE post_id=? AND floor=?", (post_id, floor)).fetchone()
        if not r:
            return False
        c.execute("UPDATE replies SET content='该楼层已被删除', quote='', sign='—' WHERE id=?", (r["id"],))
        c.execute("DELETE FROM votes WHERE ref=?", (f"r{r['id']}",))
    return True
