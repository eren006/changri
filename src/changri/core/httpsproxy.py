"""一键 HTTPS：用 Caddy 在前面接 https，再转给本机的设置平台和各机器人的小手机。

- Caddy 由 `scripts/install_caddy.sh`（Linux，root）或安装包根目录的 `Install-HTTPS.bat`（Windows，管理员，转给 scripts/install_caddy.bat，注册成服务 ChangriCaddy）装好，以「接口模式」运行（只在本机 127.0.0.1:2019 收配置，`--resume` 记住上次的配置）。
  之后开通/关闭/端口变化都由这里把配置推给它，程序本身不需要 root。
- 证书：默认用公网 IP 向 Let's Encrypt 申请 IP 证书（shortlived，6 天有效，Caddy 自动续）；也可以填自己的域名。
  Let's Encrypt 只认两种验证：来 80 端口（HTTP-01）或 443 端口（TLS-ALPN-01），两种都开着，有一个能通就行。
  80 被 nginx 之类占着时，让 Caddy 改在本机 ALT_HTTP_PORT 回应验证，由 nginx 把 /.well-known/acme-challenge/ 转过来。
- 端口：设置平台 https 用 5443；每个机器人的小手机 https = 它的 http 端口 + 1000（5092 → 6092）。原来的 http 端口照常能用。
- 只动名字以 changri_ 开头的 Caddy 站点和我们自己那张证书的申请规则，同一台 Caddy 上别的网站不受影响。
"""
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .db import get_config, list_bot_ids, set_config, use_bot

CADDY_API = "http://127.0.0.1:2019"
PREFIX = "changri_"
ADMIN_HTTPS_PORT = 5443
PHONE_OFFSET = 1000
ALT_HTTP_PORT = 5480                # 80 被别的程序占着时，Caddy 在这里回应 80 端口的验证（要那个程序转发过来）
MIN_VERSION = (2, 10, 0)            # 2.10 起 Caddy 才能选 Let's Encrypt 的 shortlived 配置（IP 证书必须用它）
from ..paths import APP_ROOT as ROOT, PKG_DIR
INSTALL_SCRIPT = PKG_DIR / "scripts" / "install_caddy.sh"          # 随程序走，在线更新会带上新版
INSTALL_SCRIPT_WIN = ROOT / "Install-HTTPS.bat"                    # Windows 安装包根目录的入口，转给当前版本里的脚本
WIN_EXE = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ChangriCaddy" / "caddy.exe"
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class CaddyError(Exception):
    pass


# ---------- 配置 ----------

def enabled() -> bool:
    return get_config("https_enabled", "0") == "1"


def active() -> bool:
    """证书已经拿到、网址已经切成 https。"""
    return get_config("https_active", "0") == "1"


def domain() -> str:
    return get_config("https_domain", "")


def subject() -> str:
    """证书上写的名字：填了域名用域名，否则用这台服务器的公网 IP。"""
    from .netcheck import public_ip_cached
    return domain() or get_config("https_subject", "") or public_ip_cached()


def admin_port() -> int:
    from .. import config
    return config.admin_port()


def phone_https_port(http_port: int) -> int:
    return http_port + PHONE_OFFSET


def check_domain(text: str) -> str:
    """能用返回空串，否则返回原因。"""
    d = text.strip().lower()
    if not d:
        return ""
    if d.startswith(("http://", "https://")) or "/" in d or ":" in d:
        return "只填域名本身，例如 bot.example.com，不要带 http:// 和端口"
    if not _DOMAIN_RE.match(d):
        return "这不像一个域名，例如 bot.example.com"
    return ""


def plan() -> list[dict]:
    """要开哪些 https 端口：设置平台 + 小手机（所有机器人共用一个端口，开着小手机的机器人有一个就开）。"""
    from .phoneserver import phone_bots, port
    out = [{"name": PREFIX + "admin", "label": "设置平台", "listen": ADMIN_HTTPS_PORT, "upstream": admin_port(), "bot": ""}]
    if phone_bots(fresh=True):
        p = port()
        out.append({"name": f"{PREFIX}phone", "label": "小手机", "listen": phone_https_port(p), "upstream": p, "bot": "*"})
    return out


def build(subj: str, items: list[dict], is_domain: bool) -> tuple[dict, dict]:
    """返回（站点表, 证书申请规则）。80、443 两种验证都开着：Caddy 失败了会换另一种再试。"""
    servers = {}
    for it in items:
        servers[it["name"]] = {
            "listen": [f":{it['listen']}"],
            "routes": [{"match": [{"host": [subj]}],
                        "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": f"127.0.0.1:{it['upstream']}"}]}],
                        "terminal": True}],
            # 用 IP 打开时浏览器不发 SNI；云服务器的网卡上又只有内网 IP，Caddy 认不出该给哪张证书，所以指定默认的
            "tls_connection_policies": [{"default_sni": subj}],
            "automatic_https": {"disable_redirects": True},
        }
    issuer = {"module": "acme"}
    if not is_domain:
        issuer["profile"] = "shortlived"                                            # Let's Encrypt 的 IP 证书只发这一种
    policy = {"subjects": [subj], "issuers": [issuer]}
    return servers, policy


