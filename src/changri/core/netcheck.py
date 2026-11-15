"""网络体检：机器人连上了吗 / 小手机在监听吗 / 玩家能从外网打开吗 / 有没有 HTTPS。
「玩家能从外网打开吗」靠主页服务器（system.changri.work）反向连一下这台服务器的小手机端口——
探测服务只会连发起请求的那个 IP，不接收别人指定的地址（tools/probe_service.py）。"""
import json
import socket
import time

from .botconf import _http, public_ip
from .db import get_config, get_int

PROBE_URL = "https://system.changri.work/api/probe"
ONLINE_WITHIN = 90


def bot_online() -> bool:
    from .botconf import configured_bot_ids
    now = time.time()
    for bot_id in configured_bot_ids():
        try:
            if now - float(get_config(f"heartbeat:{bot_id}", "0")) <= ONLINE_WITHIN:
                return True
        except ValueError:
            pass
    return False


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def probe_outside(port: int) -> dict:
    """返回 {state: ok|closed|timeout|not_phone|unreachable, msg}。"""
    try:
        status, text = _http(f"{PROBE_URL}?port={port}", timeout=15)
        data = json.loads(text)
    except ValueError:      # 对方不是检测服务（返回了网页）：检测服务还没上线
        return {"state": "unreachable", "msg": "检测服务暂时不可用，没法自动判断。你可以用手机（关掉 WiFi 用流量）直接打开小手机网址试试：能打开就说明通了。"}
    except Exception:  # noqa: BLE001
        return {"state": "unreachable", "msg": "没连上检测服务，稍后再试；不影响使用。你也可以用手机（关掉 WiFi 用流量）直接打开小手机网址试试。"}
    state = data.get("state", "unreachable")
    msg = {
        "ok": "玩家可以从外网打开小手机。",
        "closed": "外网连不上这个端口：云服务商的安全组还没放行，或这台服务器的防火墙拦住了。",
        "timeout": "外网连接超时：多半是云服务商的安全组没放行这个端口。",
        "not_phone": "端口通了，但打开的不是小手机，可能被别的程序占用了，换一个端口试试。",
    }.get(state, data.get("msg", "没有得到结果"))
    return {"state": state, "msg": msg}


def lights(port: int, outside: dict | None) -> list[dict]:
    """四个灯：state 是 ok / bad / unknown。"""
    base = get_config("phone_base_url", "")
    out_state = "unknown" if not outside else ("ok" if outside["state"] == "ok" else ("unknown" if outside["state"] == "unreachable" else "bad"))
    return [
        {"name": "机器人连上了吗", "state": "ok" if bot_online() else "bad", "hint": "" if bot_online() else "到「机器人」页检查 AppID/密钥和 IP 白名单。"},
        {"name": "小手机在监听吗", "state": "ok" if listening(port) else "bad", "hint": "" if listening(port) else "小手机没有启动，可能被关掉了，或端口被占用。"},
        {"name": "玩家能从外网打开吗", "state": out_state, "hint": outside["msg"] if outside else "点「检查」才会知道。"},
        {"name": "有没有 HTTPS", "state": "ok" if base.startswith("https://") else "unknown", "hint": "" if base.startswith("https://") else "可选。没有也能用，只是浏览器会提示「不安全」。"},
    ]


def default_base_url() -> str:
    ip = public_ip()
    from .phoneserver import port
    return f"http://{ip}:{port()}" if ip else ""


_ip_cache = {"ip": "", "ts": 0.0}


def public_ip_cached() -> str:
    """页面上显示用，10 分钟内不重复联网。"""
    if time.time() - _ip_cache["ts"] > 600:
        _ip_cache.update(ip=public_ip(), ts=time.time())
    return _ip_cache["ip"]
