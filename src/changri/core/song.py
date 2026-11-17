"""点歌（额外扩展）：规则、解析和查歌都在这里，发送由 plugins/song.py 负责。

两种点法：
1. 回复一张网易云 / QQ 音乐的分享卡片，发「点歌 [给 某人|给大家] [寄语]」（也认旧格式「点歌人：… 留言：… 送给：…」）。
   官方机器人收到回复时，被引用的卡片会以文字形式出现在 msg_elements[].content 里（tag/title/desc/jump_url 各一行），
   2026-11-15 在线上服务器实测过，网易云和 QQ 音乐都有。
2. 「点歌 [QQ/网易] 歌名 [歌手] [给 某人|给大家] [寄语]」：机器人自己去搜，取最像的一首。

公告群收到一张图文卡片（msg_type 8，点开跳到歌曲页）；卡片发不出去时退成带链接的文字。
查歌用网易云 / QQ 音乐网页版的老接口，都不是官方开放接口，对方一改就可能失效——失效时按歌名点会报「暂时搜不到」，
回复卡片点歌仍然能用（卡片自带歌名和链接，只是可能没有封面）。
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import season
from .db import get_conn, get_int

PLATFORM_NAMES = {"163": "网易云音乐", "qq": "QQ音乐", "bili": "哔哩哔哩"}
_UA = "Mozilla/5.0 (changri-bot)"
_TIMEOUT = 6


# ---------- 次数与记录 ----------

def daily_limit() -> int:
    return get_int("song_daily_limit", 3)


def day_count(role_id: int) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM song_requests WHERE from_id=? AND day=?", (role_id, season.day_key())).fetchone()[0]


def limit_reached(role_id: int) -> str:
    cap = daily_limit()
    if cap > 0 and day_count(role_id) >= cap:
        return f"🎵 今天的点歌次数用完了（{cap}/{cap}）。"
    return ""


def record(from_id: int, req: dict, song: dict, source: str) -> int:
    with get_conn() as c:
        return c.execute(
            "INSERT INTO song_requests(from_id, signer, to_name, platform, song_id, title, artist, url, cover, message, source, day, ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (from_id, req.get("signer", ""), req.get("to", ""), song.get("platform", ""), str(song.get("id") or ""),
             song["title"], song.get("artist", ""), song.get("url", ""), song.get("cover", ""), req.get("message", ""),
             source, season.day_key(), int(time.time()))).lastrowid


def mark_announced(req_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE song_requests SET announced=1 WHERE id=?", (req_id,))


def delete(req_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM song_requests WHERE id=?", (req_id,))


def recent(limit: int = 50) -> list:
    with get_conn() as c:
        return c.execute("SELECT s.*, r.name AS from_name FROM song_requests s LEFT JOIN roles r ON r.id=s.from_id"
                         " ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()


# ---------- 指令参数 ----------

_OLD_FIELDS = ("点歌人", "留言", "歌名", "送给")
_OLD_RE = re.compile(r"(点歌人|留言|歌名|送给)\s*[:：]\s*(.*?)(?=\s*(?:点歌人|留言|歌名|送给)\s*[:：]|$)", re.S)


def parse_args(text: str) -> dict:
    """「[QQ/网易] 歌名 [给 某人|给大家] [寄语]」或旧格式「点歌人：… 留言：… 歌名：… 送给：…」
    → {platform, keyword, to, message, signer}。to 为空 = 给大家。"""
    text = (text or "").strip()
    out = {"platform": None, "keyword": "", "to": "", "message": "", "signer": ""}
    if any(re.search(f"{f}\\s*[:：]", text) for f in _OLD_FIELDS):
        fields = {k: v.strip() for k, v in _OLD_RE.findall(text)}
        out.update(signer=fields.get("点歌人", ""), message=fields.get("留言", ""),
                   keyword=fields.get("歌名", ""), to=fields.get("送给", ""))
    else:
        tokens = text.split()
        if tokens and re.fullmatch(r"(?i)qq|qq音乐", tokens[0]):
            out["platform"], tokens = "qq", tokens[1:]
        elif tokens and tokens[0] in ("网易", "网易云", "网易云音乐"):
            out["platform"], tokens = "163", tokens[1:]
        i = next((k for k, t in enumerate(tokens) if t in ("给", "给大家") or (t.startswith("给") and len(t) > 1 and k > 0)), -1)
        if i >= 0:
            t = tokens[i]
            if t == "给大家":
                out["message"] = " ".join(tokens[i + 1:])
            elif t == "给":
                out["to"] = tokens[i + 1] if i + 1 < len(tokens) else ""
                out["message"] = " ".join(tokens[i + 2:])
            else:                                            # 「给张三」连着写
                out["to"] = t[1:]
                out["message"] = " ".join(tokens[i + 1:])
            tokens = tokens[:i]
        out["keyword"] = " ".join(tokens)
    if out["to"] in ("大家", "所有人"):
        out["to"] = ""
    return out


# ---------- 被回复的分享卡片 ----------

_LINE = re.compile(r"^\s*(tag|title|desc|jump_url)\s*[:：]\s*(.*?)\s*$", re.M)


def parse_card_text(text: str) -> dict | None:
    """被回复卡片的文字版（「[卡片消息] 图文H5 / tag: / title: / desc: / jump_url:」）→ 歌曲信息；不是音乐卡片返回 None。"""
    if not text:
        return None
    f = {k: v for k, v in _LINE.findall(text)}
    return card_to_song(f)


def card_to_song(fields: dict) -> dict | None:
    """ark_data.fields（或从文字版拆出来的同名字段）→ {platform, id, mid, title, artist, url, cover}。"""
    title, url = (fields.get("title") or "").strip(), (fields.get("jump_url") or "").strip()
    if not title or not url:
        return None
    tag = fields.get("tag") or ""
    song = {"platform": "", "id": "", "mid": "", "title": title, "artist": (fields.get("desc") or "").strip(),
            "url": url, "cover": (fields.get("preview") or "").strip()}
    host = urllib.parse.urlparse(url).hostname or ""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "163" in host or "网易云" in tag:
        song["platform"] = "163"
        m = re.search(r"[?&]id=(\d+)", url) or re.search(r"/song/(\d+)", url)
        song["id"] = m.group(1) if m else ""
    elif host.endswith("qq.com") or "QQ音乐" in tag:
        song["platform"] = "qq"
        song["id"] = (q.get("songid") or [""])[0]
        m = re.search(r"songDetail/(\w+)", url)
        song["mid"] = (q.get("songmid") or [""])[0] or (m.group(1) if m else "")
    elif not url.startswith("https://"):
        return None                                   # 别的平台的卡片：链接不是 https 的发不了图文卡片，也没法查，算了
    if song["platform"] and not (song["id"] or song["mid"]):
        song["platform"] = ""
    return song


def card_from_event(event) -> dict | None:
    """从回复事件里找被引用的音乐卡片：先看原始的 ark_data，没有就拆 content 文字。"""
    for el in getattr(event, "msg_elements", None) or []:
        ark = getattr(el, "ark_data", None) or (el.get("ark_data") if isinstance(el, dict) else None)
        if isinstance(ark, dict) and ark.get("fields"):
            s = card_to_song(ark["fields"])
            if s:
                return s
        content = getattr(el, "content", None) or (el.get("content") if isinstance(el, dict) else "")
        s = parse_card_text(content or "")
        if s:
            return s
    return None


# ---------- 查歌（网易云 / QQ 音乐网页版老接口） ----------

def _get_json(url: str, params: dict, referer: str, post: bool = False) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + qs, data=b"" if post else None,
                                 headers={"Referer": referer, "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _https(u: str) -> str:
    return ("https://" + u[len("http://"):]) if u.startswith("http://") else u


def song_url(platform: str, song_id: str, mid: str = "", fallback: str = "") -> str:
    if platform == "163" and song_id:
        return f"https://music.163.com/song?id={song_id}"
    if platform == "qq" and mid:
        return f"https://y.qq.com/n/ryqq/songDetail/{mid}"
    return fallback


def _netease_detail(song_id: str) -> dict | None:
    data = _get_json("https://music.163.com/api/song/detail/", {"ids": json.dumps([int(song_id)])}, "https://music.163.com/")
    for sg in data.get("songs") or []:
        al = sg.get("album") or {}
        return {"platform": "163", "id": str(sg["id"]), "mid": "", "title": sg.get("name") or "",
                "artist": " / ".join(a.get("name", "") for a in sg.get("artists") or [] if a.get("name")),
                "cover": _https(al.get("picUrl") or "")}
    return None


def _qq_detail(song_id: str = "", mid: str = "") -> dict | None:
    params = {"format": "json", **({"songmid": mid} if mid else {"songid": song_id})}
    data = _get_json("https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg", params, "https://y.qq.com/")
    items = data.get("data") or []
    if not items:
        return None
    sg, al = items[0], items[0].get("album") or {}
    return {"platform": "qq", "id": str(sg.get("id") or song_id), "mid": sg.get("mid") or mid,
            "title": sg.get("name") or sg.get("title") or "",
            "artist": " / ".join(a.get("name", "") for a in sg.get("singer") or [] if a.get("name")),
            "cover": f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{al['mid']}.jpg" if al.get("mid") else ""}


def lookup(song: dict) -> dict:
    """卡片里拿到的歌：按平台补全封面、规整链接。查不到就原样返回（卡片自带的歌名和链接照用）。"""
    out = dict(song)
    try:
        d = None
        if song["platform"] == "163" and song.get("id"):
            d = _netease_detail(song["id"])
        elif song["platform"] == "qq" and (song.get("id") or song.get("mid")):
            d = _qq_detail(song.get("id", ""), song.get("mid", ""))
        if d:
            out["cover"] = out.get("cover") or d["cover"]
            out["id"], out["mid"] = d["id"] or out.get("id", ""), d["mid"] or out.get("mid", "")
            out["artist"] = out.get("artist") or d["artist"]
    except Exception:  # noqa: BLE001 —— 查不到不影响点歌
        pass
    out["url"] = song_url(out["platform"], out.get("id", ""), out.get("mid", ""), fallback=out.get("url", ""))
    return out


def _netease_search(q: str) -> list[dict]:
    data = _get_json("https://music.163.com/api/cloudsearch/pc", {"s": q, "type": 1, "limit": 10, "offset": 0},
                     "https://music.163.com/", post=True)
    out = []
    for sg in (data.get("result") or {}).get("songs") or []:
        al = sg.get("al") or {}
        out.append({"platform": "163", "id": str(sg["id"]), "mid": "", "title": sg.get("name") or "",
                    "artist": " / ".join(a.get("name", "") for a in sg.get("ar") or [] if a.get("name")),
                    "cover": _https(al.get("picUrl") or "")})
    return out


def _qq_search(q: str) -> list[dict]:
    data = _get_json("https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg",
                     {"key": q, "format": "json", "inCharset": "utf-8", "outCharset": "utf-8"}, "https://y.qq.com/")
    mids = [it["mid"] for it in (((data.get("data") or {}).get("song") or {}).get("itemlist") or []) if it.get("mid")]
    return [d for d in (_qq_detail(mid=m) for m in mids[:5]) if d]


def best_match(keyword: str, songs: list[dict]) -> dict | None:
    """按「歌名对得上、歌手写了也对得上、不是翻唱」挑最像的一首。
    歌名和关键词互不包含的不要（比如网易云没有周杰伦的版权，搜「晴天 周杰伦」会出来别的歌），返回 None 让调用方换平台。"""
    kw = keyword.replace(" ", "").lower()

    def base(t: str) -> str:                          # 「L O V E (《天生一对》主题曲)」→「love」
        return re.sub(r"[（(\[【].*?[)）\]】]", "", t).replace(" ", "").lower()

    def score(i_s):
        i, s = i_s
        t = base(s["title"])
        sc = -i
        if t == kw or (t and kw.startswith(t)):
            sc += 50
        elif t and t in kw:
            sc += 40
        if any(a and a.lower().replace(" ", "") in kw for a in s["artist"].split(" / ")):
            sc += 20
        if re.search(r"原唱|翻唱|cover|伴奏|dj|remix|live", s["title"].lower()):
            sc -= 15
        return sc

    cands = [(i, s) for i, s in enumerate(songs) if base(s["title"]) and (base(s["title"]) in kw or kw in base(s["title"]))]
    return max(cands, key=score)[1] if cands else None


def _artist_hit(keyword: str, s: dict) -> bool:
    kw = keyword.replace(" ", "").lower()
    return any(a and a.lower().replace(" ", "") in kw for a in s["artist"].split(" / "))


def search(keyword: str, platform: str | None = None) -> dict | None:
    """按歌名搜；不写平台先搜 QQ 音乐（实测它的联想按热度排，原唱靠前；网易云前几条常是翻唱），
    关键词里写了歌手却没对上，再去网易云找。接口出错抛 RuntimeError。"""
    keyword = keyword.strip()[:40]
    order = [platform] if platform else ["qq", "163"]
    failed, fallback = 0, None
    for pf in order:
        try:
            songs = _netease_search(keyword) if pf == "163" else _qq_search(keyword)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        hit = best_match(keyword, songs)
        if not hit:
            continue
        hit["url"] = song_url(hit["platform"], hit["id"], hit["mid"])
        if _artist_hit(keyword, hit) or len(keyword.split()) < 2:     # 只写了歌名，或歌手也对上了：就它
            return hit
        fallback = fallback or hit
    if fallback:
        return fallback
    if failed == len(order):
        raise RuntimeError("暂时搜不到歌，音乐平台那边可能出问题了，稍后再试；也可以回复一张音乐分享卡片来点歌。")
    return None


# ---------- 哔哩哔哩：贴链接或 BV 号 ----------
# B 站分享到 QQ 是小程序卡片，里面只有「bilibili」和一张图，没有标题和链接（2026-11-15 实测），所以回复卡片点不了，
# 只能让玩家「分享 → 复制链接」贴过来，或者直接写 BV 号。视频接口不带访客 cookie 会 412，先去 spi 拿一个 buvid3。

_BILI_LINK = re.compile(r"https?://(?:b23\.tv|bili2233\.cn)/[\w-]+|https?://(?:www\.|m\.)?bilibili\.com/video/[\w]+[^\s]*")
_BV = re.compile(r"(?<![\w])(BV1[0-9A-Za-z]{9})(?![\w])")
_AV = re.compile(r"(?<![\w])av(\d{1,12})(?![\w])", re.I)
_bili_cookie: dict = {}


def find_bili(text: str) -> tuple[str, str]:
    """文字里的 B 站链接 / BV 号 / av 号 → (找到的那一段, 去掉它和分享标题【…】之后剩下的话)；没有返回 ("", 原文)。"""
    text = text or ""
    m = _BILI_LINK.search(text) or _BV.search(text) or _AV.search(text)
    if not m:
        return "", text
    rest = (text[:m.start()] + " " + text[m.end():])
    rest = re.sub(r"【.*】", " ", rest)                  # B 站「复制链接」带的「【视频标题-哔哩哔哩】」
    return m.group(0), " ".join(rest.split())


def _bili_headers() -> dict:
    now = time.time()
    if not _bili_cookie or now - _bili_cookie.get("at", 0) > 6 * 3600:
        try:
            d = _get_json("https://api.bilibili.com/x/frontend/finger/spi", {}, "https://www.bilibili.com/")
            _bili_cookie.update(b3=d["data"]["b_3"], at=now)
        except Exception:  # noqa: BLE001
            _bili_cookie.update(b3="", at=now - 6 * 3600 + 300)      # 拿不到 5 分钟后再试
    h = {"Referer": "https://www.bilibili.com/", "User-Agent": _UA}
    if _bili_cookie.get("b3"):
        h["Cookie"] = f"buvid3={_bili_cookie['b3']}"
    return h


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _resolve_short(url: str) -> str:
    """b23.tv 短链接 → 跳转地址。只读 Location、不真的打开视频页（线上服务器打开 www.bilibili.com 会被 412 拦）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=_TIMEOUT) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400 and e.headers.get("Location"):
            return e.headers["Location"]
        raise


