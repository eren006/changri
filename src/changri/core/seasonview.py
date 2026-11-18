"""复盘网页：一季结束后的总结页，照 RP 端（rp_archive）的公开视图 /view/<token> 做——一个链接看整季：
主页（本季总数、角色、按天的卡片）→ 某一天的全部场次和短信礼物心动信 → 某一场的戏文（可下载 TXT），另有「互动」总页和每个角色的场次。

- 数据来自季度档案（core/seasonarchive.py：结束季度、收尾时各存一次），所以「收尾」清空以后链接照样能看。
- 每季一个链接：「结束季度」时自动打开，把链接发到后台群（没设后台群就不发，不往公告群发）；管理员要的话自己转发。
  管理员也可以发「复盘网页」（再发一次到后台群）、「复盘网页 关闭」、「复盘网页 换链接」（旧链接失效），后台「季度与出场 → 往季」里也能开关。
- 只读、不用登录；token 是 32 位随机串。只放结束了的场次，只放戏文（TXT 里带着闲聊、标了「闲聊」）。
- 短信、礼物、心动信能在后台「复盘网页」设置里分别关掉；心动信只显示署名，不显示真实寄信人。
- 页面挂在小手机的服务上（/v/…），要开着小手机、填了小手机的访问网址。
"""
import re
import secrets
import time

from . import seasonarchive
from .db import get_config, get_conn, set_config

KIND_TAG = {"约会": "private", "心愿": "private", "踩点": "private", "官约": "official", "电话": "call", "前置电话": "call", "微信": "call"}
NO_TIME = {"微信", "前置电话"}


# ---------- 链接 ----------

def token_of(season_id: int) -> str:
    return get_config(f"sv_token:{season_id}", "")


def publish(season_id: int, new_link: bool = False) -> str:
    """打开这一季的复盘网页，返回 token（已有就沿用；new_link=True 换一个，旧的失效）。"""
    old = token_of(season_id)
    if old and not new_link:
        set_config(f"sv_tok:{old}", str(season_id))
        return old
    if old:
        set_config(f"sv_tok:{old}", "")
    t = secrets.token_hex(16)
    set_config(f"sv_token:{season_id}", t)
    set_config(f"sv_tok:{t}", str(season_id))
    return t


def unpublish(season_id: int) -> None:
    old = token_of(season_id)
    if old:
        set_config(f"sv_tok:{old}", "")


def is_published(season_id: int) -> bool:
    t = token_of(season_id)
    return bool(t) and get_config(f"sv_tok:{t}", "") == str(season_id)


def season_for(tok: str) -> int | None:
    """token → 季度 id；没打开、没档案都返回 None。"""
    if not re.fullmatch(r"[0-9a-f]{32}", tok or ""):
        return None
    sid = get_config(f"sv_tok:{tok}", "")
    if not sid.isdigit() or not secrets.compare_digest(token_of(int(sid)), tok):
        return None
    return int(sid) if int(sid) in seasonarchive.archived_ids() else None


def url(season_id: int) -> str:
    """这一季复盘网页的链接；没打开或没填小手机访问网址时返回空串。"""
    base = get_config("phone_base_url", "").rstrip("/")
    return f"{base}/v/{token_of(season_id)}" if base and is_published(season_id) else ""


def why_no_url() -> str:
    from . import extensions
    if get_config("phone_enabled", "0") != "1" or not extensions.is_enabled("phone"):
        return "复盘网页挂在小手机的服务上，要先开小手机（后台「小手机」页）。"
    if not get_config("phone_base_url", ""):
        return "还没填小手机的访问网址（后台「小手机」页），生成不了链接。"
    return ""


def latest_ended() -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM seasons WHERE status='ended' ORDER BY COALESCE(ended_ts,0) DESC, id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def season_name(season_id: int) -> str:
    with get_conn() as c:
        r = c.execute("SELECT name FROM seasons WHERE id=?", (season_id,)).fetchone()
    return r["name"] if r else "本季"


def message(season_id: int) -> str:
    """发到后台群的那一条。"""
    u = url(season_id)
    if not u:
        return f"📖 「{season_name(season_id)}」的复盘网页没生成：{why_no_url() or '先发「复盘网页」打开。'}"
    return (f"📖 「{season_name(season_id)}」复盘网页（整季总结：按天看场次、戏文、短信礼物心动信，每场能下载 TXT；只读、不用登录）：\n{u}\n"
            f"需要的话转发给大家。「复盘网页 关闭」关掉，「复盘网页 换链接」让这个链接失效。")


# ---------- 内容（从季度档案读） ----------

