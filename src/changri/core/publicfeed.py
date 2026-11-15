"""公开播报：机器人往公告群公开发的内容（礼物公开、心愿挂出、被吹散的心动信……）留一份底，小手机「公开播报」里能翻。
点歌那份直接读 song_requests（点歌扩展自己存了）。只记「发出去的那条文字」，不存发件人的私人信息。"""
import time

from .db import get_conn

KINDS = {"gift": "礼物", "wish": "心愿", "lovemail": "心动信", "other": "其他"}
KEEP = 500          # 最多留多少条，旧的自动清掉


def record(kind: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    kind = kind if kind in KINDS else "other"
    with get_conn() as c:
        c.execute("INSERT INTO public_feed(ts, kind, text) VALUES(?,?,?)", (int(time.time()), kind, text[:1500]))
        c.execute("DELETE FROM public_feed WHERE id <= (SELECT MAX(id) FROM public_feed) - ?", (KEEP,))


def recent(limit: int = 80) -> list[dict]:
    """旧 → 新。{"kind","ts","time","text","song"}，song 只有点歌条目才有。"""
    with get_conn() as c:
        rows = [dict(kind=r["kind"], ts=r["ts"], text=r["text"], song=None) for r in c.execute("SELECT * FROM public_feed ORDER BY id DESC LIMIT ?", (limit,))]
        try:
            for r in c.execute("SELECT * FROM song_requests WHERE announced=1 ORDER BY id DESC LIMIT ?", (limit,)):
                who = r["signer"] or "有人"
                to = r["to_name"] or "大家"
                rows.append(dict(kind="song", ts=r["ts"], text=r["message"] or "", song={"title": r["title"], "artist": r["artist"], "url": r["url"]}, head=f"{who} 点给 {to}"))
        except Exception:  # noqa: BLE001  点歌表还没有（老库）就当没有
            pass
    rows.sort(key=lambda x: x["ts"])
    rows = rows[-limit:]
    for x in rows:
        x["time"] = time.strftime("%m-%d %H:%M", time.localtime(x["ts"]))
    return rows


def last_line() -> str:
    r = recent(1)
    return (r[-1]["text"] if r and r[-1]["kind"] != "song" else (r[-1]["head"] if r else "")).replace("\n", " ")[:30]
