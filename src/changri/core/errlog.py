"""报错日志：程序里出的错、整个程序崩溃（没正常退出）都记下来，恢复之后自动发给「报错接收地址」。
玩家/管理员什么都不用做；后台「总览」最下面有一个默认折叠的「报错日志」可以看。
记在本机 data/errors.jsonl（只留最近 200 条）。发出去的内容只有：时间、种类、版本、实例编号（本机随机生成的，见 core/usage.py）、出错位置和报错文字，
并且把长得像密钥/口令的字符串抹掉；不带聊天内容、群号、QQ 号。
唯一的例外是装的人自己在后台「基础」里填的「联系 QQ」（选填）：填了才随报错一起发，方便作者看到报错后联系他。"""
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
_CODE_NAME = re.compile(r"_*[a-z][a-z0-9]*(?:_+[a-z0-9]+)+_*")   # 代码里的函数名（_get_authorization_header 这种 snake_case）不是密钥，不遮
_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")          # 群号、QQ 号、AppID 之类的长数字


def _path():
    return db.DB_PATH.parent / "errors.jsonl"


def _marker():
    return db.DB_PATH.parent / ".running"


def _mask(m) -> str:
    return m.group(0) if _CODE_NAME.fullmatch(m.group(0)) else "<…>"


def scrub(text: str) -> str:
    """遮掉密钥样的长串和长数字。不截长度——报错的真正原因在 trace 最后几行，由调用方决定留头还是留尾。"""
    return _DIGITS.sub("#", _SECRET.sub(_mask, text or ""))


def _version() -> str:
    try:
        from . import updater
        return updater.current_version()
    except Exception:  # noqa: BLE001
        return ""


def record(kind: str, source: str, message: str, trace: str = "") -> None:
    """记一条。kind: error（程序里出的错）/ crash（上次没正常退出）/ config（装的人要去改的配置问题，比如 IP 白名单）。"""
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


# ---------- 配置问题：IP 白名单 ----------
# QQ 开放平台要求把服务器的公网 IP 加进白名单。没加（或 IP 变了）时机器人收得到指令，但回话被 QQ 拒绝（401，code 11298），
# 群里的人看到的就是「机器人没反应」。这不是程序出错：单独记成一条「配置」，写清去哪里改；后台总览顶上也会提醒。

WHITELIST_SOURCE = "IP 白名单"
WHITELIST_MARKS = ("code=11298", "接口访问源IP不在白名单")
WHITELIST_TEXT = ("QQ 拒绝了机器人发消息：这台服务器的公网 IP 不在 QQ 开放平台的 IP 白名单里（没加，或者 IP 变了）。"
                  "请到 QQ 开放平台「开发管理 → 开发设置 → IP 白名单」加上这台服务器的公网 IP；"
                  "不知道 IP 是多少，就到设置平台「机器人接入」页点「检查连接」，它会告诉你。")
WHITELIST_QUIET = 600          # 同一个问题 10 分钟内只记一条（每条指令都会撞一次，不刷屏）
WHITELIST_SHOW = 1800          # 最近 30 分钟内撞过，后台总览才提醒；改好以后不再撞，提醒自己消失


def is_whitelist_error(text: str) -> bool:
    return any(m in (text or "") for m in WHITELIST_MARKS)


def whitelist_issue(now: float | None = None, within: int = WHITELIST_SHOW) -> dict | None:
    """最近有没有因为 IP 白名单发不出消息：有就返回那条记录，没有返回 None。"""
    now = now or time.time()
    for r in reversed(read_all()):
        if r.get("kind") == "config" and r.get("source") == WHITELIST_SOURCE:
            return r if now - r.get("ts", 0) <= within else None
    return None


def note_whitelist() -> bool:
    """记一条「IP 白名单没加对」。10 分钟内记过就不再记。返回这次有没有记。"""
    if whitelist_issue(within=WHITELIST_QUIET):
        return False
    record("config", WHITELIST_SOURCE, WHITELIST_TEXT)
    try:
        print("\n[长日bot] ⚠️ " + WHITELIST_TEXT + "\n", file=sys.stderr, flush=True)     # 看着窗口 / 日志的人也能直接看到
    except Exception:  # noqa: BLE001
        pass
    return True


def _loguru_sink(message) -> None:
    rec = message.record
    if rec["level"].no < 40:                       # 只收 ERROR 以上
        return
    exc = rec["exception"]
    trace = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback)) if exc else ""
    if is_whitelist_error(str(rec["message"])) or is_whitelist_error(trace):
        note_whitelist()                           # 配置问题：不记成「出错」，也不带一大段堆栈
        return
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


_QQ = re.compile(r"\d{5,12}")


def contact_qq() -> str:
    """装的人自己在后台「基础」里填的联系 QQ（选填）。没填、填得不像 QQ 号都返回空串 = 不带。
    设置是按机器人存的，这里没有「当前机器人」（启动时在后台线程里发），所以每个机器人的库都看一眼，取第一个填了的。"""
    from .db import get_config, list_bot_ids, use_bot
    vals = []
    try:
        vals.append(get_config("contact_qq", ""))
        for bid in list_bot_ids():
            with use_bot(bid):
                vals.append(get_config("contact_qq", ""))
    except Exception:  # noqa: BLE001 读不到就当没填，不能耽误发报错
        pass
    for v in vals:
        v = (v or "").strip()
        if _QQ.fullmatch(v):
            return v
    return ""


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
    payload = {"app": "changri_bot", "version": _version(), "install": inst, "errors": todo[-50:]}
    contact = contact_qq()
    if contact:                                   # 只有自己填了才带
        payload["contact"] = contact
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
