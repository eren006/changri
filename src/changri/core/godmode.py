"""上帝模式：管理员对玩家的干预工具，全部在后台「上帝模式」页操作（没有群指令）。
- 功能权限：按角色关掉某项功能（惩罚违规、临时禁言）
- 时间锁定：把某个角色某天（或每天）的某个时段锁住，不能约
- 重置今日次数：玩家今天的次数用完了又需要再给几次
- 清空关系线"""
import json
import time

from . import rooms, season, storycal
from .db import get_config, get_conn, set_config
from .textutil import hm_text

# 功能 → 给玩家看的名字。官约是管理员发起的，不在里面。
# 「微信」这种房间现在只有小手机的多人群聊会建（群里的「微信」指令已经改成去小手机），所以对外叫「多人群聊」；键名不改，库里存的还是「微信」。
FEATURES = {"sms": "短信", "gift": "礼物", "约会": "约会", "电话": "电话", "微信": "多人群聊", "lovemail": "心动信", "wish": "心愿", "forum": "论坛"}
ROOM_KINDS = ("约会", "电话", "微信")


def shown_features() -> dict[str, str]:
    """上帝模式页上列出来的开关。小手机多人群聊没开时不列「多人群聊」：那时它管不到任何东西，列出来只会让人以为群里还有「微信」。
    后台保存开关时也只处理这里列出的，没列出的保持原样（不然藏起来的开关每次保存都会被当成关掉）。"""
    from . import extensions
    out = dict(FEATURES)
    if not extensions.is_enabled("groupchat"):
        out.pop("微信")
    else:
        out["微信"] = "多人群聊（小手机）"
    return out


# ---------- 功能权限 ----------

def check(role_id: int, feature: str, who: str = "你") -> str:
    """能用返回空串，被关了返回要告诉玩家的话。"""
    if global_off(feature):
        return f"🚫 「{FEATURES[feature]}」功能现在被管理员暂时关闭了（所有人）。"
    with get_conn() as c:
        off = c.execute("SELECT 1 FROM role_perms WHERE role_id=? AND feature=?", (role_id, feature)).fetchone()
    return f"🚫 {who}的「{FEATURES[feature]}」功能被管理员暂时关闭了。" if off else ""


# ---------- 全体：一次对所有人生效 ----------

def global_off(feature: str) -> bool:
    return get_config(f"god_off:{feature}", "0") == "1"


def set_global(feature: str, on: bool) -> None:
    if feature in FEATURES:
        set_config(f"god_off:{feature}", "0" if on else "1")


def global_locks() -> list[dict]:
    try:
        return json.loads(get_config("god_global_locks", "[]"))
    except ValueError:
        return []


def add_global_lock(day: int | None, time_text: str, note: str = "") -> tuple[bool, str]:
    hm = rooms.parse_hm(time_text, day)
    if not hm:
        return False, storycal.parse_when(time_text, day)[1] or storycal.format_error(day)
    items = global_locks()
    items.append({"id": time.time_ns(), "day": day, "start_hm": hm[0], "end_hm": hm[1], "note": note.strip()[:40]})
    set_config("god_global_locks", json.dumps(items, ensure_ascii=False))
    return True, ""


def remove_global_lock(lock_id: int) -> None:
    set_config("god_global_locks", json.dumps([x for x in global_locks() if x["id"] != lock_id], ensure_ascii=False))


def set_perm(role_id: int, feature: str, on: bool) -> None:
    if feature not in FEATURES:
        return
    with get_conn() as c:
        if on:
            c.execute("DELETE FROM role_perms WHERE role_id=? AND feature=?", (role_id, feature))
        else:
            c.execute("INSERT OR IGNORE INTO role_perms(role_id, feature, ts) VALUES(?,?,?)", (role_id, feature, int(time.time())))


def closed_list() -> list[dict]:
    """谁的什么功能被关了。"""
    with get_conn() as c:
        rows = c.execute("SELECT p.role_id, p.feature, r.name FROM role_perms p JOIN roles r ON r.id=p.role_id ORDER BY r.name, p.feature").fetchall()
    return [{"role_id": r["role_id"], "feature": r["feature"], "label": FEATURES.get(r["feature"], r["feature"]), "name": r["name"]} for r in rows]


# ---------- 时间锁定 ----------

def add_lock(role_id: int, day: int | None, time_text: str, note: str = "") -> tuple[bool, str]:
    hm = rooms.parse_hm(time_text, day)
    if not hm:
        return False, storycal.parse_when(time_text, day)[1] or storycal.format_error(day)
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
            return False, "找不到这个角色。"
        c.execute("INSERT INTO time_locks(role_id, day, start_hm, end_hm, note, ts) VALUES(?,?,?,?,?,?)", (role_id, day, hm[0], hm[1], note.strip()[:40], int(time.time())))
    return True, ""


