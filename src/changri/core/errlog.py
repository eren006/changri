"""报错日志：程序里出的错、整个程序崩溃（没正常退出）都记下来，自动发给「报错接收地址」（启动时一次，之后每小时一次）。
玩家/管理员什么都不用做；后台「总览」最下面有一个默认折叠的「报错日志」可以看。
种类：crash 崩溃 / error 出错 / config 要装的人去改的配置 / notice 提醒（出了岔子但程序自己兜住了：发图失败改发文字、定时任务这一轮没做成、
和 QQ 的连接断了一下……，长日bot 自己代码里的 warning 都算，同一个问题 30 分钟只记一条）。
Python 自带 logging 记的也收（接到 loguru 上，见 _StdBridge），不只是 nonebot 的 logger。
记在本机 data/errors.jsonl：超过一天的自动清掉（要发的已经发出去了才清），最多留 200 条，满了先挤掉提醒。发出去的内容只有：时间、种类、版本、实例编号（本机随机生成的，见 core/usage.py）、出错位置和报错文字，
并且把长得像密钥/口令的字符串抹掉；不带聊天内容、群号、QQ 号。
唯一的例外是装的人自己在后台「基础」里填的「联系 QQ」（选填）：填了才随报错一起发，方便作者看到报错后联系他。"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import urllib.request

from . import db

KEEP = 200
RETAIN = 24 * 3600             # 超过一天的清掉（已经发出去的、或者没填接收地址的）
MAINTAIN_EVERY = 3600          # 每小时：把没发的发出去，再清一次旧的
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


def _trim(rows: list[dict]) -> list[dict]:
    """超过 KEEP 条：先挤掉最旧的提醒，还不够再挤最旧的其他。"""
    extra = len(rows) - KEEP
    if extra <= 0:
        return rows
    drop = set()
    for i, r in enumerate(rows):
        if len(drop) >= extra:
            break
        if r.get("kind") == "notice":
            drop.add(i)
    rows = [r for i, r in enumerate(rows) if i not in drop]
    return rows[-KEEP:]


def _write(rows: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def record(kind: str, source: str, message: str, trace: str = "", now: float | None = None) -> None:
    """记一条。kind: error（程序里出的错）/ crash（上次没正常退出）/ config（装的人要去改的配置问题，比如 IP 白名单）/ notice（提醒）。"""
    row = {"ts": int(now or time.time()), "kind": kind, "source": source[:120], "message": scrub(message)[:600],
           "trace": scrub(trace)[-1500:], "version": _version(), "reported": 0}
    with _lock:
        try:
            _write(_trim(read_all() + [row]))
        except OSError:
            pass


def prune(now: float | None = None) -> int:
    """清掉超过一天的。填了接收地址的，只清已经发出去的（没发出去的留着下次接着发）。返回清了几条。"""
    now = now or time.time()
    sending = report_url().lower().startswith(("http://", "https://"))
    with _lock:
        rows = read_all()
        keep = [r for r in rows if now - r.get("ts", 0) <= RETAIN or (sending and not r.get("reported"))]
        if len(keep) == len(rows):
            return 0
        try:
            _write(keep)
        except OSError:
            return 0
        return len(rows) - len(keep)


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


# ---------- 配置问题：按钮被 QQ 拒绝 ----------
# 带按钮的消息被拒时机器人自动退回纯文字、连续被拒才暂停按钮一会儿（core/qqsend.py），功能不受影响，所以以前只在窗口里打一行警告。
# 开机自启（没有窗口）的 Windows 用户就看不到为什么没按钮了：记成一条「配置」，带上 QQ 返回的原文，后台「报错日志」里能直接看。

BUTTON_SOURCE = "QQ 按钮"
BUTTON_QUIET = 600             # 10 分钟内只记一条


def note_button_rejected(where: str, err: str, now: float | None = None) -> bool:
    """记一条「带按钮的消息被 QQ 拒绝」。where：哪种消息（回复指令 / 主动推送……）。
    IP 白名单的问题不归这里（纯文字也会被拒，由 note_whitelist 管）。10 分钟内记过就不再记。返回这次有没有记。"""
    if is_whitelist_error(err):
        return False
    now = now or time.time()
    for r in reversed(read_all()):
        if r.get("kind") == "config" and r.get("source") == BUTTON_SOURCE:
            if now - r.get("ts", 0) < BUTTON_QUIET:
                return False
            break
    record("config", BUTTON_SOURCE,
           f"{where}带按钮的消息被 QQ 拒绝，这一条已改发纯文字；连续被拒 3 次会暂停按钮 10 分钟（玩家照文字提示手打指令，功能不受影响）。"
           f"QQ 返回：{(err or '').strip()[:300] or '（没有说明）'}。"
           "常见原因：机器人没有群 Markdown 权限，或没有开通自定义按钮；把这一条整段发给维护的人就能查。")
    return True


# ---------- 提醒：出了岔子但程序自己兜住了 ----------
# 长日bot 自己代码里的 warning（发图失败改发文字、定时任务这一轮出错、发到某个群失败……）记成「提醒」。
# 同一个问题（来源 + 去掉数字后的开头一段一样）30 分钟内只记一条，下一条带上中间又出现了几次，定时任务每分钟出错也不刷屏。

NOTICE_QUIET = 1800
NOTICE_SKIP = (                                   # 本来就是给装的人看的正常提示，或者已经由别处记过的
    "已用 .env 里的 ADMIN_PASSWORD", "首次设置：", "表单校验码对不上",
    "30 秒后再试",                                 # 掉线备案：自己会重试
    "[按钮] 被 QQ 拒绝", "[按钮] 主动消息带按钮被 QQ 拒绝",   # note_button_rejected 已记成「配置」
)
_notice_seen: dict[str, list[float]] = {}         # key → [上次记的时间, 之后又出现但没记的次数]
_NUM = re.compile(r"\d+")


def _notice_key(source: str, message: str) -> str:
    return source.split(":")[0] + "|" + _NUM.sub("#", _TAG.sub("", message))[:60]


def note_notice(source: str, message: str, trace: str = "", now: float | None = None) -> bool:
    """记一条提醒。30 分钟内同样的记过就只数次数。返回这次有没有记。"""
    if any(m in (message or "") for m in NOTICE_SKIP):
        return False
    now = now or time.time()
    key = _notice_key(source, message)
    with _lock:
        seen = _notice_seen.get(key)
        if seen and now - seen[0] < NOTICE_QUIET:
            seen[1] += 1
            return False
        more = seen[1] if seen else 0
        _notice_seen[key] = [now, 0]
        if len(_notice_seen) > 500:                # 不让它无限长
            for k in sorted(_notice_seen, key=lambda k: _notice_seen[k][0])[:250]:
                _notice_seen.pop(k, None)
    text = _TAG.sub("", message).strip()
    if more:
        text += f"（之前 30 分钟里同样的还出现了 {more} 次）"
    record("notice", source, text, trace, now=now)
    return True


# ---------- 临时断线：偶尔一条记成提醒 ----------
# 机器人和 QQ 服务器之间的连接偶尔会断（网络抖一下、QQ 那边重启连接），QQ 适配器会自己重连，但它把这些都记成 ERROR。
# 偶尔一条不算出错：记成「提醒」（30 分钟一条）。30 分钟内反复出现 FLAP_LIMIT 次以上才合成一条「出错」（之后 30 分钟内不再记），那多半是网络或配置真有问题。

FLAP_MARKS = ("WebSocket Closed", "Failed to get gateway info", "Received invalid session event",
              "Error while setup websocket", "Error while process data from websocket", "Failed to establish connection to QQ")
FLAP_WINDOW = 1800
FLAP_LIMIT = 5
_flaps: list[float] = []
_flap_reported = [0.0]
_TAG = re.compile(r"</?[a-z#][^>]*>")


def is_flap(text: str) -> bool:
    return any(m in (text or "") for m in FLAP_MARKS)


def note_flap(message: str, trace: str = "", now: float | None = None) -> bool:
    """临时断线来了一条。返回这次有没有记成「出错」（偶尔一条记成提醒，不算）。"""
    now = now or time.time()
    last = _TAG.sub("", message).strip()
    with _lock:
        _flaps[:] = [t for t in _flaps if now - t < FLAP_WINDOW] + [now]
        n = len(_flaps)
        escalate = n >= FLAP_LIMIT and now - _flap_reported[0] >= FLAP_WINDOW
        if escalate:
            _flap_reported[0] = now
            _flaps.clear()
    if not escalate:
        note_notice("QQ 连接", f"和 QQ 服务器的连接断了一下，会自己重连（{last}）。偶尔一次不用管。", now=now)
        return False
    record("error", "QQ 连接", f"30 分钟内和 QQ 服务器的连接断开 / 重连了 {n} 次（最近一次：{last}）。偶尔断一下会自己重连，"
                              "这么频繁多半是这台服务器的网络不稳，或者 AppSecret、IP 白名单不对。", trace)
    return True


_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # .../changri


def _own_code(path: str) -> bool:
    """是不是长日bot 自己的代码（第三方库的 warning 不收，太吵）。"""
    try:
        return os.path.abspath(path or "").startswith(_PKG_DIR + os.sep)
    except Exception:  # noqa: BLE001
        return False


def _loguru_sink(message) -> None:
    rec = message.record
    level = rec["level"].no
    if level < 30:
        return
    extra = rec.get("extra") or {}
    src = extra.get("std_src") or f"{rec['name']}:{rec['function']}:{rec['line']}"    # 从 Python logging 转过来的，用原来的位置
    path = extra.get("std_path") or getattr(rec.get("file"), "path", "")
    exc = rec["exception"]
    trace = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback)) if exc else ""
    text = str(rec["message"])
    if is_whitelist_error(text) or is_whitelist_error(trace):
        note_whitelist()                           # 配置问题：不记成「出错」，也不带一大段堆栈
        return
    if is_flap(text):
        note_flap(text, trace)                     # 临时断线：偶尔一条记成提醒，反复出现才记成出错
        return
    if level >= 40:
        record("error", src, text, trace)
    elif _own_code(path):
        note_notice(src, text, trace)


class _StdBridge(logging.Handler):
    """把 Python 自带 logging 的日志转给 loguru：窗口里照样能看到，报错日志也能收到（以前 logging 记的错一条都收不到）。
    uvicorn 的日志 nonebot 已经自己转过了，跳过免得记两遍。"""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("uvicorn"):
            return
        try:
            from loguru import logger
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            logger.bind(std_src=f"{record.name}:{record.funcName}:{record.lineno}", std_path=record.pathname) \
                .opt(exception=record.exc_info).log(level, record.getMessage())
        except Exception:  # noqa: BLE001
            self.handleError(record)


def _bridge_std_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, _StdBridge) for h in root.handlers):
        root.addHandler(_StdBridge())


def _maintain_loop() -> None:
    """启动时发一次，之后每小时：把没发的报错发出去，再清掉超过一天的。"""
    while True:
        try:
            report_pending()
            prune()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(MAINTAIN_EVERY)


def install() -> None:
    """程序启动时调用一次：挂上错误收集，检查上次是不是崩溃退出的，然后（有地址的话）把没发的发出去，之后每小时一次。"""
    try:
        from loguru import logger
        logger.add(_loguru_sink, level="WARNING", enqueue=False, catch=True)
        _bridge_std_logging()
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
    threading.Thread(target=_maintain_loop, name="errlog-maintain", daemon=True).start()


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
    base = {"app": "changri_bot", "version": _version(), "install": inst}
    contact = contact_qq()
    if contact:                                   # 只有自己填了才带
        base["contact"] = contact
    sent = 0
    for batch in _batches(base, todo):            # 分批发：接收端一次最多收 50 条、64KB；以前超了整批被拒、或者没发的也被标成已发
        body = json.dumps({**base, "errors": batch}, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "changri-bot"})
            with urllib.request.urlopen(req, timeout=8) as resp:        # noqa: S310
                ok = 200 <= resp.status < 300
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            break
        done = {(r["ts"], r["source"], r["message"]) for r in batch}
        with _lock:
            rows = read_all()
            for r in rows:
                if (r.get("ts"), r.get("source"), r.get("message")) in done:
                    r["reported"] = 1
            _write(rows)
        sent += len(batch)
    return sent


BATCH_ITEMS = 50
BATCH_BYTES = 48 * 1024                           # 接收端上限 64KB，留点余量


def _batches(base: dict, todo: list[dict]):
    """按条数和大小切批，旧的先发。单条太大的把堆栈截短再发。"""
    head = len(json.dumps({**base, "errors": []}, ensure_ascii=False).encode("utf-8"))
    batch, size = [], head
    for r in todo:
        n = len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1
        if head + n > BATCH_BYTES:
            r = {**r, "trace": r.get("trace", "")[-500:]}
            n = len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1
        if batch and (len(batch) >= BATCH_ITEMS or size + n > BATCH_BYTES):
            yield batch
            batch, size = [], head
        batch.append(r)
        size += n
    if batch:
        yield batch
