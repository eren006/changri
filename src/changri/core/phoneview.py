"""小手机里一条短信/礼物归到哪个会话（照老系统 rp_archive 的 _phone_view_of）：
- 自己发的：挂在收件人名下（真名会话）。
- 收到的：按落款认人。没署名、或署名就是发件人自己的名字 → 发件人的会话；
  署名正好是某个角色的名字 → 并进那个角色的会话（落款可能被换成别人，按落款显示，不多透露）；
  署名不是任何角色 → 单独一个以署名命名的会话（不能回复，回不到真名上，免得穿帮）。
- 匿名对话（alias_id 不为空）的短信不进这里，在匿名对话里显示（core/aliases.py）。
- 另有两个系统会话：「点歌台」（点歌的人和被点的人各看到一条）、「偷盗记录」（偷的人、被偷的人各看到按身份写的那句话）。"""
import time

from .db import get_conn

SONG_THREAD = "点歌台"
STEAL_THREAD = "偷盗记录"


def _names() -> tuple[dict[int, str], dict[str, int]]:
    with get_conn() as c:
        rows = c.execute("SELECT id, name FROM roles").fetchall()
    return {r["id"]: r["name"] for r in rows}, {r["name"]: r["id"] for r in rows}


def key_of(m, rid: int, by_id: dict, by_name: dict):
    """这条消息在 rid 手机里归哪个会话：('r', 角色id) / ('s', 署名)；不在 rid 手机里返回 None。"""
    if m["alias_id"]:
        return None
    if m["from_role_id"] == rid:
        return ("r", m["to_role_id"])
    if m["to_role_id"] != rid:
        return None
    sign = (m["sign"] or "").strip()
    if not sign or sign == by_id.get(m["from_role_id"]):
        return ("r", m["from_role_id"])
    if sign in by_name:
        return ("r", by_name[sign]) if by_name[sign] != rid else ("s", sign)
    return ("s", sign)


def _mine_or_received(rid: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM messages WHERE (from_role_id=? OR to_role_id=?) AND alias_id IS NULL ORDER BY id DESC", (rid, rid)).fetchall()


def _preview(m) -> str:
    return ((m["content"] or "［图片］") if m["kind"] == "sms" else f"［礼物］{m['title'] or m['content']}")[:40]


def inbox(role, song_on: bool = False, steal_on: bool = False) -> list[dict]:
    """会话列表：{name, preview, ts, url_kind, key}。url_kind：'r'（角色）/'s'（署名）/'alias'/'song'/'steal'。新的在前。"""
    from . import aliases
    rid = role["id"]
    by_id, by_name = _names()
    out: dict = {}
    for m in _mine_or_received(rid):
        k = key_of(m, rid, by_id, by_name)
        if not k or k in out:
            continue
        out[k] = {"name": by_id.get(k[1], "?") if k[0] == "r" else k[1], "preview": _preview(m), "ts": m["created_ts"], "url_kind": k[0], "key": k[1]}
    rows = list(out.values())
    for t in aliases.threads(rid):
        rows.append({"name": t["title"], "preview": t["preview"], "ts": t["ts"], "url_kind": "alias", "key": t["alias_id"]})
    if song_on:
        s = song_items(role)
        if s:
            rows.append({"name": SONG_THREAD, "preview": s[-1]["text"][:40], "ts": s[-1]["ts"], "url_kind": "song", "key": ""})
    if steal_on:
        st = steal_items(rid)
        if st:
            rows.append({"name": STEAL_THREAD, "preview": st[-1]["text"][:40], "ts": st[-1]["ts"], "url_kind": "steal", "key": ""})
    from . import precall
    if precall.is_on():                                     # 前置电话的电话：跟普通对话一样排在里面，名字后面挂「前置电话」标签（照老系统）
        for x in precall.sessions_for(rid):
            rows.append({"name": x["name"], "preview": ("已挂断 · " if x["ended"] else "") + (("我：" if x["last_mine"] else "") + x["last"] if x["last"] else "刚配对，说句话吧")[:40],
                         "ts": x["ts"], "url_kind": "precall", "key": x["id"], "tag": "前置电话"})
    rows.sort(key=lambda r: -r["ts"])
    for r in rows:
        r["time"] = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
    return rows


def thread_messages(rid: int, other_id: int) -> list:
    """和某个角色的会话（按落款认人之后属于这个会话的消息），旧的在前。"""
    by_id, by_name = _names()
    return [m for m in reversed(_mine_or_received(rid)) if key_of(m, rid, by_id, by_name) == ("r", other_id)]


def sign_messages(rid: int, sign: str) -> list:
    """以署名命名的会话（署名不是任何角色）：只有收到的，不能回复。"""
    by_id, by_name = _names()
    return [m for m in reversed(_mine_or_received(rid)) if key_of(m, rid, by_id, by_name) == ("s", sign)]


def song_items(role) -> list[dict]:
    """点歌台：我点给别人的（mine）、别人点给我的（显示署名，没署名就是匿名）。旧的在前。"""
    from . import charprofile
    me = {role["name"], charprofile.get(role["id"])["short"]} - {""}
    with get_conn() as c:
        rows = c.execute(f"SELECT * FROM song_requests WHERE to_name!='' AND (from_id=? OR to_name IN ({','.join('?' * len(me))})) ORDER BY id",
                         (role["id"], *me)).fetchall()                    # 点给大家的只进公开播报（照老系统）
    out = []
    for r in rows:
        mine = r["from_id"] == role["id"]
        song = f"《{r['title']}》" + (f"（{r['artist']}）" if r["artist"] else "")
        if mine:
            text = f"你给 {r['to_name'] or '大家'} 点了一首{song}" + (f"：{r['message']}" if r["message"] else "")
        else:
            text = f"{r['signer'] or '有人'} 给你点了一首{song}" + (f"：{r['message']}" if r["message"] else "")
        out.append({"mine": mine, "text": text, "ts": r["ts"], "url": r["url"], "song_id": r["id"]})
    return out


def steal_items(rid: int) -> list[dict]:
    from . import gifts
    return [{"mine": False, "text": x["text"], "ts": x["ts"]} for x in reversed(gifts.steal_log(rid, 200)) if x["text"]]
