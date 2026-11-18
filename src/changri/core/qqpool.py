"""群号池：每个官方机器人可以连一个或多个 QQ 号（连到 SnowLuma 等 OneBot 11 客户端），这些号所在的群放进同一个池子。
池里的群才会被监听、才能改群名；池外的群一律不处理。数据放在各机器人自己的工作区。"""
import re
import time

from .db import get_config, get_conn, set_config

KINDS = {"listen": "只监听", "bot": "机器人也在", "room": "房间群"}
DEFAULT_URL = "ws://127.0.0.1:3001"


def conf() -> dict:
    return {"url": get_config("qqlink_url", DEFAULT_URL), "token": get_config("qqlink_token", ""),
            "on": get_config("qqlink_on", "0") == "1"}


def save_conf(url: str, token: str | None, on: bool) -> str:
    url = url.strip()
    if not re.fullmatch(r"wss?://[^\s]+", url):
        return "地址要以 ws:// 或 wss:// 开头"
    set_config("qqlink_url", url)
    if token is not None:                      # None = 不改口令；口令不回显，留空就是不改
        set_config("qqlink_token", token.strip())
    set_config("qqlink_on", "1" if on else "0")
    return ""


# ---------- QQ 号连接：一个机器人可以连多个 QQ 号 ----------

def links() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM qq_links ORDER BY id")]