def merge(cfg: dict | None, servers: dict, policy: dict | None, forget: set[str], alt_http: bool = False) -> dict:
    """把我们的站点和证书规则放进 Caddy 现有配置里；别人的站点和规则原样保留。
    forget：要删掉的旧证书名字（换域名、换 IP、关闭时）。
    alt_http：80 被别的程序占着，让 Caddy 在 ALT_HTTP_PORT 回应 80 端口的验证（Let's Encrypt 照样去 80，由那个程序转过来）。"""
    cfg = json.loads(json.dumps(cfg or {}))
    apps = cfg.setdefault("apps", {})
    http = apps.setdefault("http", {})
    if alt_http and servers:
        http["http_port"] = ALT_HTTP_PORT
    elif http.get("http_port") == ALT_HTTP_PORT:          # 只撤销我们自己设的
        http.pop("http_port")
    old = http.get("servers") or {}
    http["servers"] = {k: v for k, v in old.items() if not k.startswith(PREFIX)}
    http["servers"].update(servers)
    if not http["servers"]:
        http.pop("servers")
    if not http:
        apps.pop("http", None)
    tls = apps.setdefault("tls", {})
    auto = tls.setdefault("automation", {})
    names = set(forget) | ({*policy["subjects"]} if policy else set())
    pols = [p for p in auto.get("policies") or [] if not names & set(p.get("subjects") or [])]
    if policy:
        pols.insert(0, policy)
    if pols:
        auto["policies"] = pols
    else:
        auto.pop("policies", None)
    if not auto:
        tls.pop("automation", None)
    if not tls:
        apps.pop("tls", None)
    if not apps:
        cfg.pop("apps", None)
    return cfg


# ---------- 和 Caddy 打交道 ----------

def _api(method: str, path: str, body: dict | None = None, timeout: float = 10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(CADDY_API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        raise CaddyError(detail.strip()[:400] or f"HTTP {e.code}") from None
    except OSError as e:
        raise CaddyError(f"连不上 Caddy（{e}）") from None
    return json.loads(text) if text.strip() else None


def api_ok() -> bool:
    try:
        _api("GET", "/config/", timeout=3)
        return True
    except CaddyError:
        return False


def caddy_version() -> tuple[int, ...] | None:
    exe = next((p for p in (shutil.which("caddy"), "/usr/bin/caddy", str(WIN_EXE)) if p and os.path.exists(p)), "")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"v(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def cert_ok(subj: str, port: int = ADMIN_HTTPS_PORT, timeout: float = 4) -> bool:
    """用系统信任的证书库连一下本机的 https 端口：证书是正规签发的、名字对得上，才算好。"""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=subj):
            return True
    except (OSError, ssl.SSLError, ValueError):
        return False


def port80_state() -> str:
    """none = 本机 80 端口空着（Caddy 验证时会自己临时用）；caddy = Caddy 自己占着（它自己会回应）；
    nginx = 被 nginx 占着（可以让它转发）；other = 被别的程序占着。"""
    try:
        with socket.create_connection(("127.0.0.1", 80), timeout=2) as s:
            s.sendall(b"HEAD /.well-known/acme-challenge/changri-check HTTP/1.0\r\nHost: localhost\r\n\r\n")
            head = s.recv(2048).decode("latin-1", "replace").lower()
    except OSError:
        return "none"
    if "server: caddy" in head:
        return "caddy"
    return "nginx" if "server: nginx" in head else "other"


def port443_state() -> str:
    """none = 空着；caddy = Caddy 自己有站点在 443（它自己会回应验证）；other = 被别的程序占着。"""
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=2):
            pass
    except OSError:
        return "none"
    try:
        servers = ((_api("GET", "/config/", timeout=3) or {}).get("apps") or {}).get("http", {}).get("servers") or {}
    except CaddyError:
        servers = {}
    return "caddy" if any(a.rsplit(":", 1)[-1] == "443" for s in servers.values() for a in s.get("listen") or []) else "other"


def blocked(p80: str, p443: str) -> bool:
    """80、443 都被别的程序占着，又没设转发：证书拿不到。"""
    return p80 in ("nginx", "other") and p443 == "other"


def nginx_snippet() -> str:
    return ("location /.well-known/acme-challenge/ {\n"
            f"    proxy_pass http://127.0.0.1:{ALT_HTTP_PORT};\n"
            "    proxy_set_header Host $host;\n"
            "}")


