"""小手机的公网监听：所有机器人共用一个端口（默认 5092），网址都是 /p。开关/端口在设置平台改完立刻生效，不用重启。

一个请求属于哪个机器人（按顺序看）：
  1. 网址里带 ?b=<AppID>：设置平台发出的「以 TA 打开」「管理员手机」一次性链接
  2. cookie phone_bot：登录成功时写上（见 phone/app.py 的 after_request）
  3. cookie phone_auth_<AppID 后 6 位>：老版本登录留下的
  4. 在 /p 提交激活码：在所有开着小手机的机器人里找这个码
  5. 都没有：第一个开着小手机的机器人（只开了一个机器人时就是它）
认出来以后，请求交给那个机器人自己的小手机应用，在它的工作区里处理；登录 cookie 仍在各自的工作区里校验，改 cookie 换不了身份。
"""
from __future__ import annotations

import io
import socket
import sys
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from . import db as _db
from .db import GLOBAL_CONFIG_PREFIXES, get_config, list_bot_ids, set_config, use_bot

_lock = threading.Lock()
_start_lock = threading.RLock()          # 启停整个过程串行：开机、QQ 号掉线/恢复、后台保存可能同时调 start()，不能各起一个 uvicorn 抢同一个端口
_server: dict = {}                      # {"server", "thread", "port"}
_apps: dict[str, object] = {}           # AppID → 这个机器人的小手机（WSGI）
DEFAULT_PORT = 5092
PORT_KEY = "phonesrv_port"              # 全局设置（GLOBAL_CONFIG_PREFIXES 里有 phonesrv_）
BOT_COOKIE = "phone_bot"
assert PORT_KEY.startswith(GLOBAL_CONFIG_PREFIXES)


def _admin_port() -> int:
    from .. import config
    return config.admin_port()


# ---------- 端口 ----------

def port() -> int:
    """小手机端口。老版本每个机器人各有 phone_port：第一次读到时取第一个开着小手机的机器人的端口，之后存成全局的。"""
    v = get_config(PORT_KEY, "")
    if v.isdigit():
        return int(v)
    legacy = DEFAULT_PORT
    for bid in phone_bots() or [b for b in list_bot_ids(False) if b]:
        with use_bot(bid):
            p = get_config("phone_port", "")
        if p.isdigit():
            legacy = int(p)
            break
    set_config(PORT_KEY, str(legacy))
    _retarget_base_urls(legacy)
    return legacy


def port_of(app_id: str = "") -> int:      # noqa: ARG001  兼容老调用：现在所有机器人同一个端口
    return port()


def set_port(p: int) -> None:
    set_config(PORT_KEY, str(int(p)))
    _retarget_base_urls(int(p))


def _retarget_base_urls(p: int) -> None:
    """各机器人发给玩家的网址：http://主机:端口 的，端口换成共用端口（https 的由 httpsproxy 管）。"""
    import re
    for bid in list_bot_ids(False):
        if not bid:
            continue
        with use_bot(bid):
            base = get_config("phone_base_url", "")
            new = re.sub(r"^(http://[^/:]+)(:\d+)?(?=/|$)", rf"\g<1>:{p}", base)
            if new != base:
                set_config("phone_base_url", new)


def _bindable(p: int) -> bool:
    with socket.socket() as s:
        if not sys.platform.startswith("win"):
            # 和 uvicorn 一样开 SO_REUSEADDR：刚关掉的端口（TIME_WAIT）uvicorn 能用，这里也不能误报「被占用」。
            # Windows 上这个选项意思不同（能抢别人正在用的端口），不开。
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", p))
            return True
        except OSError:
            return False


def suggest_port() -> int:
    """从 5092 往后找第一个本机没被别的程序占的端口（正在用的自己的端口也算可用）。"""
    mine = _server.get("port")
    for p in range(DEFAULT_PORT, DEFAULT_PORT + 200):
        if p != _admin_port() and (p == mine or _bindable(p)):
            return p
    return DEFAULT_PORT


