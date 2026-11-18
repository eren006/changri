"""QQ 号掉线备案：监听用的 QQ 号掉线时，房间 QQ 群里的发言记不进复盘。这时把进行中的戏挪到小手机上写。

- 掉线（qqwatch 发出掉线提醒的同时）：记下哪个 QQ 号掉了；小手机没开就自动打开（记住是「自动开的」）；
  通知所有玩家暂停在 QQ 群回戏、到小手机上接着写，附上各自的激活码（plugins/qqwatch.py 发）。
- 掉线期间：这个号管的房间群在小手机上可以发言（平时只能看），写的内容进同一份复盘记录，接着原来的进度。
- 恢复：所有掉线的号都回来了才算恢复；小手机是自动开的就关回去；通知大家回 QQ 群接着写。
设置项 qqdown_phone_fallback（默认关，在「结合QQ小号模式（双骰）」页打开）。要先在「小手机」页把端口、网址准备好，否则玩家打不开（ready() 会列出缺什么）。
"""
from __future__ import annotations

import json

from . import pool, qqpool
from .db import get_config, get_conn, set_config

DOWN_KEY = "qqdown_links"            # 正在掉线、已经切到小手机的 QQ 号连接（JSON 列表）
AUTO_KEY = "qqdown_phone_auto"       # 1 = 小手机是因为掉线自动打开的，恢复后要关回去


def enabled() -> bool:
    return get_config("qqdown_phone_fallback", "0") == "1"


def ready() -> list[str]:
    """备案能不能用：返回还缺的东西（空 = 准备好了）。"""
    missing = []
    if not enabled():
        missing.append("上面的「掉线时自动切到小手机」关着")
    if not get_config("phone_base_url", "").strip():
        missing.append("「小手机」页还没开过、没有玩家用的网址")
    if get_config("ext:phone", "") == "0":
        missing.append("「扩展」页里小手机被关掉了")
    try:
        last = json.loads(get_config("phone_net_last", "") or "null")
    except ValueError:
        last = None
    if not last or last.get("state") != "ok":
        missing.append("还没在「小手机」页点「检查玩家能不能打开」并通过（云服务器的安全组要放行小手机端口）")
    return missing


def down_links() -> list[int]:
    try:
        return [int(x) for x in json.loads(get_config(DOWN_KEY, "") or "[]")]
    except (ValueError, TypeError):
        return []


def active() -> bool:
    return bool(down_links())


def _save(links: list[int]) -> None:
    set_config(DOWN_KEY, json.dumps(sorted(set(links))))


def room_down(room) -> bool:
    """这个房间的群是不是归掉线的 QQ 号管（掉线期间可以在小手机上写）。"""
    down = down_links()
    if not down or not room or room["status"] != "open" or not room["pool_id"]:
        return False
    g = pool.get(room["pool_id"])
    if not g or not pool.is_qq_only(g):
        return False
    owner = qqpool.owner_of(g["real_gid"])
    return owner in down or not owner           # 没认领过归属的群：只要有号掉线就算（宁可让玩家能写）


def rooms_down_for(role_id: int) -> list:
    """这个玩家参与的、正在掉线的房间。"""
    if not active():
        return []
    with get_conn() as c:
        rows = c.execute("SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.role_id=? AND r.status='open' "
                         "AND r.pool_id IS NOT NULL ORDER BY r.id", (role_id,)).fetchall()
    return [r for r in rows if room_down(r)]


def start(link_id: int) -> dict:
    """记下掉线。返回 {"first": 是不是这次掉线的第一个号, "phone_opened": 小手机是不是刚被自动打开}。"""
    links = down_links()
    first = not links
    if link_id in links:
        return {"first": False, "phone_opened": False}
    _save(links + [link_id])
    opened = False
    if get_config("phone_enabled", "0") != "1":
        set_config("phone_enabled", "1")
        set_config(AUTO_KEY, "1")
        opened = True
    from . import phoneserver
    phoneserver.start()
    return {"first": first, "phone_opened": opened}


def end(link_id: int) -> dict:
    """这个号恢复了。返回 {"all_clear": 所有号都恢复了, "phone_closed": 自动开的小手机关回去了}。"""
    links = [x for x in down_links() if x != link_id]
    _save(links)
    if links:
        return {"all_clear": False, "phone_closed": False}
    closed = False
    if get_config(AUTO_KEY, "0") == "1":
        set_config("phone_enabled", "0")
        closed = True
    set_config(AUTO_KEY, "0")
    from . import phoneserver
    phoneserver.start()
    return {"all_clear": True, "phone_closed": closed}


def players() -> list[int]:
    with get_conn() as c:
        return [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0 ORDER BY id")]


def down_text(role_id: int) -> str:
    """掉线通知（发到每个玩家的个人群）。备案准备好了就带上小手机网址和这个人的激活码。"""
    from . import phonecodes
    head = "⚠️ 拉群用的 QQ 号掉线了，QQ 群里的发言现在记不进复盘，请先暂停在 QQ 群里回戏。"
    base = get_config("phone_base_url", "").rstrip("/")
    if not enabled() or not base:
        return head + "\n管理员正在处理，恢复后会再通知你。"
    code = phonecodes.code_for_role(role_id)
    return (head + f"\n进行中的戏请到小手机上接着写，之前的发言都在里面：{base}/p"
            f"\n你的激活码：{code}（只给你自己用，别发给别人）\nQQ 号恢复后会再通知你回 QQ 群。")


UP_TEXT = "✅ 拉群用的 QQ 号已经恢复，可以回 QQ 群接着回戏了。小手机上写的已经记进复盘，从那里的进度接着来就行。"
