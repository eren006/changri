"""看门进程（python -m changri supervise）：把真正干活的程序（python -m changri run）作为子进程跑起来，看着它。

- 子进程退出码 3（点了「重启」、装完更新、改了扩展）：马上重新拉起；current 变了（装了新版本）就自己退出码 10，
  让外面的启动脚本按新版本、新运行环境重新起看门进程。
- 子进程异常退出：5 秒后重新拉起。刚更新完的新版本 90 秒内就退出，算一次失败，连续 3 次就回滚（core/updater.py）。
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
    while not stopping["flag"]:
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
            _log("重启中……")
            continue
        if code == 0:
            _log("程序正常退出。")
            return 0
        if ran < HEALTHY_AFTER and (msg := updater.rollback_if_failing()):
            _log(msg)
            return RELAUNCH
        _log(f"程序意外退出（退出码 {code}），{CRASH_DELAY} 秒后重新启动。")
        for _ in range(CRASH_DELAY * 10):              # 等的时候收到停止信号要能马上停，不能再拉起一次
            if stopping["flag"]:
                break
            time.sleep(0.1)
    _log("已停止。")
    return 0
