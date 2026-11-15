"""开机自启：Linux 用 systemd，Windows 用「任务计划」（开机时以 SYSTEM 运行，不需要有人登录）。

  changri service install     装上并马上启动（Linux 要 sudo；Windows 要「以管理员身份运行」）
  changri service uninstall   取消开机自启并停止
  changri service status      看装没装、在不在跑
顺便在本机防火墙放行设置平台和小手机的端口；云服务商的「安全组」程序碰不到，要自己去网页控制台放行。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .. import config
from ..paths import APP_ROOT
from . import layout

UNIT = "changri-bot"
UNIT_PATH = f"/etc/systemd/system/{UNIT}.service"
TASK = "ChangriBot"
FW_RULE = "ChangriBot"
SERVICE_USER = "changri"


def _ports() -> list[int]:
    from ..core import phoneserver
    try:
        phone = phoneserver.port()
    except Exception:  # noqa: BLE001  第一次装还没有数据库
        phone = phoneserver.DEFAULT_PORT
    return [config.admin_port(), phone]


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# ---------- Linux ----------

def _linux_unit(user: str) -> str:
    return f"""[Unit]
Description=长日bot（机器人 + 设置平台 + 小手机）
After=network-online.target
Wants=network-online.target

[Service]
User={user}
WorkingDirectory={APP_ROOT}
Environment=CHANGRI_SERVICE=1
Environment=PYTHONUNBUFFERED=1
ExecStart={APP_ROOT}/changri run
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
"""


def _ensure_user() -> str:
    """专门的系统用户 changri 来跑（不用 root）；建不了就退回 root。"""
    if _run(["id", SERVICE_USER], check=False).returncode == 0:
        return SERVICE_USER
    if shutil.which("useradd"):
        nologin = "/usr/sbin/nologin" if os.path.exists("/usr/sbin/nologin") else "/bin/false"
        if _run(["useradd", "--system", "--home-dir", str(APP_ROOT), "--no-create-home", "--shell", nologin, SERVICE_USER], check=False).returncode == 0:
            return SERVICE_USER
    return "root"


def _linux_firewall(ports: list[int]) -> list[str]:
    done = []
    if shutil.which("ufw") and "Status: active" in _run(["ufw", "status"], check=False).stdout:
        for p in ports:
            _run(["ufw", "allow", f"{p}/tcp"], check=False)
        done.append("ufw")
    if shutil.which("firewall-cmd") and _run(["firewall-cmd", "--state"], check=False).returncode == 0:
        _run(["firewall-cmd", "--permanent", *[f"--add-port={p}/tcp" for p in ports]], check=False)
        _run(["firewall-cmd", "--reload"], check=False)
        done.append("firewalld")
    return done


def _linux_install() -> str:
    if os.geteuid() != 0:
        raise RuntimeError("请用 sudo 运行：sudo ./changri service install")
    if not shutil.which("systemctl"):
        raise RuntimeError("这台机器没有 systemd，没法设开机自启。可以用 ./changri run 前台运行（配合 screen / tmux）。")
    user = _ensure_user()
    if user != "root":
        _run(["chown", "-R", f"{user}:", str(APP_ROOT)])      # 在线更新要往安装目录里写新版本
    with open(UNIT_PATH, "w", encoding="utf-8") as f:
        f.write(_linux_unit(user))
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", UNIT])
    fw = _linux_firewall(_ports())
    return f"已设为开机自启并启动（服务名 {UNIT}，以 {user} 用户运行）" + (f"；已在本机防火墙（{'、'.join(fw)}）放行端口" if fw else "")


def _linux_uninstall() -> str:
    _run(["systemctl", "disable", "--now", UNIT], check=False)
    if os.path.exists(UNIT_PATH):
        os.remove(UNIT_PATH)
        _run(["systemctl", "daemon-reload"], check=False)
    return "已取消开机自启并停止。数据还在 data/ 里，没动。"


def _linux_status() -> str:
    if not os.path.exists(UNIT_PATH):
        return "没有设开机自启"
    state = _run(["systemctl", "is-active", UNIT], check=False).stdout.strip()
    return f"已设开机自启，现在{'正在运行' if state == 'active' else '没在运行（' + state + '）'}。查看日志：journalctl -u {UNIT} -f"


# ---------- Windows ----------

def _win_launcher() -> str:
    return str(APP_ROOT / "Start-Changri.bat")


def _win_install() -> str:
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("请右键「Install-Autostart.bat」，选「以管理员身份运行」。")
    _run(["schtasks", "/Create", "/TN", TASK, "/TR", f'"{_win_launcher()}" --service', "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"])
    ports = ",".join(str(p) for p in _ports())
    _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"], check=False)
    fw = _run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RULE}", "dir=in", "action=allow", "protocol=TCP", f"localport={ports}"],
              check=False).returncode == 0
    _run(["schtasks", "/Run", "/TN", TASK], check=False)
    return "已设为开机自动运行，并已在后台启动（没有窗口）" + (f"；已在 Windows 防火墙放行 {ports} 端口" if fw else "") + \
        "。如果之前双击开着的窗口还在，请先关掉它，否则端口会冲突。"


def _win_uninstall() -> str:
    _run(["schtasks", "/End", "/TN", TASK], check=False)
    _run(["schtasks", "/Delete", "/TN", TASK, "/F"], check=False)
    _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"], check=False)
    return "已取消开机自动运行并停止。数据还在 data 文件夹里，没动。"


def _win_status() -> str:
    r = _run(["schtasks", "/Query", "/TN", TASK, "/FO", "LIST"], check=False)
    if r.returncode != 0:
        return "没有设开机自启"
    running = "Running" in r.stdout or "正在运行" in r.stdout
    return f"已设开机自启，现在{'正在运行' if running else '没在运行'}。"


def install() -> str:
    if not layout.INSTALLED:
        raise RuntimeError("现在是直接从源码运行的（开发环境）。开机自启只给安装包用。")
    return _win_install() if _is_windows() else _linux_install()


def uninstall() -> str:
    return _win_uninstall() if _is_windows() else _linux_uninstall()


def status() -> str:
    return _win_status() if _is_windows() else _linux_status()