def check_port(value: str) -> str:
    """能用返回空串，不能用返回原因。"""
    value = str(value).strip()
    if not (value.isdigit() and 1024 <= int(value) <= 65535):
        return "端口要在 1024–65535 之间"
    p = int(value)
    if p == _admin_port():
        return "不能和设置平台的端口相同"
    if p != _server.get("port") and not _bindable(p):
        return f"{p} 端口被这台机器上别的程序占用了，换一个（建议 {suggest_port()}）"
    return ""


# ---------- 哪些机器人开着小手机 ----------

_bots_cache: tuple[float, list[str]] = (0.0, [])


def phone_bots(fresh: bool = False) -> list[str]:
    global _bots_cache
    now = time.monotonic()
    if not fresh and now - _bots_cache[0] < 3:
        return _bots_cache[1]
    from . import extensions
    out = []
    for bid in list_bot_ids(True):
        if not bid and not _db.LEGACY_SINGLE:          # 单库模式（测试）里机器人 ID 是空串
            continue
        with use_bot(bid):
            if get_config("phone_enabled", "0") == "1" and extensions.is_enabled("phone"):
                out.append(bid)
    _bots_cache = (now, out)
    return out


# ---------- 分发 ----------

def _bot_app(app_id: str):
    app = _apps.get(app_id)
    if app is None:
        from changri.admin import _secret_key
        from changri.phone.app import create_phone_app
        app = _apps[app_id] = create_phone_app(_secret_key())
    return app


def _bot_for_code(code: str, bots: list[str]) -> str:
    from . import phonecodes
    code = code.strip().upper()
    if not code:
        return ""
    for bid in bots:
        with use_bot(bid):
            if phonecodes.role_for_code(code):
                return bid
    return ""


def pick_bot(environ: dict, bots: list[str]) -> str | None:
    """返回机器人 AppID；没有机器人开着小手机返回 None。"""
    if not bots:
        return None
    b = (parse_qs(environ.get("QUERY_STRING", "")).get("b") or [""])[0]
    if b in bots:
        return b
    jar = SimpleCookie()
    try:
        jar.load(environ.get("HTTP_COOKIE", ""))
    except Exception:  # noqa: BLE001  坏 cookie 当没有
        jar = SimpleCookie()
    if BOT_COOKIE in jar and jar[BOT_COOKIE].value in bots:
        return jar[BOT_COOKIE].value
    for bid in bots:
        if f"phone_auth_{bid[-6:]}" in jar:
            return bid
    if environ.get("REQUEST_METHOD") == "POST" and environ.get("PATH_INFO", "") in ("/p", "/p/"):
        try:
            n = min(int(environ.get("CONTENT_LENGTH") or 0), 64 * 1024)
        except ValueError:
            n = 0
        body = environ["wsgi.input"].read(n) if n else b""
        environ["wsgi.input"] = io.BytesIO(body)          # 读过的内容放回去，后面 Flask 还要读
        code = (parse_qs(body.decode("utf-8", "replace")).get("code") or [""])[0]
        if found := _bot_for_code(code, bots):
            return found
    return bots[0]


def dispatch(environ, start_response):
    from .db import enter_bot, leave_bot
    bots = phone_bots()
    bid = pick_bot(environ, bots)
    if bid is None:
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return ["小手机还没开启".encode()]
    tok = enter_bot(bid)
    try:
        return list(_bot_app(bid)(environ, start_response))
    finally:
        leave_bot(tok)


def _build_asgi():
    from a2wsgi import WSGIMiddleware

    from changri.admin.remote import LocalProxyFix
    return WSGIMiddleware(LocalProxyFix(dispatch))         # 经 Caddy（HTTPS）转来的请求认出真实来源 IP


# ---------- 启停 ----------