def bili_lookup(found: str) -> dict | None:
    """链接 / BV 号 / av 号 → 视频信息（当成一首歌）；不是视频或查不到返回 None，接口出错抛 RuntimeError。"""
    try:
        if re.match(r"https?://(?:b23\.tv|bili2233\.cn)/", found):
            found = _resolve_short(found)
        bv, av = _BV.search(found), re.search(r"(?:/video/av|^av)(\d+)", found, re.I)
        if not (bv or av):
            return None
        params = {"bvid": bv.group(1)} if bv else {"aid": av.group(1)}
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(params),
                                     headers=_bili_headers())
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("暂时查不到这个 B 站视频，稍后再试。") from e
    v = data.get("data") or {}
    if data.get("code") != 0 or not v.get("bvid"):
        return None
    return {"platform": "bili", "id": v["bvid"], "mid": "", "title": v.get("title") or "",
            "artist": (v.get("owner") or {}).get("name") or "",
            "url": f"https://www.bilibili.com/video/{v['bvid']}", "cover": _https(v.get("pic") or "")}


def quoted_text(event) -> str:
    """被回复的那条消息的文字（玩家可能回复的是别人贴的 B 站链接）。"""
    return "\n".join(str(getattr(el, "content", None) or (el.get("content") if isinstance(el, dict) else "") or "")
                     for el in getattr(event, "msg_elements", None) or [])


