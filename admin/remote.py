"""从公网打开设置平台：默认关。开了也有三道防护——
1) 只认「允许的 IP」名单里的来源，名单为空就一个人都不放进来（其他人看到的是 404，连登录页都看不到）；
2) 危险操作（机器人密钥、账号、更新、装/删/开关扩展、重启、这个页面本身）公网一律拒绝，只能在服务器本机或隧道里做；
3) 登录仍有按 IP 的失败锁定。
注意：这里是 http 明文，名单只是缩小暴露面，想完全放心请配 HTTPS 域名。"""
import ipaddress
import re

from core.db import get_config

_P = r"^(?:/b/\d+)?"          # 可以带「/b/机器人AppID」前缀
BLOCKED = [re.compile(_P + p) for p in (
    r"/bots(/|$)", r"/accounts(/|$)", r"/update(/|$)", r"/remote(/|$)",
    r"/extensions/(upload|toggle|restart)$", r"/extensions/[^/]+/delete$",
)]
LOCAL = {"127.0.0.1", "::1"}


def parse_allow(text: str) -> list:
    """「1.2.3.4, 5.6.7.0/24」→ 网段列表；写错的忽略。"""
    out = []
    for part in re.split(r"[\s,，;；]+", text or ""):
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            pass
    return out


def allowed(ip: str, nets: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def is_blocked_path(path: str) -> bool:
    return any(r.search(path) for r in BLOCKED)


class RemoteGuard:
    """套在设置平台外面、给公网监听用的 WSGI 中间件。"""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        ip = environ.get("REMOTE_ADDR", "")
        if not allowed(ip, parse_allow(get_config("remote_admin_ips", ""))):
            start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"Not Found"]
        if is_blocked_path(environ.get("PATH_INFO", "")):
            start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
            return ["这个操作为了安全，只能在服务器本机或通过隧道里做。".encode()]
        return self.app(environ, start_response)


class AdminGate:
    """挂在后台端口（5091）外面：没开「远程访问」时和以前一样（想让谁连由防火墙/安全组决定）；
    开了以后，从服务器本机以外来的请求只放名单里的 IP，并拒绝危险操作。不再单独占一个端口。"""

    def __init__(self, app):
        self.app = app
        self.guard = RemoteGuard(app)

    def __call__(self, environ, start_response):
        if get_config("remote_admin_enabled", "0") == "1" and environ.get("REMOTE_ADDR", "") not in LOCAL:
            return self.guard(environ, start_response)
        return self.app(environ, start_response)