def stop(app_id: str = "") -> None:
    """某个机器人被移除/删除：丢掉它的小手机应用；没有机器人开着小手机了就关掉监听。"""
    _apps.pop(app_id, None)
    if app_id:
        start()
        return
    with _start_lock:
        _stop_server()


def _stop_server() -> None:
    with _lock:
        st = dict(_server)
        _server.clear()
    if st:
        st["server"].should_exit = True
        st["thread"].join(timeout=5)


def start(app_id: str = "") -> str:  # noqa: ARG001  兼容老调用
    """按当前设置启动/重启/关闭监听。返回给界面看的一句话。"""
    with _start_lock:
        return _start()


def _serve(server, p: int) -> None:
    """uvicorn 绑不上端口会 sys.exit(3)：在这里接住，记一条看得懂的话，不让「SystemExit: 3」刷屏。"""
    try:
        server.run()
    except SystemExit:
        from . import errlog
        errlog.record("error", "线程 phone-web", f"小手机没能在 {p} 端口启动：端口被占用（可能是上一个进程还没退干净，或别的程序占着）", "")


def _start() -> str:
    import uvicorn

    if not phone_bots(fresh=True):
        _stop_server()
        return "小手机已关闭"
    p = port()
    with _lock:
        if _server and _server["port"] == p and _server["thread"].is_alive():
            return f"小手机在 {p} 端口运行"
    _stop_server()
    if not _bindable(p):                                     # 端口被别的程序占着：不起 uvicorn（起了也只会 SystemExit: 3）
        return f"没能在 {p} 端口启动（被别的程序占用了），换一个端口试试"
    server = uvicorn.Server(uvicorn.Config(_build_asgi(), host="0.0.0.0", port=p, log_level="warning"))
    server.install_signal_handlers = lambda: None          # 只有主线程能装信号处理
    thread = threading.Thread(target=_serve, args=(server, p), name="phone-web", daemon=True)
    thread.start()
    for _ in range(30):                                      # 最多等 3 秒，看有没有真的起来（端口被占会很快退出）
        time.sleep(0.1)
        if server.started or not thread.is_alive():
            break
    if not thread.is_alive():
        return f"没能在 {p} 端口启动（可能被别的程序占用了），换一个端口试试"
    if not server.started:                                   # 3 秒还没起来但线程还活着：照样记下来，下次 start() 能先把它关掉，不会再起一个抢端口
        with _lock:
            _server.update(server=server, thread=thread, port=p)
        return f"小手机正在 {p} 端口启动，稍等几秒刷新看看"
    with _lock:
        _server.update(server=server, thread=thread, port=p)
    return f"小手机已在 {p} 端口运行"


def player_url() -> str:
    """玩家打开小手机的网址（…/p）。还没填网址时（0.22.4 起默认打开，没人去后台点过「开启」）按这台机器的公网 IP 和端口补上，存下来以后不再查。"""
    base = get_config("phone_base_url", "").rstrip("/")
    if not base:
        from . import netcheck
        base = netcheck.default_base_url().rstrip("/")
        if base:
            set_config("phone_base_url", base)
    return f"{base}/p" if base else ""


def running() -> bool:
    """小手机的网页现在是不是由这个进程在跑（不看端口上是谁：别的程序占着 5092 时这里是 False）。"""
    with _lock:
        return bool(_server) and _server["thread"].is_alive() and _server["port"] == port()


def start_all() -> None:
    start()


def ensure() -> None:
    """该开没开、该关没关时，按设置启停一次（机器人同步每 5 秒调一次）。
    起因：新装时开机那一刻还没有机器人，start() 判「小手机已关闭」；之后在向导里加了机器人，机器人几秒内自动连上，
    小手机却一直没监听，后台还显示开着，要重启长日bot 才好。增删、停用机器人，端口被别的程序占着后又放开，都靠这里跟上。"""
    if bool(phone_bots(fresh=True)) != running():
        start()
