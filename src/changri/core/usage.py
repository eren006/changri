"""匿名使用情况上报：告诉作者「这台机器装着长日bot、是哪个版本、最近有没有人在用」，作者那边好知道装了的人是不是真在用。

发出去的只有：实例编号（第一次运行时本机随机生成，和 QQ、群、服务器都没关系）、版本、系统平台、
配了几个机器人、几个连着、上次上报以来处理了多少次指令、程序连续运行了多久。
不带群号、QQ 号、角色名、聊天内容。后台「基础」设置里「匿名使用情况上报」关掉就不发。
跟着「检查更新」一起发（启动 2 分钟后一次，之后每 6 小时一次），发不出去就算了，不重试、不影响别的。
"""
from __future__ import annotations

import json
import platform
import secrets
import sys
import threading
import time
import urllib.request

from ..paths import DATA_DIR

DEFAULT_URL = "https://system.changri.work/api/ping"
_started = time.time()
_lock = threading.Lock()
_cmds = 0


def install_id() -> str:
    """这台机器的实例编号：data/install_id 里的 16 位随机十六进制，没有就生成一个。"""
    p = DATA_DIR / "install_id"
    try:
        v = p.read_text(encoding="utf-8").strip()
        if len(v) == 16 and all(c in "0123456789abcdef" for c in v):
            return v
    except OSError:
        pass
    v = secrets.token_hex(8)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v, encoding="utf-8")
    except OSError:
        pass
    return v


def count_command() -> None:
    global _cmds
    with _lock:
        _cmds += 1


def _take_count() -> int:
    global _cmds
    with _lock:
        n, _cmds = _cmds, 0
    return n


def enabled() -> bool:
    from .db import get_config
    return get_config("usage_report", "1") == "1"


def payload(bots: int, online: int, cmds: int) -> dict:
    from . import updater
    return {"install": install_id(), "version": updater.current_version(),
            "plat": f"{sys.platform}-{platform.machine().lower()}"[:40],
            "bots": int(bots), "online": int(online), "cmds": int(cmds),
            "uptime": int(time.time() - _started)}


def report(bots: int, online: int, url: str = DEFAULT_URL) -> bool:
    """发一次。关着、发不出去都返回 False；发不出去时这段时间的指令数并回去，下次一起报。"""
    if not enabled():
        _take_count()
        return False
    cmds = _take_count()
    body = json.dumps(payload(bots, online, cmds)).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "changri-bot"})
        with urllib.request.urlopen(req, timeout=8) as r:        # noqa: S310
            ok = 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        global _cmds
        with _lock:
            _cmds += cmds
    return ok
