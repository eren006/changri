"""NPC：角色表上的一个标记。NPC 不参与出场抽签、漂流瓶、混乱短信的随机对象、群体闹钟、季末报告；
其余功能和玩家一样。「创建NPC」建出来时还没有个人群（不占群池），回执里带一个认群码：
在给 NPC 用的群里发「认群 码」，之后 NPC 就在那个群里发短信、约会，跟玩家一样。"""
import time

from .db import get_conn
from .roles import NAME_MAX, _valid_name


def is_npc(role_id: int) -> bool:
    with get_conn() as c:
        r = c.execute("SELECT is_npc FROM roles WHERE id=?", (role_id,)).fetchone()
    return bool(r and r["is_npc"])


def names() -> list[str]:
    with get_conn() as c:
        return [r["name"] for r in c.execute("SELECT name FROM roles WHERE is_npc=1 ORDER BY id")]


def listing() -> list[dict]:
    """NPC 一览：名字、有没有个人群。"""
    with get_conn() as c:
        rows = c.execute("SELECT r.id, r.name, (SELECT COUNT(*) FROM role_groups g WHERE g.role_id=r.id AND COALESCE(g.group_openid,'')!='') AS groups "
                         "FROM roles r WHERE r.is_npc=1 ORDER BY r.id").fetchall()
    from . import charprofile
    out = []
    for r in rows:
        d = dict(r)
        d["profile"] = "　".join(charprofile.summary_lines(r))
        out.append(d)
    return out


def set_flag(name: str, on: bool) -> tuple[bool, str]:
    with get_conn() as c:
        r = c.execute("SELECT id, is_npc FROM roles WHERE name=?", (name,)).fetchone()
        if not r:
            return False, f"找不到角色「{name}」。"
        if bool(r["is_npc"]) == on:
            return False, f"「{name}」本来就{'是' if on else '不是'} NPC。"
        c.execute("UPDATE roles SET is_npc=? WHERE id=?", (1 if on else 0, r["id"]))
    return True, f"「{name}」{'已设为 NPC' if on else '已改回普通角色'}。"


def create(name: str) -> tuple[bool, str]:
    """建一个不带群的 NPC（在后台、公告群这些地方建的），回执带认群码。"""
    name = (name or "").strip()
    if not _valid_name(name):
        return False, f"角色名要在 {NAME_MAX} 字以内，且不能带空格。"
    with get_conn() as c:
        if c.execute("SELECT 1 FROM roles WHERE name=?", (name,)).fetchone() or c.execute("SELECT 1 FROM role_profiles WHERE short=?", (name,)).fetchone():
            return False, f"「{name}」已经有人用了（本名或简称），换一个名字。"
        rid = c.execute("INSERT INTO roles(name, member_openid, created_ts, is_npc) VALUES(?,?,?,1)", (name, "", int(time.time()))).lastrowid
    from .roles import npc_bind_code
    code = npc_bind_code(rid)
    return True, (f"第 1 步完成：已创建 NPC「{name}」。\n"
                  f"第 2 步：把机器人拉进给它用的群，扮它的人在那个群里发「认群 {code}」（24 小时内有效；过期了在后台「角色 → {name} → 换绑群号」再生成）。\n"
                  f"之后机器人会带着绑 QQ、发回执，NPC 在那个群里发指令跟玩家一样。")


def delete(name: str, confirmed: bool) -> tuple[bool, str]:
    """「删除NPC 名字」：只删 NPC（玩家角色在后台删）。不带「确认」先说会删掉什么，带了才真删；删完可以重新「创建NPC」。"""
    from .roles import delete_role, get_role_by_name
    r = get_role_by_name((name or "").strip())
    if not r:
        return False, f"没有叫「{name}」的角色。"
    if not r["is_npc"]:
        return False, f"「{r['name']}」不是 NPC。玩家角色请在后台「角色」页删除。"
    if not confirmed:
        return False, (f"要彻底删除 NPC「{r['name']}」吗？会删掉它的个人群绑定、短信、关系线、背包、时间线，"
                       f"它发起的约会/电话整场删除。不能撤销。\n确认请发：删除NPC {r['name']} 确认")
    res = delete_role(r["id"])
    if not res:
        return False, "这个 NPC 已经不在了。"
    return True, f"已彻底删除 NPC「{res['name']}」。要重来就再发「创建NPC {res['name']}」，拿新的认群码去认群。"
