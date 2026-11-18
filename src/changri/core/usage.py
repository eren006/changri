"""匿名使用情况上报：告诉作者「这台机器装着长日bot、是哪个版本、最近有没有人在用」，作者那边好知道装了的人是不是真在用。

发出去的只有：实例编号（第一次运行时本机随机生成，和 QQ、群、服务器都没关系）、版本、系统平台、
配了几个机器人、几个连着、上次上报以来处理了多少次指令（以及每个指令各几次，只有指令名，比如「ra 12」，
不带指令后面写的内容；只算长日bot 自带的指令，用户自己装的扩展不算）、开着哪些官方扩展、程序连续运行了多久。
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
import urllib.error
import urllib.request

from ..paths import DATA_DIR

DEFAULT_URL = "https://system.changri.work/api/ping"
_started = time.time()
_lock = threading.Lock()
_cmds = 0
_by_name: dict[str, int] = {}
CMD_NAME_MAX = 16          # 指令名最多几个字
CMD_KINDS_MAX = 80         # 一次最多报多少种指令（按次数取前面的）


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


def count_command(name: str = "") -> None:
    """name 是玩家用的指令词（注册过的主名字或别名，不是整句话）；空的只算总数。"""
    global _cmds
    name = str(name or "").strip()[:CMD_NAME_MAX]
    with _lock:
        _cmds += 1
        if name:
            _by_name[name] = _by_name.get(name, 0) + 1


def _take_count() -> tuple[int, dict[str, int]]:
    global _cmds, _by_name
    with _lock:
        n, by, _cmds, _by_name = _cmds, _by_name, 0, {}
    return n, by


def _put_back(n: int, by: dict[str, int]) -> None:
    global _cmds
    with _lock:
        _cmds += n
        for k, v in by.items():
            _by_name[k] = _by_name.get(k, 0) + v


def official_exts_on() -> list[str]:
    """每个机器人开着的官方扩展（名字，比如「跑团骰子」）合在一起；出错就当没有。"""
    try:
        from .db import list_bot_ids, use_bot
        from .extensions import enabled_official
        out: set[str] = set()
        for bid in list_bot_ids():
            with use_bot(bid):
                out |= {e["name"] for e in enabled_official()}
        return sorted(out)
    except Exception:  # noqa: BLE001
        return []


def enabled() -> bool:
    from .db import get_config
    return get_config("usage_report", "1") == "1"


def payload(bots: int, online: int, cmds: int, by_name: dict[str, int] | None = None,
            exts: list[str] | None = None) -> dict:
    from . import updater
    top = sorted((by_name or {}).items(), key=lambda kv: -kv[1])[:CMD_KINDS_MAX]
    return {"install": install_id(), "version": updater.current_version(),
            "plat": f"{sys.platform}-{platform.machine().lower()}"[:40],
            "bots": int(bots), "online": int(online), "cmds": int(cmds),
            "by_cmd": dict(top), "exts": list(exts or []),
            "uptime": int(time.time() - _started)}


def report(bots: int, online: int, url: str = DEFAULT_URL) -> bool:
    """发一次。关着、发不出去都返回 False；发不出去时这段时间的指令数并回去，下次一起报。"""
    if not enabled():
        _take_count()
        return False
    cmds, by = _take_count()
    body = json.dumps(payload(bots, online, cmds, by, official_exts_on()), ensure_ascii=False).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "changri-bot"})
        with urllib.request.urlopen(req, timeout=8) as r:        # noqa: S310
            ok = 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500 and e.code != 429:          # 主站不收（格式不对、太大）：这段的数就不要了，免得越攒越大
            return False
        ok = False
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        _put_back(cmds, by)
    return ok
