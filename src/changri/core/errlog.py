"""报错日志：程序里出的错、整个程序崩溃（没正常退出）都记下来，恢复之后自动发给「报错接收地址」。
玩家/管理员什么都不用做；后台「总览」最下面有一个默认折叠的「报错日志」可以看。
记在本机 data/errors.jsonl（只留最近 200 条）。发出去的内容只有：时间、种类、版本、实例编号（本机随机生成的，见 core/usage.py）、出错位置和报错文字，
并且把长得像密钥/口令的字符串抹掉；不带聊天内容、群号、QQ 号。"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.request

from . import db

KEEP = 200
DEFAULT_URL = "https://system.changri.work/api/errors"       # 主站（和更新服务同一个域名）；后台「基础」设置里可以改，清空 = 只存本机
_lock = threading.Lock()
_SECRET = re.compile(r"[A-Za-z0-9_\-]{24,}")
_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")          # 群号、QQ 号、AppID 之类的长数字


def _path():
    return db.DB_PATH.parent / "errors.jsonl"


def _marker():
    return db.DB_PATH.parent / ".running"


def scrub(text: str) -> str:
    return _DIGITS.sub("#", _SECRET.sub("<…>", text or ""))[:2000]


def _version() -> str:
    try:
        from . import updater
        return updater.current_version()
    except Exception:  # noqa: BLE001
        return ""


def record(kind: str, source: str, message: str, trace: str = "") -> None:
    """记一条。kind: error（程序里出的错）/ crash（上次没正常退出）。"""
    row = {"ts": int(time.time()), "kind": kind, "source": source[:120], "message": scrub(message)[:600],
           "trace": scrub(trace)[-1500:], "version": _version(), "reported": 0}
    with _lock:
        try:
            p = _path()
            p.parent.mkdir(parents=True, exist_ok=True)
            rows = read_all() + [row]
            p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[-KEEP:]), encoding="utf-8")
        except OSError:
            pass


def read_all() -> list[dict]:
    try:
        out = []
        for line in _path().read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
    except OSError:
        return []


def recent(n: int = 30) -> list[dict]:
    return list(reversed(read_all()[-n:]))


def clear() -> None:
    with _lock:
        try:
            _path().unlink()
        except OSError:
            pass


def _loguru_sink(message) -> None:
    rec = message.record
    if rec["level"].no < 40:                       # 只收 ERROR 以上
        return
    exc = rec["exception"]
    trace = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback)) if exc else ""
    record("error", f"{rec['name']}:{rec['function']}:{rec['line']}", str(rec["message"]), trace)


def install() -> None:
    """程序启动时调用一次：挂上错误收集，检查上次是不是崩溃退出的，然后（有地址的话）把没发的发出去。"""
    try:
        from loguru import logger
        logger.add(_loguru_sink, level="ERROR", enqueue=False, catch=True)
    except Exception:  # noqa: BLE001
        pass
    old_hook = sys.excepthook

    def hook(t, v, tb):
        record("error", "未捕获的异常", f"{t.__name__}: {v}", "".join(traceback.format_exception(t, v, tb)))
        old_hook(t, v, tb)
    sys.excepthook = hook
    threading.excepthook = lambda a: record("error", f"线程 {a.thread.name if a.thread else ''}", f"{a.exc_type.__name__}: {a.exc_value}",
                                            "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    m = _marker()
    try:
        if m.exists():                              # 上次启动后没有正常退出标记：崩溃了，或被强行结束
            record("crash", "进程", "上次运行没有正常退出（程序崩溃、被强行结束或机器断电）。")
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass
    import atexit
    atexit.register(mark_clean_exit)
    threading.Thread(target=report_pending, daemon=True).start()


def mark_clean_exit() -> None:
    try:
        _marker().unlink()
    except OSError:
        pass


def report_url() -> str:
    from .db import get_config
    return (get_config("error_report_url", DEFAULT_URL) or "").strip()


def report_pending() -> int:
    """把还没发的报错发给报错接收地址（后台「基础」设置里填；留空 = 只存本机）。返回发了几条。"""
    url = report_url()
    if not url.lower().startswith(("http://", "https://")):
        return 0
    with _lock:
        rows = read_all()
    todo = [r for r in rows if not r.get("reported")]
    if not todo:
        return 0
    try:
        from .usage import install_id
        inst = install_id()
    except Exception:  # noqa: BLE001
        inst = ""
    body = json.dumps({"app": "changri_bot", "version": _version(), "install": inst, "errors": todo[-50:]}, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "changri-bot"})
        with urllib.request.urlopen(req, timeout=8) as resp:        # noqa: S310
            ok = 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        return 0
    if ok:
        with _lock:
            rows = read_all()
            for r in rows:
                r["reported"] = 1
            _path().write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return len(todo)
    return 0
