"""小手机的公网监听：每个机器人一个端口、各管各的。开关/端口在设置平台改完立刻生效，不用重启机器人。"""
import os
import socket
import threading
import time

from .db import current_bot, get_config, list_bot_ids, use_bot

_lock = threading.Lock()
_state: dict[str, dict] = {}           # AppID → {server, thread, port}
DEFAULT_PORT = 5092


def _build_app(app_id: str):
    from a2wsgi import WSGIMiddleware

    from admin import _secret_key
    from core.db import enter_bot, leave_bot
    from phone.app import create_phone_app

    flask_app = create_phone_app(_secret_key())

    def in_workspace(environ, start_response):         # 这个端口上的所有请求都落在这个机器人的工作区
        tok = enter_bot(app_id)
        try:
            return list(flask_app(environ, start_response))
        finally:
            leave_bot(tok)

    return WSGIMiddleware(in_workspace)


def port_of(app_id: str) -> int:
    with use_bot(app_id):
        try:
            return int(get_config("phone_port", str(DEFAULT_PORT)) or DEFAULT_PORT)
        except ValueError:
            return DEFAULT_PORT


def ports_in_use(exclude: str = "") -> dict[int, str]:
    """别的机器人已经占着（配置里写了端口）的端口 → 谁占的。"""
    out = {}
    for bid in list_bot_ids(False):
        if bid and bid != exclude:
            with use_bot(bid):
                if get_config("phone_enabled", "0") == "1":
                    out[port_of(bid)] = bid
    return out


def _bindable(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def suggest_port(app_id: str = "") -> int:
    """从 5092 往后找第一个没被别的机器人占、本机也没被别的程序占的端口。"""
    app_id = app_id or current_bot()
    taken = set(ports_in_use(app_id)) | {int(os.getenv("PORT", "5091"))}
    mine = _state.get(app_id, {}).get("port")
    for p in range(DEFAULT_PORT, DEFAULT_PORT + 200):
        if p not in taken and (p == mine or _bindable(p)):
            return p
    return DEFAULT_PORT


def check_port(app_id: str, port: str) -> str:
    """能用返回空串，不能用返回原因。"""
    if not (port.isdigit() and 1024 <= int(port) <= 65535):
        return "端口要在 1024–65535 之间"
    p = int(port)
    if p == int(os.getenv("PORT", "5091")):
        return "不能和设置平台的端口相同"
    other = ports_in_use(app_id).get(p)
    if other:
        return f"这个端口已经给机器人 {other[-4:]} 的小手机用了，换一个（建议 {suggest_port(app_id)}）"
    return ""


def stop(app_id: str = "") -> None:
    app_id = app_id or current_bot()
    with _lock:
        st = _state.pop(app_id, None)
    if st:
        st["server"].should_exit = True
        st["thread"].join(timeout=5)


def start(app_id: str = "") -> str:
    """按这个机器人当前的设置启动（已在跑且端口没变就什么都不做）。返回给界面看的一句话。"""
    import uvicorn

    app_id = app_id or current_bot()
    with use_bot(app_id):
        on = get_config("phone_enabled", "0") == "1"
    if not on:
        stop(app_id)
        return "小手机已关闭"
    port = port_of(app_id)
    with _lock:
        st = _state.get(app_id)
        if st and st["port"] == port and st["thread"].is_alive():
            return f"小手机在 {port} 端口运行"
    stop(app_id)
    server = uvicorn.Server(uvicorn.Config(_build_app(app_id), host="0.0.0.0", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None          # 只有主线程能装信号处理
    thread = threading.Thread(target=server.run, name=f"phone-web-{app_id[-4:]}", daemon=True)
    thread.start()
    for _ in range(30):                                      # 最多等 3 秒，看有没有真的起来（端口被占会很快退出）
        time.sleep(0.1)
        if server.started or not thread.is_alive():
            break
    if not thread.is_alive() or not server.started:
        return f"没能在 {port} 端口启动（可能被别的程序占用了），换一个端口试试"
    with _lock:
        _state[app_id] = {"server": server, "thread": thread, "port": port}
    return f"小手机已在 {port} 端口运行"


def start_all() -> None:
    for bid in list_bot_ids(True):
        if bid:
            start(bid)
