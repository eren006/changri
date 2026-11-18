"""自检（changri doctor）：装好以后不知道哪里不对，先跑这个。只读，不改任何东西（要修用官网的修复工具，见 portcheck.fix_hint）。"""
from __future__ import annotations

import os
import socket
import sys

from .. import config
from ..paths import APP_ROOT, DATA_DIR
from . import layout

QQ_HOSTS = [("api.sgroup.qq.com", 443), ("bots.qq.com", 443)]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _reach(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def checks() -> list[tuple[bool | None, str, str]]:
    """[(结果, 项目, 说明)]；结果 None = 只是信息。"""
    out: list[tuple[bool | None, str, str]] = []
    out.append((None, "版本", f"{layout.package_version()}（{layout.platform_tag()}，Python {sys.version.split()[0]}）"))
    out.append((None, "程序目录", str(APP_ROOT)))
    writable = os.access(DATA_DIR if DATA_DIR.exists() else DATA_DIR.parent, os.W_OK)
    out.append((writable, "数据目录可写", str(DATA_DIR) if writable else f"{DATA_DIR} 写不进去：看看是不是用别的用户装的"))
    ap = config.admin_port()
    if not _listening(ap):
        out.append((False, f"设置平台（{ap} 端口）", "没在运行：先启动程序"))
    else:
        from .portcheck import busy_message, changri_on, who_listens
        if changri_on(ap):
            out.append((True, f"设置平台（{ap} 端口）", "在运行"))
        else:
            out.append((False, f"设置平台（{ap} 端口）", busy_message(ap, who_listens(ap))))
    try:
        from ..core import phoneserver
        if phoneserver.phone_bots(fresh=True):
            pp = phoneserver.port()
            ok = _listening(pp)
            out.append((ok, f"小手机（{pp} 端口）", "在运行" if ok else "开着小手机但没在监听：去设置平台「小手机」页看看提示"))
        else:
            out.append((None, "小手机", "没有机器人开着小手机"))
    except Exception as e:  # noqa: BLE001
        out.append((None, "小手机", f"查不了（{type(e).__name__}）"))
    for host, port in QQ_HOSTS:
        ok = _reach(host, port)
        out.append((ok, f"能连上 QQ（{host}）", "能" if ok else "连不上：检查这台服务器能不能上外网"))
    try:
        from ..core.netcheck import public_ip
        ip = public_ip()
        out.append((None, "公网 IP", (ip + "（QQ 开放平台的 IP 白名单要填这个）") if ip else "查不到"))
    except Exception:  # noqa: BLE001
        pass
    if layout.INSTALLED:
        from . import service
        try:
            out.append((None, "开机自启", service.status()))
        except Exception as e:  # noqa: BLE001
            out.append((None, "开机自启", f"查不了（{type(e).__name__}）"))
    return out


def report() -> tuple[str, bool]:
    lines, all_ok = [], True
    for ok, name, note in checks():
        mark = "  ·" if ok is None else ("  ✓" if ok else "  ✗")
        all_ok = all_ok and ok is not False
        lines.append(f"{mark} {name}：{note}")
    lines.append("")
    lines.append("全部正常。" if all_ok else "有打 ✗ 的项目，按说明处理；还是不行就把上面这些发给维护的人。")
    return "\n".join(lines), all_ok

