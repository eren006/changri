"""开机自启：Linux 用 systemd，Windows 用「任务计划」（开机时以 SYSTEM 运行，不需要有人登录）。

  changri service install     装上并马上启动（Linux 要 sudo；Windows 要「以管理员身份运行」）
  changri service uninstall   取消开机自启并停止
  changri service status      看装没装、在不在跑
顺便在本机防火墙放行设置平台和小手机的端口；云服务商的「安全组」程序碰不到，要自己去网页控制台放行。

本机防火墙（open_firewall）不只装的时候放行一次：每次启动、改小手机端口、开 HTTPS 都会按当前端口再放行一遍。
- Windows：开机自启以 SYSTEM 运行，直接 netsh；双击运行（不是管理员）的放行不了，也不报错。
- Linux：服务以 changri 用户运行，没有 root。装的时候写一个 root 拥有的小脚本 FW_HELPER（只接受端口号参数），
  再写 sudoers 只允许 changri 免密运行它。不能让 root 去跑安装目录里的东西：安装目录归 changri 用户所有，能改。
- 当前端口另外写到 data/firewall_ports，给修复工具读（它不用懂数据库）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config
from ..paths import APP_ROOT, DATA_DIR
from . import layout

UNIT = "changri-bot"
UNIT_PATH = f"/etc/systemd/system/{UNIT}.service"
TASK = "ChangriBot"
FW_RULE = "ChangriBot"
SERVICE_USER = "changri"
FW_HELPER = "/usr/local/sbin/changri-firewall"
SUDOERS_PATH = "/etc/sudoers.d/changri-bot"
PORTS_FILE = DATA_DIR / "firewall_ports"
DONE_FILE = DATA_DIR / "firewall_done"          # 上次放行成功的那组端口：启动时端口没变就不再放行


def _ports() -> list[int]:
    """要放行的端口：设置平台、小手机；开了 HTTPS 再加 80、443、设置平台 https、小手机 https。"""
    from ..core import phoneserver
    try:
        phone = phoneserver.port()
    except Exception:  # noqa: BLE001  第一次装还没有数据库
        phone = phoneserver.DEFAULT_PORT
    ports = [config.admin_port(), phone]
    try:
        from ..core import httpsproxy
        if httpsproxy.enabled():
            ports += [80, 443, httpsproxy.ADMIN_HTTPS_PORT, httpsproxy.phone_https_port(phone)]
    except Exception:  # noqa: BLE001  还没有数据库
        pass
    return list(dict.fromkeys(ports))


def _write_ports_file(ports: list[int]) -> None:
    try:
        PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORTS_FILE.write_text(" ".join(map(str, ports)) + "\n", encoding="utf-8")
    except OSError:
        pass


def _key(ports: list[int]) -> str:
    return " ".join(map(str, ports))


def _already_done(ports: list[int]) -> bool:
    try:
        return DONE_FILE.read_text(encoding="utf-8").strip() == _key(ports)
    except OSError:
        return False


def _mark_done(ports: list[int]) -> None:
    try:
        DONE_FILE.write_text(_key(ports) + "\n", encoding="utf-8")
    except OSError:
        pass


def open_firewall(ports: list[int] | None = None, only_if_changed: bool = False) -> str:
    """在本机防火墙放行端口。返回给人看的一句话（空串 = 没放行：防火墙没开、没权限、端口没变（only_if_changed），或者是开发环境）。从不抛错。
    only_if_changed：启动时用，这组端口上次已经放行成功就跳过。"""
    if not layout.INSTALLED:
        return ""
    ports = ports or _ports()
    _write_ports_file(ports)
    if only_if_changed and _already_done(ports):
        return ""
    shown = "、".join(map(str, ports))
    try:
        if _is_windows():
            if not _win_firewall(ports):
                return ""
            _mark_done(ports)
            return f"Windows 防火墙已放行 {shown} 端口"
        if os.geteuid() == 0:
            r = None
            done = _linux_firewall(ports)
        elif os.path.exists(FW_HELPER) and shutil.which("sudo"):
            r = _run(["sudo", "-n", FW_HELPER, *map(str, ports)], check=False)
            done = r.stdout.split() if r.returncode == 0 else []
        else:
            return ""
        if r is None or r.returncode == 0:
            _mark_done(ports)                            # 防火墙没开也算「处理过了」：下次启动不再试
    except Exception:  # noqa: BLE001
        return ""
    return f"本机防火墙（{'、'.join(done)}）已放行 {shown} 端口" if done else ""


def _linux_fw_active() -> str:
    """不用 root 也能看的：ufw 看配置文件，firewalld 看服务在不在跑。返回开着的那个名字，都没开返回空串。"""
    try:
        if shutil.which("ufw") and "ENABLED=yes" in open("/etc/ufw/ufw.conf", encoding="utf-8").read().replace(" ", ""):
            return "ufw"
    except OSError:
        pass
    if shutil.which("firewall-cmd") and shutil.which("systemctl") \
            and _run(["systemctl", "is-active", "firewalld"], check=False).stdout.strip() == "active":
        return "firewalld"
    return ""


def firewall_hint() -> str:
    """诊断：本机防火墙开着、长日bot 自己又放行不了时，告诉人怎么放行。没问题（或看不出来）返回空串。不抛错。"""
    if not layout.INSTALLED:
        return ""
    from .portcheck import FIX_LINUX, FIX_PAGE
    ports = _ports()
    shown = ",".join(map(str, ports))
    try:
        if _is_windows():
            r = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FW_RULE}"], check=False)
            if r.returncode == 0 and all(str(p) in r.stdout for p in ports):
                return ""
            return ("Windows 防火墙还没放行长日bot 的端口。用管理员身份打开命令提示符，运行："
                    f"netsh advfirewall firewall add rule name={FW_RULE} dir=in action=allow protocol=TCP localport={shown}"
                    f"；或者运行一次修复工具（{FIX_PAGE}）。")
        if os.geteuid() == 0 or os.path.exists(FW_HELPER):
            return ""                                    # 自己能放行，启动时已经放过了
        fw = _linux_fw_active()
        if not fw:
            return ""
        return (f"这台服务器的本机防火墙（{fw}）开着，长日bot 没法自己放行端口 {shown}。"
                f"运行一次修复工具就会帮你放行，以后改端口也会自动放行：{FIX_LINUX}")
    except Exception:  # noqa: BLE001
        return ""


def open_firewall_async() -> None:
    """启动时用：firewall-cmd --reload 可能要几秒，别卡住启动。"""
    import threading
    threading.Thread(target=open_firewall, kwargs={"only_if_changed": True}, name="changri-firewall", daemon=True).start()


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
RestartPreventExitStatus=4
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


FW_HELPER_SRC = Path(__file__).with_name("firewall_helper.sh")     # 修复工具（packaging/fix/fix.sh）打包时也嵌进同一份


def _linux_sudo_helper(user: str) -> None:
    """写 FW_HELPER 和 sudoers，让以 changri 用户跑的长日bot 改了端口也能自己放行。sudoers 先用 visudo 检查，不对就不装。"""
    if user == "root" or not shutil.which("visudo"):
        return
    with open(FW_HELPER, "w", encoding="utf-8") as f:
        f.write(FW_HELPER_SRC.read_text(encoding="utf-8"))
    os.chown(FW_HELPER, 0, 0)
    os.chmod(FW_HELPER, 0o755)
    rule = f"Defaults:{user} !requiretty\n{user} ALL=(root) NOPASSWD: {FW_HELPER}\n"
    tmp = SUDOERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(rule)
    os.chmod(tmp, 0o440)
    if _run(["visudo", "-cf", tmp], check=False).returncode == 0:
        os.replace(tmp, SUDOERS_PATH)
    else:
        os.remove(tmp)


def _linux_firewall(ports: list[int]) -> list[str]:
    """有 root 时直接跑放行脚本（和 sudo 那条路、修复工具是同一份）。返回放行了的防火墙名字。"""
    return _run(["sh", str(FW_HELPER_SRC), *map(str, ports)], check=False).stdout.split()


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
    try:
        _linux_sudo_helper(user)
    except OSError:
        pass                                             # 装不上只是以后改端口要手动放行，不影响开机自启
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", UNIT])
    fw = open_firewall()
    return f"已设为开机自启并启动（服务名 {UNIT}，以 {user} 用户运行）" + (f"；{fw}" if fw else "")


def _linux_uninstall() -> str:
    _run(["systemctl", "disable", "--now", UNIT], check=False)
    if os.path.exists(UNIT_PATH):
        os.remove(UNIT_PATH)
        _run(["systemctl", "daemon-reload"], check=False)
    for f in (SUDOERS_PATH, FW_HELPER):
        if os.path.exists(f):
            os.remove(f)
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
    fw = open_firewall()
    _run(["schtasks", "/Run", "/TN", TASK], check=False)
    return "已设为开机自动运行，并已在后台启动（没有窗口）" + (f"；{fw}" if fw else "") + \
        "。如果之前双击开着的窗口还在，请先关掉它，否则端口会冲突。"


def _win_firewall(ports: list[int]) -> bool:
    """整条规则按当前端口重建（规则名固定，改了端口旧的就不留）。不是管理员时两条都失败，原来的规则不动。"""
    add = ["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RULE}", "dir=in", "action=allow", "protocol=TCP",
           "localport=" + ",".join(map(str, ports))]
    if _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"], check=False).returncode != 0 \
            and _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FW_RULE}"], check=False).returncode == 0:
        return False                                     # 有规则却删不掉 = 没有管理员权限
    return _run(add, check=False).returncode == 0


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