# ---------- 发到公告群的内容 ----------

def headline(req: dict) -> str:
    who = req.get("signer") or "有人"
    to = f"点给 {req['to']}" if req.get("to") else "点给大家"
    return f"{who}{to}" + (f"：{req['message']}" if req.get("message") else "")


def card_body(req: dict, song: dict) -> dict | None:
    """图文卡片（msg_type 8）的 card 字段；缺封面或链接不是 https 的发不了，返回 None。"""
    url, pic = song.get("url", ""), song.get("cover", "")
    if not (url.startswith("https://") and pic.startswith("https://")):
        return None
    title = f"🎵 {song['title']}" + (f" - {song['artist']}" if song.get("artist") else "")
    return {"type": "tuwen", "content": {"title": title[:50], "description": headline(req)[:200],
                                         "url": url[:1024], "pic_url": pic[:1024]}}


def text_body(req: dict, song: dict, with_link: bool = True) -> str:
    pf = PLATFORM_NAMES.get(song.get("platform", ""), "")
    lines = [f"🎵 {headline(req)}", f"《{song['title']}》" + (f" - {song['artist']}" if song.get("artist") else "")]
    if with_link and song.get("url"):
        lines.append(f"🔗 {song['url']}")
    elif pf:
        lines.append(f"（去{pf}搜一搜就能听到）")
    return "\n".join(lines)
