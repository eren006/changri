"""看门进程（python -m changri supervise）：把真正干活的程序（python -m changri run）作为子进程跑起来，看着它。

- 子进程退出码 3（点了「重启」、装完更新、改了扩展）：马上重新拉起；current 变了（装了新版本）就自己退出码 10，
  让外面的启动脚本按新版本、新运行环境重新起看门进程。
- 子进程异常退出：5 秒后重新拉起。刚更新完的新版本 90 秒内就退出，算一次失败，连续 3 次就回滚（core/updater.py）。
- 连续 MAX_QUICK_CRASHES 次都是一启动就崩（端口被占、配置写错这类重来多少次都不会好的）：不再拉起，退出码 0。
  用 0 是为了让老版本的 Start-Changri.bat 也停下来（它见到非 0 会自己再循环）；有窗口的话先停住让人看清原因。
- 每次拉起前先看设置平台端口上是不是已经有一个长日bot 在跑（/healthz 回 ok）：是的话这次不启动，退出码 0。
  常见于装了开机自启又双击了启动；不查的话新的那个抢不到端口，一直崩一直重启，报错刷屏。
- 收到 Ctrl+C / 服务停止：把子进程一起停掉，自己退出码 0（启动脚本看到 0 就不再循环）。
Windows 和 Linux 用同一套，取代以前 start.bat / start.sh 里的循环。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from . import layout

RELAUNCH = 10            # 给启动脚本：按 current 重新起我
RESTART_CODE = 3
HEALTHY_AFTER = 90
CRASH_DELAY = 5
MAX_QUICK_CRASHES = 5    # 连续这么多次「没跑满 HEALTHY_AFTER 秒就崩」就放弃


def _child_cmd() -> list[str]:
    return [sys.executable, "-m", "changri", "run"]


def _log(msg: str) -> None:
    print(time.strftime("%m-%d %H:%M:%S"), "[看门]", msg, flush=True)


def _open_browser_once() -> None:
    """Windows 上双击启动、第一次还没建账号：等设置平台起来后自动打开浏览器。服务模式（没有桌面）不打开。"""
    if not sys.platform.startswith("win") or os.environ.get("CHANGRI_SERVICE") == "1":
        return
    try:
        from ..core import webusers
        if webusers.count() > 0:
            return
    except Exception:  # noqa: BLE001
        pass
    import threading
    import urllib.request
    import webbrowser

    from .. import config

    def wait_and_open():
        url = f"http://127.0.0.1:{config.admin_port()}/"
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "healthz", timeout=2)
                webbrowser.open(url)
                return
            except Exception:  # noqa: BLE001
                time.sleep(1)
    threading.Thread(target=wait_and_open, daemon=True).start()


def _wait_for_user() -> None:
    """双击启动的窗口：停住，不然窗口一闪就关，人看不到为什么停了。开机自启（没有窗口）直接走。"""
    if os.environ.get("CHANGRI_SERVICE") == "1" or not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("按回车键关闭这个窗口……")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def _already_running() -> int | None:
    """设置平台端口上已经有一个长日bot 在跑：返回端口号；没有（连不上、不是长日bot）返回 None。"""
    import urllib.request

    from .. import config
    try:
        port = config.admin_port()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            if r.status == 200 and r.read(16).strip() == b"ok":
                return port
    except Exception:  # noqa: BLE001  连不上、超时、别的程序回了别的东西：都当没有
        pass
    return None


def run() -> int:
    from ..core import updater
    started_version = layout.read_current()
    stopping = {"flag": False}
    child: dict = {"p": None}

    def on_signal(signum, _frame):
        stopping["flag"] = True
        p = child["p"]
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_signal)        # Windows 关窗口 / Ctrl+Break

    _open_browser_once()
    quick_crashes = 0
    while not stopping["flag"]:
        if (port := _already_running()) is not None:
            _log(f"已经有一个长日bot 在运行（设置平台 http://127.0.0.1:{port} 能打开），这次不再启动。")
            _log("想重新开：先关掉原来那个。装了开机自启的，用 Remove-Autostart.bat 取消，或重启电脑后只留一种启动方式。")
            _wait_for_user()
            return 0
        t0 = time.monotonic()
        child["p"] = subprocess.Popen(_child_cmd())
        try:
            code = child["p"].wait()
        except KeyboardInterrupt:
            stopping["flag"] = True
            child["p"].terminate()
            code = child["p"].wait()
        ran = time.monotonic() - t0
        if stopping["flag"]:
            _log("已停止。")
            return 0
        if ran >= HEALTHY_AFTER:
            updater.confirm_healthy()
        if layout.INSTALLED and layout.read_current() != started_version:
            _log(f"切换到版本 {layout.read_current()}……")
            return RELAUNCH
        if code == RESTART_CODE:
            quick_crashes = 0
            _log("重启中……")
            continue
        if code == 0:
            _log("程序正常退出。")
            return 0
        if ran < HEALTHY_AFTER and (msg := updater.rollback_if_failing()):
            _log(msg)
            return RELAUNCH
        quick_crashes = quick_crashes + 1 if ran < HEALTHY_AFTER else 0
        if quick_crashes >= MAX_QUICK_CRASHES:
            _log(f"程序连续 {quick_crashes} 次一启动就退出（最后一次退出码 {code}），不再自动重启。")
            _log("请看上面的报错。最常见的是端口被占用：可能已经开着一个长日bot（比如装了开机自启又双击了启动），"
                 "或者别的软件占了这个端口。处理好之后再重新打开。")
            _wait_for_user()
            return 0
        _log(f"程序意外退出（退出码 {code}），{CRASH_DELAY} 秒后重新启动。")
        for _ in range(CRASH_DELAY * 10):              # 等的时候收到停止信号要能马上停，不能再拉起一次
            if stopping["flag"]:
                break
            time.sleep(0.1)
    _log("已停止。")
    return 0