def get_link(link_id: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM qq_links WHERE id=?", (link_id,)).fetchone()
    return dict(r) if r else None


def _check_url(url: str) -> str:
    return "" if re.fullmatch(r"wss?://[^\s]+", url.strip()) else "地址要以 ws:// 或 wss:// 开头"


def add_link(name: str, url: str, token: str = "", enabled: bool = True) -> tuple[int | None, str]:
    err = _check_url(url)
    if err:
        return None, err
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM qq_links").fetchone()[0]
        lid = conn.execute("INSERT INTO qq_links(name,url,token,enabled,created_ts) VALUES(?,?,?,?,?)",
                           (name.strip()[:20] or f"QQ号{n + 1}", url.strip(), token.strip(), int(enabled), int(time.time()))).lastrowid
    return lid, ""


def update_link(link_id: int, name: str, url: str, token: str | None, enabled: bool) -> str:
    err = _check_url(url)
    if err:
        return err
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM qq_links WHERE id=?", (link_id,)).fetchone():
            return "没有这个 QQ 号连接"
        conn.execute("UPDATE qq_links SET name=?, url=?, enabled=? WHERE id=?", (name.strip()[:20] or f"QQ号{link_id}", url.strip(), int(enabled), link_id))
        if token is not None:                  # None = 不改口令；口令不回显，留空就是不改
            conn.execute("UPDATE qq_links SET token=? WHERE id=?", (token.strip(), link_id))
    return ""


def link_groups(link_id: int) -> list[str]:
    with get_conn() as conn:
        return [r["gid"] for r in conn.execute("SELECT gid FROM qq_groups WHERE link_id=?", (link_id,))]


def link_usage(link_id: int) -> int:
    """这个 QQ 号名下有几个房间群正被房间使用或等待清场。"""
    from . import pool
    with get_conn() as conn:
        gids = {r["gid"] for r in conn.execute("SELECT gid FROM qq_groups WHERE link_id=?", (link_id,))}
    return sum(1 for g in pool.all_groups() if pool.is_qq_only(g) and g["real_gid"] in gids and g["state"] != "free")


def delete_link(bot_id: str, link_id: int) -> str:
    """删除一个 QQ 号连接。名下的群正被房间用着（或待清场）时不能删；删掉后，它名下的群一起移出群号池。"""
    from . import pool
    link = get_link(link_id)
    if not link:
        return "没有这个 QQ 号连接"
    n = link_usage(link_id)
    if n:
        return f"「{link['name']}」名下有 {n} 个群正在被房间使用或等待清场，先结束房间、清场之后才能删"
    with get_conn() as conn:
        gids = [r["gid"] for r in conn.execute("SELECT gid FROM qq_groups WHERE link_id=?", (link_id,))]
    for gid in gids:
        g = pool.is_pool_group(bot_id, pool.QQ_PREFIX + gid)
        if g:
            pool.delete(g["id"])
    with get_conn() as conn:
        conn.execute("DELETE FROM qq_groups WHERE link_id=?", (link_id,))
        conn.execute("DELETE FROM qq_links WHERE id=?", (link_id,))
    return ""


def ensure_links() -> int:
    """老版本只有一条连接，存在 config 里：第一次启动时搬成一条 QQ 号连接，原来的群都归它。返回新建的连接 id（没搬返回 0）。"""
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM qq_links").fetchone():
            return 0
    url, token, on = get_config("qqlink_url", ""), get_config("qqlink_token", ""), get_config("qqlink_on", "0") == "1"
    if not url or not (on or token):
        return 0
    lid, _ = add_link("QQ号1", url, token, on)
    if lid:
        with get_conn() as conn:
            conn.execute("UPDATE qq_groups SET link_id=? WHERE link_id=0", (lid,))
    return lid or 0


def owner_of(gid: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT link_id FROM qq_groups WHERE gid=?", (str(gid),)).fetchone()
    return row["link_id"] if row else 0


def claim_owner(gid: str, link_id: int) -> None:
    """群还没有归属的 QQ 号时，第一个听到它的号就是它的归属（之后别的号听到同一个群的消息不重复处理）。"""
    with get_conn() as conn:
        conn.execute("UPDATE qq_groups SET link_id=? WHERE gid=? AND link_id=0", (link_id, str(gid)))


ALREADY = "这个群已经在池子里了"     # 在本机器人自己的池里；调用方按这句判断「已在池里，改成房间群」


def _other_bots():
    """逐个进入别的（启用中的）机器人的工作区，产出 (名字, 它启用着的 QQ 号连接编号)。
    只有一个机器人（或旧的单机器人部署）时什么也不产出。"""
    from . import botconf
    from .db import current_bot, list_bot_ids, use_bot, workspace_path
    me = current_bot()
    if not me:
        return
    for b in list_bot_ids(only_enabled=True):
        if not b or b == me or not workspace_path(b).exists():
            continue
        name = ((botconf.get_account(b) or {}).get("note") or "").strip() or b
        with use_bot(b):
            yield name, {ln["id"] for ln in links() if ln["enabled"]}


def held_by_other_bots() -> dict[str, str]:
    """别的机器人池子里的 QQ 群：{群号: 那个机器人的名字}。同一个 QQ 小号可以给几个机器人用，但一个群只能归一个机器人，
    不然两边会同时领这个群开房、互相改群名。
    只算那边还连着的：群所属的 QQ 号连接被删了或关了（小号已经从那个机器人解绑），这个群就不算占着。
    官方机器人的备用群（在群里发过「设为备用群 群号」、还在池里的）不看小号连没连着，一直算。"""
    out: dict[str, str] = {}
    for name, on in _other_bots():
        with get_conn() as conn:
            for r in conn.execute(
                    "SELECT real_gid FROM pool_groups WHERE group_openid NOT LIKE 'qq:%' AND real_gid!='' AND in_pool=1 AND moved=0"):
                out.setdefault(r[0], name)
            if not on:
                continue
            for r in conn.execute("SELECT q.gid, q.link_id FROM qq_groups q WHERE NOT EXISTS ("
                                  "SELECT 1 FROM pool_groups p WHERE p.group_openid='qq:' || q.gid AND p.moved=1)"):
                if r["link_id"] in on or not r["link_id"]:      # 手动贴的群没记是哪个号，那边有连着的号就算
                    out.setdefault(r["gid"], name)
    return out


def auth_held_by_other_bots() -> dict[str, str]:
    """别的机器人「授权的 QQ 号」：{QQ 号: 机器人名字}。同一个号在两边都授权，群里发一次「加入群号组」两边都会收，所以也不许重。"""
    out: dict[str, str] = {}
    for name, on in _other_bots():
        if not on:                                              # 那边一个 QQ 号都没连着，指令根本收不到
            continue
        for q in auth_qqs():
            out.setdefault(q, name)
    return out


def add_group(gid: str, note: str = "", kind: str = "listen", link_id: int = 0) -> str:
    gid = gid.strip()
    if not re.fullmatch(r"\d{5,12}", gid):
        return "群号是 5 到 12 位数字"
    if kind not in KINDS:
        return "类型不对"
    with get_conn() as conn:
        mine = conn.execute("SELECT 1 FROM pool_groups WHERE real_gid=? AND group_openid NOT LIKE 'qq:%' AND in_pool=1 AND moved=0",
                            (gid,)).fetchone()
    if mine:
        return f"群 {gid} 现在归官方机器人管（在群里发过「设为备用群」）。要交回小号，到「群池」页点「转回小号群」"
    other = held_by_other_bots().get(gid)
    if other:
        return (f"群 {gid} 已经绑在机器人「{other}」上了。要在这里用它，先到「{other}」的后台把这个群从群号组移出，"
                f"或者在那边把这个 QQ 小号的连接删掉（解绑）")
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM qq_groups WHERE gid=?", (gid,)).fetchone():
            return ALREADY
        conn.execute("INSERT INTO qq_groups(gid, note, kind, link_id, created_ts) VALUES(?,?,?,?,?)", (gid, note.strip()[:40], kind, link_id, int(time.time())))
    return ""


def remove_group(gid: str) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM qq_groups WHERE gid=?", (gid,)).rowcount > 0


def update_group(gid: str, note: str, kind: str, group_openid: str = "") -> bool:
    if kind not in KINDS:
        return False
    with get_conn() as conn:
        return conn.execute("UPDATE qq_groups SET note=?, kind=?, group_openid=? WHERE gid=?",
                            (note.strip()[:40], kind, group_openid.strip(), gid)).rowcount > 0


def list_groups() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM qq_groups ORDER BY created_ts, gid")]


def in_pool(gid: str) -> bool:
    """小号要不要管这个群：在 qq_groups 里，并且没有转给官方机器人（pool_groups 里 qq:群号 那一行 moved=1 就是转走了）。"""
    with get_conn() as conn:
        return bool(conn.execute("SELECT 1 FROM qq_groups q WHERE q.gid=? AND NOT EXISTS ("
                                 "SELECT 1 FROM pool_groups p WHERE p.group_openid='qq:' || q.gid AND p.moved=1)", (str(gid),)).fetchone())


def openid_of(gid: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT group_openid FROM qq_groups WHERE gid=?", (str(gid),)).fetchone()
    return row["group_openid"] if row else ""


# ---------- 自动关联：同一句话，QQ 号在真实群里听到、官方机器人在某个 group_openid 里收到 → 两边是同一个群 ----------
_LINK_WINDOW = 15                      # 秒
_heard: list = []                      # (ts, 机器人, 真实群号, 规整后的文字)：QQ 号听到的、还没关联的群
_seen: list = []                       # (ts, 机器人, group_openid, 规整后的文字)：官方机器人收到的


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _same_line(heard: str, seen: str) -> bool:
    """QQ 号那边带着「@机器人」，官方机器人这边没有：去掉空白后，听到的以收到的结尾就算同一句。"""
    return len(seen) >= 2 and (heard == seen or heard.endswith(seen))


def _bot() -> str:
    from .db import current_bot
    try:
        return current_bot() or ""
    except Exception:  # noqa: BLE001
        return ""


def _prune(buf: list, now: float) -> None:
    buf[:] = [x for x in buf[-200:] if now - x[0] <= _LINK_WINDOW]


def _try_link(bot: str, now: float) -> str:
    """配得上就写进 qq_groups.group_openid，返回关联上的真实群号；配不上或有歧义就什么都不做。"""
    for ts_s, b_s, openid, seen in reversed(_seen):
        if b_s != bot:
            continue
        gids = {g for ts_h, b_h, g, heard in _heard if b_h == bot and abs(ts_h - ts_s) <= _LINK_WINDOW and _same_line(heard, seen)}
        if len(gids) != 1:
            continue
        gid = gids.pop()
        with get_conn() as conn:
            if conn.execute("SELECT 1 FROM qq_groups WHERE group_openid=? AND kind!='room'", (openid,)).fetchone():
                continue                                        # 这个 openid 已经关联了别的群
            if conn.execute("UPDATE qq_groups SET group_openid=? WHERE gid=? AND kind!='room' AND group_openid=''", (openid, gid)).rowcount:
                _heard[:] = [x for x in _heard if x[2] != gid]
                _seen[:] = [x for x in _seen if x[2] != openid]
                return gid
    return ""


def note_heard(gid: str, text: str) -> str:
    """QQ 号听到池里某个群的一句话。这个群还没关联时记下来，试着配对。"""
    with get_conn() as conn:
        row = conn.execute("SELECT group_openid, kind FROM qq_groups WHERE gid=?", (str(gid),)).fetchone()
    if not row or row["group_openid"] or row["kind"] == "room" or not _squash(text):
        return ""
    now, bot = time.time(), _bot()
    _prune(_heard, now); _prune(_seen, now)
    _heard.append((now, bot, str(gid), _squash(text)))
    return _try_link(bot, now)


def note_official(group_openid: str, text: str) -> str:
    """官方机器人收到一条群消息。这个群还没对上池里的群时记下来，试着配对。"""
    if not group_openid or not _squash(text):
        return ""
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM qq_groups WHERE group_openid=?", (group_openid,)).fetchone():
            return ""
        if not conn.execute("SELECT 1 FROM qq_groups WHERE kind!='room' AND group_openid='' LIMIT 1").fetchone():
            return ""                                           # 没有等关联的群，不用记
    now, bot = time.time(), _bot()
    _prune(_heard, now); _prune(_seen, now)
    _seen.append((now, bot, group_openid, _squash(text)))
    return _try_link(bot, now)


def skip_users() -> set[str]:
    """不记进 log 的发言人（QQ 号或昵称，一行一个）：官方机器人自己的发言，它的骰子回复已经另外记了。"""
    return {x.strip() for x in get_config("qqlink_skip", "").splitlines() if x.strip()}


def save_skip(text: str) -> None:
    set_config("qqlink_skip", "\n".join(x.strip() for x in text.splitlines() if x.strip()))


def add_rooms(bot_id: str, text: str, set_name: str = "", link_id: int = 0) -> tuple[int, list[str]]:
    """批量加「房间群」：一行一个群号（空格、逗号也行）。放进群池后，约会房间会随机领到其中一个，并自动改群名；官方机器人不进群，一言不发。
    返回 (加了几个, 没加成的原因)。"""
    from . import pool
    ok, bad = 0, []
    for gid in re.split(r"[\s,，、;；]+", text.strip()):
        if not gid:
            continue
        err = add_group(gid, "", "room", link_id)
        if err and err != ALREADY:
            bad.append(f"{gid}：{err}")
            continue
        if err:                                   # 已在池里：升成房间群
            update_group(gid, "", "room", "")
        pid = pool.register_qq(bot_id, gid, set_name)
        _unmove(pid)                              # 走到这里说明官方机器人那边没在用这个群：小号这一行重新算数
        pool.set_in_pool(pid, True)
        ok += 1
    return ok, bad


ROOM_NAME_AUTO = "auto"
ROOM_NAME_PRESETS = [(ROOM_NAME_AUTO, "约会 D3 20:00-21:00 咖啡馆 小明、小红（跟长日系统一样）")]
KIND_IN_NAME: dict[str, str] = {}      # 群名里的类型叫法要换的写在这里（0.21 起私约改名约会，不用再换）
NAME_MAX = 30                       # QQ 群名长度上限（跟长日系统一致）
NAME_CHARS = 5                      # 群名里每个人名最多取几个字


def fmt_names(names) -> str:
    """参与者的名字拼成群名里的一段：1 到 3 个人用「×」连起来，更多的写成「甲×乙等 5 人」。（自己写模板时用）"""
    if isinstance(names, str):
        names = [x for x in re.split(r"[、,，\s]+", names) if x]
    if len(names) <= 3:
        return "×".join(names)
    return "×".join(names[:2]) + f"等{len(names)}人"


def _short_place(place: str) -> str:
    c = list(place or "")
    return "".join(c[-5:]) if len(c) > 7 else (place or "")


def _clamp(parts: list[str]) -> str:
    """parts = [类型, D几, 时间段, 地点(可无), 人名]。超长时：地点缩成后 3 字 → 人名各取前 3 字 → 时间只留开始 → 末尾截断。"""
    n = lambda: len(" ".join(parts))
    if n() <= NAME_MAX:
        return " ".join(parts)
    if len(parts) >= 5 and len(parts[3]) > 3:
        parts[3] = parts[3][-3:]
    if n() > NAME_MAX:
        parts[-1] = re.sub(r"[^、]+", lambda m: m.group(0)[:3], parts[-1])
    if n() > NAME_MAX and len(parts) >= 4:
        parts[2] = re.sub(r"^(\d{1,2}:\d{2})-\d{1,2}:\d{2}$", r"\1", parts[2])
    out = " ".join(parts)
    return out if len(out) <= NAME_MAX else out[:NAME_MAX - 1] + "…"


def room_name(room, names, template: str = "") -> str:
    """房间占用时的群名。默认（auto）跟长日系统一样：类型 D几 时间 地点 人名，超长逐步缩短。
    也可以自己写模板，能用 {kind} {day} {time} {place} {names} {id}。"""
    tpl = template or get_config("qqlink_room_name", ROOM_NAME_AUTO)
    kind, day = KIND_IN_NAME.get(room["kind"], room["kind"]), (room["day"] if "day" in room.keys() else None)
    start = (room["start_hm"] if "start_hm" in room.keys() else "") or ""
    end = (room["end_hm"] if "end_hm" in room.keys() else "") or ""
    place = (room["place"] if "place" in room.keys() else "") or ""
    tm = f"{start}-{end}" if start and end else start
    if tpl in ("", ROOM_NAME_AUTO) or tpl == "{kind}·{names}":
        who = "、".join(x[:NAME_CHARS] for x in names) if len(names) <= 2 else "多人"
        parts = [kind]
        if day is not None:
            parts.append(f"D{day}")
        if tm:
            parts.append(tm)
        if place:
            parts.append(_short_place(place))
        parts.append(who)
        return _clamp(parts)
    out = (tpl.replace("{kind}", kind).replace("{names}", fmt_names(names)).replace("{id}", str(room["id"]))
           .replace("{day}", f"D{day}" if day is not None else "").replace("{time}", tm).replace("{place}", _short_place(place)))
    return out[:NAME_MAX]


FREE_NAME_DEFAULT = "长日备用"          # 房间结束、群名改回来时的默认名字


def free_name(set_name: str = "") -> str:
    return get_config("qqlink_free_name", FREE_NAME_DEFAULT).replace("{set}", set_name)[:30]


# ---------- 群号组：每个机器人（每个 QQ 号）自己的一套，连上 QQ 号时直接出现在页面上 ----------

def auth_qqs() -> set[str]:
    """能在群里发「加入群号组」这类指令的 QQ 号（一行一个）。没填就谁也不能用。"""
    return {x.strip() for x in get_config("qqlink_auth", "").splitlines() if x.strip()}


def save_auth(text: str) -> list[str]:
    """保存授权的 QQ 号。已经在别的机器人那边授权的号不保存，返回没保存的原因（一个号一条）。"""
    others = auth_held_by_other_bots()
    keep, bad = [], []
    for q in (x.strip() for x in re.split(r"[\s,，]+", text)):
        if not q or q in keep:
            continue
        if q in others:
            bad.append(f"QQ {q} 已经是机器人「{others[q]}」的授权号了。要在这里授权，先到「{others[q]}」的后台把它从授权名单里删掉")
            continue
        keep.append(q)
    set_config("qqlink_auth", "\n".join(keep))
    return bad


def _unmove(pool_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE pool_groups SET moved=0 WHERE id=?", (pool_id,))


def join_set(bot_id: str, gid: str, set_name: str, link_id: int = 0) -> str:
    """把一个群加进某个群号组（没有这个组就新建）。已经在别的组，就挪过来。返回空串表示成功，否则是原因。"""
    from . import pool
    set_name = set_name.strip()
    if not set_name or len(set_name) > 20:
        return "群号组名字要 1 到 20 个字"
    err = add_group(gid, "", "room", link_id)
    if err and err != ALREADY:
        return err
    if err:
        update_group(gid, "", "room", "")
        if link_id:
            with get_conn() as conn:
                conn.execute("UPDATE qq_groups SET link_id=? WHERE gid=? AND link_id=0", (link_id, gid))
    existing = [x for x in sets(bot_id) if x["name"] == set_name]
    keep_off = bool(existing) and not existing[0]["enabled"]            # 这个组已关着，新加的群也先关着
    pid = pool.register_qq(bot_id, gid, set_name)
    _unmove(pid)                                                          # 官方机器人那边没在用（不然上面 add_group 就拦了）
    pool.update(pid, gid, pool.get(pid)["label"], set_name)               # 已在别的组时，改到这个组
    if keep_off:
        ok, why = pool.set_in_pool(pid, False)
        return "" if ok or "正在使用" in why else why
    ok, why = pool.set_in_pool(pid, True)
    return "" if ok else why


def leave_set(bot_id: str, gid: str) -> str:
    from . import pool
    g = pool.is_pool_group(bot_id, pool.QQ_PREFIX + gid)
    if not g:
        return "这个群不在任何群号组里"
    ok, why = pool.delete(g["id"])
    if not ok:
        return why
    with get_conn() as conn:
        conn.execute("DELETE FROM qq_groups WHERE gid=? AND kind='room'", (gid,))
    return ""


def sets(bot_id: str) -> list[dict]:
    """后台用：这个机器人的所有群号组，每组里的群和它们的状态（空闲 / 占用 / 待清场）。"""
    from . import pool
    out: dict[str, list] = {}
    for g in pool.all_groups():
        if g["bot_id"] == bot_id and pool.is_qq_only(g):
            out.setdefault(g["set_name"] or "（未分组）", []).append(g)
    official = {g["real_gid"]: g for g in pool.all_groups()
                if g["bot_id"] == bot_id and not pool.is_qq_only(g) and g["real_gid"]}
    for v in out.values():
        for g in v:
            o = official.get(g["real_gid"])
            g["official_id"] = o["id"] if o else 0          # 这个群也登记过官方机器人备用群：可以一键转过去 / 转回来
    return [{"name": k, "groups": v, "enabled": any(g["in_pool"] for g in v if not g["moved"]) or (all(g["moved"] for g in v) and any(g["in_pool"] for g in v)),
             "busy": sum(1 for g in v if g["state"] != "free")} for k, v in out.items()]


def rename_set(bot_id: str, old: str, new: str) -> str:
    """群号组改名。名字不能和别的组重复。返回空串表示成功。"""
    from . import pool
    new = new.strip()
    if not new or len(new) > 20:
        return "群号组名字要 1 到 20 个字"
    names = {x["name"] for x in sets(bot_id)}
    if old not in names:
        return "没有这个群号组"
    if new == old:
        return ""
    if new in names:
        return f"已经有叫「{new}」的群号组了，名字不能重复"
    with get_conn() as conn:
        for g in pool.all_groups():
            if g["bot_id"] == bot_id and pool.is_qq_only(g) and g["set_name"] == old:
                conn.execute("UPDATE pool_groups SET set_name=? WHERE id=?", (new, g["id"]))
    return ""


def set_enabled(bot_id: str, set_name: str, on: bool) -> str:
    """群号组的「开启使用」开关：开着，组里的群才会被房间领用；组里有群正在占用（或待清场）时不能关。返回空串表示成功。"""
    from . import pool
    cur = [x for x in sets(bot_id) if x["name"] == set_name]
    if not cur:
        return "没有这个群号组"
    if not on and cur[0]["busy"]:
        return f"「{set_name}」里还有 {cur[0]['busy']} 个群正在被房间使用或等待清场，等它们结束、清场后才能关"
    bad = []
    for g in cur[0]["groups"]:
        ok, why = pool.set_in_pool(g["id"], on)
        if not ok:
            bad.append(f"{g['real_gid']}：{why}")
    return "；".join(bad)


def trpg_bindings() -> list[dict]:
    """后台用：QQ 号 ↔ 跑团身份（玩家名、名下的卡）。"""
    with get_conn() as c:
        out = []
        for r in c.execute("SELECT member_openid, player_id, created_ts FROM trpg_identities WHERE member_openid LIKE 'qq:%' ORDER BY created_ts DESC"):
            p = c.execute("SELECT name FROM trpg_players WHERE id=?", (r["player_id"],)).fetchone()
            cards = [x["name"] for x in c.execute("SELECT name FROM trpg_cards WHERE player_id=? ORDER BY id", (r["player_id"],))]
            out.append({"qq": r["member_openid"][3:], "player": (p["name"] if p and p["name"] else f"玩家{r['player_id']}"), "cards": cards})
        return out


def unbind_trpg(qq: str) -> bool:
    with get_conn() as c:
        return c.execute("DELETE FROM trpg_identities WHERE member_openid=?", ("qq:" + qq,)).rowcount > 0
