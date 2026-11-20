"""看门进程（python -m changri supervise）：把真正干活的程序（python -m changri run）作为子进程跑起来，看着它。

- 子进程退出码 3（点了「重启」、装完更新、改了扩展）：马上重新拉起；current 变了（装了新版本）就自己退出码 10，
  让外面的启动脚本按新版本、新运行环境重新起看门进程。
- 子进程异常退出：5 秒后重新拉起。刚更新完的新版本 90 秒内就退出，算一次失败，连续 3 次就回滚（core/updater.py）。
- 子进程退出码 4：新版本跑起来了但升级后检查没通过，马上回滚（updater.rollback_now），退出码 10 按旧版本重新拉起。
- 连续 MAX_QUICK_CRASHES 次都是一启动就崩（端口被占、配置写错这类重来多少次都不会好的）：不再拉起，退出码 0。
  用 0 是为了让老版本的 Start-Changri.bat 也停下来（它见到非 0 会自己再循环）；有窗口的话先停住让人看清原因。
- 每次拉起前先看设置平台端口上是不是已经有一个长日bot 在跑（/healthz 回 ok）：是的话这次不启动、帮他打开设置平台，退出码 0。
  常见于装了开机自启又双击了启动；不查的话新的那个抢不到端口，一直崩一直重启，报错刷屏。
  端口被卡死的旧长日bot / 别的程序占着：不在这里修，提示用官网的修复工具（portcheck.fix_hint）。
- 子进程退出码 PORT_BUSY（设置平台端口被别的程序占了，platform/portcheck.py）：不再拉起，退出码 0。
- 收到 Ctrl+C / 服务停止：把子进程一起停掉，自己退出码 0（启动脚本看到 0 就不再循环）。
  同时替子进程写好「正常退出」（_mark_stopped）：Windows 上关窗口时子进程是被直接结束的，
  自己的收尾跑不到，不写的话下次启动会被记成「崩溃」并上报。
Windows 和 Linux 用同一套，取代以前 start.bat / start.sh 里的循环。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

from . import layout
from .portcheck import PORT_BUSY

RELAUNCH = 10            # 给启动脚本：按 current 重新起我
RESTART_CODE = 3
HEALTHY_AFTER = 90
CONFIRM_FALLBACK = 900   # 升级后检查（core/autoupdate.post_check）最多几分钟；子进程跑满这么久还没确认，看门进程替它确认
CRASH_DELAY = 5
MAX_QUICK_CRASHES = 5    # 连续这么多次「没跑满 HEALTHY_AFTER 秒就崩」就放弃


def _child_cmd() -> list[str]:
    return [sys.executable, "-m", "changri", "run"]


def _repair_win_task() -> None:
    """Windows 开机自启：旧任务带着 3 天时限，自动换掉（见 service.repair_win_task）。"""
    try:
        from .service import repair_win_task
        if msg := repair_win_task():
            _log(msg)
    except Exception:  # noqa: BLE001
        pass


def _log(msg: str) -> None:
    print(time.strftime("%m-%d %H:%M:%S"), "[看门]", msg, flush=True)


def _mark_stopped() -> None:
    """人为停止（关窗口、Ctrl+C、停服务）：替子进程删掉「运行中」标记（core/errlog.py），下次启动不记成崩溃。
    Windows 关窗口时子进程直接被系统或 terminate() 结束，NoneBot 的 on_shutdown 和 atexit 都跑不到。"""
    try:
        from ..core import errlog
        errlog.mark_clean_exit()
    except Exception:  # noqa: BLE001
        pass


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
    """设置平台端口上已经有一个正常的长日bot：返回端口号；没有（连不上、不是长日bot）返回 None。
    卡死的旧长日bot、别的程序占着端口：不在这里修，子进程会把原因和修复工具的地址打出来（portcheck.fix_hint）。"""
    from .. import config
    from .portcheck import changri_on
    try:
        port = config.admin_port()
    except Exception:  # noqa: BLE001
        return None
    return port if changri_on(port) else None


def _open_running(port: int) -> None:
    """已经有一个长日bot 在跑：双击启动的就直接帮他打开设置平台（不用懂「已经在运行」是什么意思）。"""
    if not sys.platform.startswith("win") or os.environ.get("CHANGRI_SERVICE") == "1":
        return
    try:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")
    except Exception:  # noqa: BLE001
        pass


def run() -> int:
    from ..core import updater
    started_version = layout.read_current()
    stopping = {"flag": False}
    child: dict = {"p": None}

    def on_signal(signum, _frame):
        stopping["flag"] = True
        _mark_stopped()
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
    threading.Thread(target=_repair_win_task, name="win-task-repair", daemon=True).start()   # 放后台：schtasks 万一卡住也不耽误机器人启动
    quick_crashes = 0
    while not stopping["flag"]:
        if (port := _already_running()) is not None:
            _log(f"长日bot 已经在运行了，不用再开一个。设置平台：http://127.0.0.1:{port}/" +
                 ("（已经帮你在浏览器里打开）" if sys.platform.startswith("win") and os.environ.get("CHANGRI_SERVICE") != "1" else ""))
            _open_running(port)
            return 0
        t0 = time.monotonic()
        child["p"] = subprocess.Popen(_child_cmd())
        try:
            code = child["p"].wait()
        except KeyboardInterrupt:
            stopping["flag"] = True
            _mark_stopped()
            child["p"].terminate()
            code = child["p"].wait()
        ran = time.monotonic() - t0
        if stopping["flag"]:
            _mark_stopped()                            # 子进程退得晚、又写回过标记也不怕：等它真退出了再删一次
            _log("已停止。")
            return 0
        if code == updater.ROLLBACK_CODE and (msg := updater.rollback_now()):     # 新版本跑起来了，但升级后检查没通过
            _log(msg)
            return RELAUNCH
        # 正常由子进程跑完升级后检查再确认；新版本跑了这么久还没确认（检查卡住了），算成功。
        # 刚装完更新退出的是旧版本（current 已经变了），它跑得再久也不算：以前这里会把新版本的待确认记录直接删掉，回滚保护形同虚设
        if ran >= CONFIRM_FALLBACK and (not layout.INSTALLED or layout.read_current() == started_version):
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
        if code == PORT_BUSY:                          # 端口被别的程序占了：子进程已经把原因和办法打出来了，重来多少次都一样
            _log("端口被占用，不再自动重启。照上面的办法处理好之后再重新打开。")
            _wait_for_user()
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
