"""匿名使用情况上报：告诉作者「这台机器装着长日bot、是哪个版本、最近有没有人在用」，作者那边好知道装了的人是不是真在用。

发出去的只有：实例编号（第一次运行时本机随机生成，和 QQ、群、服务器都没关系）、版本、系统平台、
配了几个机器人、几个连着、上次上报以来处理了多少次指令（以及每个指令各几次，只有指令名，比如「ra 12」，
不带指令后面写的内容；只算长日bot 自带的指令，用户自己装的扩展不算）、开着哪些官方扩展、程序连续运行了多久、小手机开没开 / 在不在运行和它的端口号
（作者那边按这个从外面连一下，看玩家能不能打开小手机）。
不带群号、QQ 号、角色名、聊天内容。后台「基础」设置里「匿名使用情况上报」关掉就不发。
启动 2 分钟后发一次，之后每小时一次；连着的机器人比上次报的多了（比如刚在后台加了机器人）也补报一次。
发不出去就留到下次，不另外重试、不影响别的。还没报出去的指令次数每几分钟存到 data/usage_pending.json，
重启不会丢。
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
_loaded = False            # data/usage_pending.json（上次没报出去的数）读进来没有
_dirty = False             # 内存里的数和文件里的不一样，要存
_last_online = -1          # 上次报出去时连着几个机器人（还没报过是 -1）
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


def _pending_path():
    return DATA_DIR / "usage_pending.json"


def _ensure_loaded() -> None:
    """第一次用到时把上次没报出去的数（data/usage_pending.json）并进来。调用方要拿着 _lock。"""
    global _loaded, _cmds
    if _loaded:
        return
    _loaded = True
    try:
        d = json.loads(_pending_path().read_text(encoding="utf-8"))
        _cmds += max(0, int(d.get("cmds", 0)))
        for k, v in (d.get("by_cmd") or {}).items():
            k = str(k)[:CMD_NAME_MAX]
            if k and int(v) > 0:
                _by_name[k] = _by_name.get(k, 0) + int(v)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def save_pending() -> None:
    """把还没报出去的指令次数存进 data/usage_pending.json（没有就删掉文件），重启后接着算。定时调，出错就算了。"""
    global _dirty
    with _lock:
        _ensure_loaded()
        if not _dirty:
            return
        n, by = _cmds, dict(_by_name)
        _dirty = False
    p = _pending_path()
    try:
        if n <= 0:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cmds": n, "by_cmd": by}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
    except OSError:
        with _lock:
            _dirty = True


def count_command(name: str = "") -> None:
    """name 是玩家用的指令词（注册过的主名字或别名，不是整句话）；空的只算总数。"""
    global _cmds, _dirty
    name = str(name or "").strip()[:CMD_NAME_MAX]
    with _lock:
        _ensure_loaded()
        _cmds += 1
        _dirty = True
        if name:
            _by_name[name] = _by_name.get(name, 0) + 1


def _take_count() -> tuple[int, dict[str, int]]:
    global _cmds, _by_name, _dirty
    with _lock:
        _ensure_loaded()
        n, by, _cmds, _by_name = _cmds, _by_name, 0, {}
        _dirty = True
    return n, by


def _put_back(n: int, by: dict[str, int]) -> None:
    global _cmds, _dirty
    with _lock:
        _cmds += n
        _dirty = True
        for k, v in by.items():
            _by_name[k] = _by_name.get(k, 0) + v


def last_online() -> int:
    """上次报出去时连着几个机器人；还没报成功过是 -1。"""
    return _last_online


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
            "uptime": int(time.time() - _started), **phone_state()}


def phone_state() -> dict:
    """小手机：off = 没开（管理员关的，或还没有机器人）；down = 开着但没在运行；up = 在运行。查不了就不报。"""
    try:
        from . import phoneserver
        if not phoneserver.phone_bots():
            return {"phone": "off"}
        out = {"phone": "up" if phoneserver.running() else "down", "phone_port": phoneserver.port()}
        try:
            from . import httpsproxy
            if httpsproxy.enabled():                    # 开了 HTTPS：玩家走的是 https 端口（端口 + 1000），作者那边测这个
                out.update(phone_port=httpsproxy.phone_https_port(out["phone_port"]), phone_tls=1)
        except Exception:  # noqa: BLE001  查不了就按没开 HTTPS 报，小手机状态照样报
            pass
        return out
    except Exception:  # noqa: BLE001
        return {}


def report(bots: int, online: int, url: str = DEFAULT_URL) -> bool:
    """发一次。关着、发不出去都返回 False；发不出去时这段时间的指令数并回去，下次一起报。"""
    global _last_online
    if not enabled():
        _take_count()
        save_pending()
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
    if ok:
        _last_online = int(online)
    else:
        _put_back(cmds, by)
    save_pending()
    return ok
