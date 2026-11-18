"""启动前看端口：设置平台的端口能不能用、被谁占着。

被占时不自动换端口（作者 2026-11-18 定的）：安全组 / 防火墙、教程、书签、一键 HTTPS 都认这个端口，
悄悄换了外面就找不到；而且被占多半是又开了一个长日bot，换端口等于两个程序连同一个机器人、写同一份数据。
所以只说清楚是谁占的、怎么处理，让装的人自己决定。

查占用者只是尽力而为：Windows 用 netstat + tasklist，Linux 用 ss，macOS 用 lsof；查不到就不说是谁。
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import urllib.request

PORT_BUSY = 4        # changri run 发现端口被占时的退出码：看门进程见到它就不再重启（systemd 单元里也写了不重启）


def can_bind(host: str, port: int) -> bool:
    """跟 uvicorn 一样试着绑一下（非 Windows 也开 SO_REUSEADDR，不然刚关掉的程序留下的 TIME_WAIT 会被误判成占用）。"""
    fam = socket.AF_INET6 if ":" in (host or "") else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        if not sys.platform.startswith("win"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host or "0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def changri_on(port: int) -> bool:
    """这个端口上是不是一个长日bot 的设置平台（/healthz 回 ok）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            return r.status == 200 and r.read(16).strip() == b"ok"
    except Exception:  # noqa: BLE001  连不上、超时、别的程序回了别的东西：都不是
        return False


def _out(cmd: list[str]) -> str:
    try:
        kw = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}    # CREATE_NO_WINDOW：别闪黑窗
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5, errors="replace", **kw).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _windows(port: int) -> str:
    pid = ""
    for line in _out(["netstat", "-ano", "-p", "TCP"]).splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].rsplit(":", 1)[-1] == str(port) and parts[3].upper() == "LISTENING":
            pid = parts[4]
            break
    if not pid.isdigit():
        return ""
    m = re.match(r'"([^"]+)"', _out(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).strip())
    return f"{m.group(1)}（PID {pid}）" if m else f"PID {pid}"


def _linux(port: int) -> str:
    m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', _out(["ss", "-ltnpH", f"sport = :{port}"]))
    return f"{m.group(1)}（PID {m.group(2)}）" if m else ""


def _mac(port: int) -> str:
    pid = name = ""
    for line in _out(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"]).splitlines():
        if line.startswith("p") and not pid:
            pid = line[1:]
        elif line.startswith("c") and not name:
            name = line[1:]
    return f"{name}（PID {pid}）" if pid and name else (f"PID {pid}" if pid else "")


def who_listens(port: int) -> str:
    """占着这个端口的程序，比如「python.exe（PID 1234）」；查不到返回空串。"""
    try:
        if sys.platform.startswith("win"):
            return _windows(port)
        if sys.platform == "darwin":
            return _mac(port)
        return _linux(port)
    except Exception:  # noqa: BLE001
        return ""


# 修复（结束卡死的旧长日bot、关掉占位置的程序）不放在程序里，交给官网的「长日bot 修复工具」（作者 2026-11-18 定的）：
# 老版本也能用、能远程更新、出了问题好改。这里只负责把下载地址说清楚。
FIX_PAGE = "https://system.changri.work/fix/"
FIX_LINUX = "curl -fsSL https://system.changri.work/fix.sh | sudo bash"


def _win() -> bool:
    return sys.platform.startswith("win")


def fix_hint() -> str:
    """一句话：怎么用修复工具。"""
    if _win():
        return f"最简单的办法：下载「长日bot 修复工具」（{FIX_PAGE}），解压后双击 Fix-Changri.bat，它会帮你修好；或者重启电脑，然后只双击一次 Start-Changri.bat。"
    return f"最简单的办法：运行修复工具：{FIX_LINUX}"


def busy_message(port: int, who: str = "", changri: bool = False) -> str:
    """给人看的一段话：谁占的、怎么办。"""
    if changri:
        return f"长日bot 已经在运行了（端口 {port} 上），这次不再启动。想重新开，先关掉原来那个。"
    by = f"被 {who} 占用了" if who else "被别的程序占用了"
    kill = (f"在任务管理器里结束 {who}" if who and sys.platform.startswith("win") else
            f"结束 {who}" if who else "找到占用它的程序并关掉（Windows：netstat -ano | findstr :%d）" % port)
    return ((f"长日bot 打不开：电脑上的 {who} 正在用它需要的位置。" if who else "长日bot 打不开：电脑上有别的程序正在用它需要的位置。")
            + fix_hint() + "\n"
            + f"（技术说明：设置平台的端口 {port} {by}。处理办法二选一：① {kill}，再重新打开长日bot；"
            f"② 打开 data/config.toml，把 port = {port} 改成别的端口（比如 {port + 10}），保存后重新打开，"
            f"之后用新端口访问设置平台，云服务器的安全组 / 防火墙也要放行新端口。）")