def is_linux() -> bool:
    return hasattr(os, "uname") and os.uname().sysname == "Linux"


def is_windows() -> bool:
    return os.name == "nt"


def install_command() -> str:
    """Linux：粘贴到终端的一行命令；Windows：要右键「以管理员身份运行」的文件路径。"""
    if is_windows():
        return str(INSTALL_SCRIPT_WIN)
    import shlex
    return f"sudo bash {shlex.quote(str(INSTALL_SCRIPT))}"          # 安装目录可能带空格


def status() -> dict:
    ver = caddy_version()
    st = {
        "linux": is_linux(),
        "windows": is_windows(),
        "version": ".".join(map(str, ver)) if ver else "",
        "version_ok": bool(ver and ver >= MIN_VERSION),
        "api": api_ok(),
        "enabled": enabled(),
        "active": active(),
        "domain": domain(),
        "subject": subject(),
        "install": install_command(),
        "admin_https": ADMIN_HTTPS_PORT,
    }
    st["installed"] = bool(ver) or st["api"]
    st["cert"] = bool(st["enabled"] and st["api"] and st["subject"] and cert_ok(st["subject"]))
    st["supported"] = st["linux"] or st["windows"]
    check = st["supported"] and not st["cert"]
    st["port80"] = port80_state() if check else ""
    st["port443"] = port443_state() if check else ""
    st["blocked"] = blocked(st["port80"], st["port443"])
    st["alt_port"] = ALT_HTTP_PORT
    st["snippet"] = nginx_snippet()
    st["reload_cmd"] = "nginx -t && nginx -s reload" if st["windows"] else "sudo nginx -t && sudo systemctl reload nginx"
    st["sites"] = [dict(it, url=f"https://{st['subject']}:{it['listen']}" + ("/p" if it["bot"] else "")) for it in plan()] if st["subject"] else []
    return st


# ---------- 开通 / 关闭 ----------

def _push(subj: str, forget: set[str]) -> None:
    servers, policy = build(subj, plan(), bool(domain()))
    alt = port80_state() in ("nginx", "other")          # 80 被别人占着：Caddy 改在 ALT_HTTP_PORT 回应，等 nginx 转发（443 空着的话也能直接走 443）
    _api("POST", "/load", merge(_api("GET", "/config/"), servers, policy, forget, alt_http=alt), timeout=30)


def enable(new_domain: str = "") -> str:
    """把配置推给 Caddy。证书是 Caddy 在后台申请的，要等 activate_if_ready() 看到证书好了才切网址。"""
    if bad := check_domain(new_domain):
        raise CaddyError(bad)
    old = get_config("https_subject", "")
    set_config("https_domain", new_domain.strip().lower())
    subj = subject()
    if not subj:
        raise CaddyError("查不到这台服务器的公网 IP，稍后再试，或填一个域名")
    _push(subj, {old} - {subj} if old else set())
    if old and old != subj:
        _switch_urls(False)
        set_config("https_active", "0")
    set_config("https_subject", subj)
    set_config("https_enabled", "1")
    return subj


def activate_if_ready() -> bool:
    """证书好了就把每个机器人的小手机网址换成 https（只换一次）。返回现在是不是 https。"""
    if not enabled():
        return False
    if active():
        return True
    if not cert_ok(subject()):
        return False
    _switch_urls(True)
    set_config("https_active", "1")
    return True


def sync() -> str:
    """小手机开关/端口变了以后调用：重新推一次配置、更新网址。没开 HTTPS 就什么都不做。返回出错原因（空 = 没问题）。"""
    if not enabled():
        return ""
    try:
        _push(subject(), set())
    except CaddyError as e:
        return f"HTTPS 没能跟着更新：{e}"
    if active():
        _switch_urls(True)
    return ""


def disable() -> None:
    subj = get_config("https_subject", "") or subject()
    try:
        _api("POST", "/load", merge(_api("GET", "/config/"), {}, None, {subj}), timeout=30)
    finally:
        _switch_urls(False)
        set_config("https_enabled", "0")
        set_config("https_active", "0")


def _switch_urls(on: bool) -> None:
    from .netcheck import public_ip_cached
    from .phoneserver import port_of
    subj, ip = subject(), public_ip_cached()
    for bid in list_bot_ids(True):
        if not bid:
            continue
        with use_bot(bid):
            if get_config("phone_enabled", "0") != "1":
                continue
            p = port_of(bid)
            if on:
                set_config("phone_base_url", f"https://{subj}:{phone_https_port(p)}")
                set_config("phone_https", "1")
            else:
                if get_config("phone_base_url", "").startswith("https://"):
                    set_config("phone_base_url", f"http://{ip}:{p}" if ip else "")
                set_config("phone_https", "0")