def _shown(kind: str) -> bool:
    return get_config(f"season_view_{kind}", "1") == "1"


def _room_item(r: dict) -> dict:
    rp = [x for x in r["lines"] if x.get("rp")]
    label = r.get("day_label") or (f"D{r['day']}" if r.get("day") is not None else time.strftime("%m-%d", time.localtime(r["created_ts"])))
    num = r.get("day_num", r["day"] if r.get("day") is not None else 10 ** 6)
    return {"type": "room", "id": r["id"], "kind": r["kind"], "tag": KIND_TAG.get(r["kind"], "private"), "place": r.get("place") or "",
            "who": r["members"], "time": "" if r["kind"] in NO_TIME else f"{r['start_hm']}-{r['end_hm']}", "story": "",
            "ts": r["created_ts"], "ended_ts": r.get("ended_ts") or (rp[-1]["ts"] if rp else r["created_ts"]),
            "day_num": num, "day_label": label, "rp_n": len(rp), "rp_chars": sum(_chars(x) for x in rp), "raw": r}


def _chars(line: dict) -> int:
    if "chars" in line:
        return line["chars"] or 0
    t = line["text"]
    return len(t.split("：", 1)[1]) if "：" in t[:12] else len(t)        # 老档案没存字数：去掉「名字：」估一下


def load(season_id: int) -> list[dict]:
    """这一季：结束了的场次 + 设置里开着的短信 / 礼物 / 心动信，按天和先后排好。"""
    arc = seasonarchive.get(season_id)
    if not arc:
        return []
    out = [_room_item(r) for r in arc["rooms"] if r["status"] == "ended"]
    out += [dict(e) for e in arc["summary"].get("events", []) if _shown(e["type"])]
    out.sort(key=lambda e: (e["day_num"], e["ts"]))
    return out


def days(evts: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for e in evts:
        d = by.setdefault(e["day_label"], {"label": e["day_label"], "num": e["day_num"], "rooms": 0, "replies": 0, "chars": 0,
                                           "sms": 0, "gift": 0, "lovemail": 0, "first_ts": e["ts"]})
        d["first_ts"] = min(d["first_ts"], e["ts"])
        if e["type"] == "room":
            d["rooms"] += 1; d["replies"] += e["rp_n"]; d["chars"] += e["rp_chars"]
        else:
            d[e["type"]] += 1
    return sorted(by.values(), key=lambda d: d["num"])


def totals(evts: list[dict]) -> dict:
    rs = [e for e in evts if e["type"] == "room"]
    return {"rooms": len(rs), "replies": sum(e["rp_n"] for e in rs), "chars": sum(e["rp_chars"] for e in rs),
            "roles": sorted({n for e in rs for n in e["who"]}),
            **{k: sum(1 for e in evts if e["type"] == k) for k in ("sms", "gift", "lovemail")}}


def room(season_id: int, room_id: int) -> dict | None:
    e = next((x for x in load(season_id) if x["type"] == "room" and x["id"] == room_id), None)
    if not e:
        return None
    from .roomlog import body_of
    rp = [{"name": x["name"], "text": body_of(x["name"], x["text"]), "ts": x["ts"]} for x in e["raw"]["lines"] if x.get("rp")]
    per: dict[str, dict] = {}
    for x in e["raw"]["lines"]:
        if x.get("rp"):
            s = per.setdefault(x["name"], {"replies": 0, "chars": 0})
            s["replies"] += 1; s["chars"] += _chars(x)
    return {**e, "rp": rp, "per": per, "dur": _dur(e["ended_ts"] - e["ts"])}


def room_text(season_id: int, room_id: int) -> str:
    e = room(season_id, room_id)
    return seasonarchive.room_text(season_name(season_id), e["raw"]) if e else ""


def all_text_zip(season_id: int) -> bytes:
    """整季每场一个 TXT，打成 zip（后台「往季」页下载）。"""
    import io
    import zipfile
    arc = seasonarchive.get(season_id)
    name = season_name(season_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in (arc["rooms"] if arc else []):
            if r["status"] != "ended":
                continue
            it = _room_item(r)
            fname = f"{it['day_label']}_R{r['id']}_{r['kind']}" + (f"_{r['place']}" if r.get("place") else "") + ".txt"
            z.writestr(re.sub(r'[\\/:*?"<>|]', "_", fname), seasonarchive.room_text(name, r))
    return buf.getvalue()


def _dur(sec: int) -> str:
    if not sec or sec <= 0:
        return ""
    h, m = divmod(sec // 60, 60)
    return f"{h} 小时 {m} 分" if h else f"{m} 分钟"


def fmt_ts(ts) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else ""
