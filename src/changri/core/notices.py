"""作者的通知：作者在自己的监控后台写，机器人跟着「检查更新」一起拉（启动 2 分钟后一次，之后每 6 小时一次），
存在 data/notices.json；管理员打开后台时弹一次（点了「知道了」这个浏览器就不再弹），总览页上一直留着，直到作者撤下。

拉的时候不带任何信息（跟下载 latest.json 一样只是一个 GET），所以「匿名使用情况上报」关了也照样拉。
只收纯文字，页面上按文字显示，不会当成 HTML。拉不到就留着上次的，不重试、不影响别的。
"""
from __future__ import annotations

import json
import os
import urllib.request

from ..paths import DATA_DIR

DEFAULT_URL = "https://system.changri.work/api/notices"
TITLE_MAX, BODY_MAX, KEEP_MAX = 40, 1000, 3
_FILE = "notices.json"


def _path():
    return DATA_DIR / _FILE


def clean(data) -> list[dict] | None:
    """主站回的 JSON → 通知列表；格式不对返回 None（这时不动本地那份）。"""
    if not isinstance(data, dict) or not isinstance(data.get("notices"), list):
        return None
    out = []
    for n in data["notices"]:
        if not isinstance(n, dict):
            continue
        nid, ts, title, body = n.get("id"), n.get("ts"), n.get("title"), n.get("body", "")
        if not isinstance(nid, int) or isinstance(nid, bool) or not isinstance(title, str) or not isinstance(body, str):
            continue
        title, body = title.strip()[:TITLE_MAX], body.strip()[:BODY_MAX]
        if title:
            out.append({"id": nid, "ts": ts if isinstance(ts, int) and not isinstance(ts, bool) else 0, "title": title, "body": body})
    return out[:KEEP_MAX]


def fetch(url: str = DEFAULT_URL, timeout: float = 8) -> bool:
    """拉一次，拉到了就覆盖本地那份（作者撤下的就跟着没了）。成功返回 True。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "changri-bot"})
        with urllib.request.urlopen(req, timeout=timeout) as r:        # noqa: S310
            if r.status != 200:
                return False
            got = clean(json.loads(r.read(64 * 1024).decode("utf-8")))
    except Exception:  # noqa: BLE001
        return False
    if got is None:
        return False
    save(got)
    return True


def save(items: list[dict]) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def current() -> list[dict]:
    """本地存着的通知，新的在前；没有或读不了就是空的。"""
    try:
        return clean({"notices": json.loads(_path().read_text(encoding="utf-8"))}) or []
    except (OSError, ValueError):
        return []