def remove_lock(lock_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM time_locks WHERE id=?", (lock_id,))


def locks() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT l.*, r.name FROM time_locks l JOIN roles r ON r.id=l.role_id ORDER BY r.name, l.day, l.start_hm")]


def lock_hit(role_id: int, day: int, a: str, b: str) -> dict | None:
    """这个时段撞上了管理员锁定的时间就返回一条像房间一样的记录（给 rooms.conflict 用）。"""
    with get_conn() as c:
        rows = c.execute("SELECT * FROM time_locks WHERE role_id=? AND (day IS NULL OR ? IS NULL OR day BETWEEN ?-1 AND ?+1)",
                         (role_id, day, day, day)).fetchall()
    rows = list(rows) + [x for x in global_locks() if x["day"] is None or day is None or abs(x["day"] - day) <= 1]
    for r in rows:
        # 锁定也可能跨午夜；只写了钟点、没写天的锁每天都锁，前一天、当天、后一天的都要比
        lock_days = [r["day"]] if r["day"] is not None else (rooms._near_days(day))
        hit_d = next((d for d in lock_days if rooms.spans_overlap(day, a, b, d, r["start_hm"], r["end_hm"])), "none")
        if hit_d != "none":
            return {"id": 0, "kind": "管理员锁定", "day": hit_d, "start_hm": r["start_hm"], "end_hm": r["end_hm"], "status": "open"}
    return None


# ---------- 重置今日次数 ----------

def offset(role_id: int, kind: str) -> int:
    with get_conn() as c:
        row = c.execute("SELECT n FROM count_resets WHERE role_id=? AND kind=? AND dkey=?", (role_id, kind, season.day_key())).fetchone()
    return row["n"] if row else 0


def _counters():
    from . import lovemail, messaging, wishes
    return {"sms": ("短信", lambda r: messaging.day_count(r, "sms")), "gift": ("礼物", lambda r: messaging.day_count(r, "gift")),
            "lovemail": ("心动信", lovemail.day_count),
            "约会": ("约会", lambda r: rooms.day_count(r, "约会")), "电话": ("电话", lambda r: rooms.day_count(r, "电话")), "微信": ("微信", lambda r: rooms.day_count(r, "微信")),
            "wish_post": ("挂心愿", lambda r: wishes.day_count(r, "post")), "wish_take": ("摘心愿", lambda r: wishes.day_count(r, "take"))}


RESET_KINDS = {"sms": "短信", "gift": "礼物", "lovemail": "心动信", "约会": "约会", "电话": "电话", "微信": "微信", "wish_post": "挂心愿", "wish_take": "摘心愿"}


def today_counts(role_id: int) -> dict[str, int]:
    return {k: fn(role_id) for k, (_, fn) in _counters().items()}


def reset_today(role_id: int, kinds: list[str] | None = None) -> int:
    """把今天已经用掉的次数清零（只影响今天，明天照旧）。kinds 不给 = 全部。返回清掉的项数。"""
    done = 0
    for k, (_, fn) in _counters().items():
        if kinds and k not in kinds:
            continue
        used = fn(role_id)
        if used <= 0:
            continue
        with get_conn() as c:
            c.execute("INSERT INTO count_resets(role_id,kind,dkey,n) VALUES(?,?,?,?) ON CONFLICT(role_id,kind,dkey) DO UPDATE SET n=n+excluded.n", (role_id, k, season.day_key(), used))
        done += 1
    return done


# ---------- 关系线 ----------

def clear_rels(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("DELETE FROM relations WHERE role_lo=? OR role_hi=?", (role_id, role_id)).rowcount


# ---------- 时间线 ----------

def timeline_all(role_id: int) -> list[dict]:
    """某个角色所有天的安排（含已结束，不含取消）；微信没有时间不在里面。"""
    return [dict(r) for r in rooms.timeline(role_id)]


def delete_slot(role_id: int, room_id: int) -> tuple[bool, str]:
    """删掉某人时间线上的一个时段：整个房间连同成员、群里的发言记录（复盘）一起删。
    房间是几个人共用的，所以对方的时间线上这一段也会没有。还没结束的房间，占着的群放回池子等清理。"""
    with get_conn() as c:
        r = c.execute("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE r.id=? AND m.role_id=?", (room_id, role_id)).fetchone()
        if not r:
            return False, "这个时段已经不在 TA 的时间线上了。"
        n = c.execute("SELECT COUNT(*) FROM room_members WHERE room_id=?", (room_id,)).fetchone()[0]
        if r["status"] == "open" and r["pool_id"]:
            c.execute("UPDATE pool_groups SET state='cleaning', room_id=NULL WHERE id=?", (r["pool_id"],))
        for t in ("room_lines", "room_members"):
            c.execute(f"DELETE FROM {t} WHERE room_id=?", (room_id,))
        c.execute("DELETE FROM rooms WHERE id=?", (room_id,))
    return True, f"已删除 D{r['day']} {hm_text(r['start_hm'], r['end_hm'])} 的{r['kind']}（共 {n} 人的时间线和这场的复盘记录一起没了）"
