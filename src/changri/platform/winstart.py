"""Windows 双击 Start-Changri.bat 时，自动设好开机自启，并交给后台任务运行（看门进程 supervisor.run 一开始调用）。

以前要右键 Install-Autostart.bat「以管理员身份运行」才装；很多人只双击过，服务器一重启长日bot 就没人拉起来了。
现在双击时：
- 已经设过开机自启：先让任务跑起来（schtasks /Run，任务已经在跑就什么都不发生），设置平台起来了就打开浏览器、关掉窗口；
  拉不起来（双击的窗口没权限运行 SYSTEM 的任务等）照旧在窗口里运行。
- 没设过：弹一次 Windows 的「是否允许此应用对你的设备进行更改」（UAC），同意了就以管理员身份运行 service install
  （建任务、放行防火墙、在后台启动），等设置平台起来后打开浏览器、关掉窗口；点了「否」或没装成，照旧在窗口里运行，提示一句。
- 本来就是管理员（Windows Server 内置的 Administrator 常常这样）：不弹窗，直接装。
已经在窗口里跑着的（多半是点了后台「更新」自己重新拉起的）不在这里装：作者定的，更新时不自动做。
改成主账号打开后台时弹窗提醒（need_popup，base.html 的 #as-dlg），让他们自己点或者去服务器上装。
不会出现两个长日bot 抢端口：交给任务的这个窗口自己不再启动；之后看门进程每次拉起前也会先看端口上是不是已经有一个在跑。

不做的情况：不是 Windows、不是安装包、开机自启自己起来的（CHANGRI_SERVICE=1）；
用 Remove-Autostart.bat 取消过开机自启（data/autostart_off，再装一次就删掉）；
看门进程 1 分钟内刚退出过（data/supervise_exit）、刚装完更新还没确认（update_pending.json）——
那是 Start-Changri.bat 在更新后、崩溃后自己重新拉起，不是有人双击，没人看着，不能弹 UAC 卡住。
万一还是弹了没人点：最多等 UAC_WAIT 秒就照旧在窗口里运行（之后有人点了「是」也不会多出一个：任务起来时看到端口上已经有长日bot 就退出）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

from ..paths import APP_ROOT, DATA_DIR, PKG_DIR
from . import layout

OFF_MARK = DATA_DIR / "autostart_off"          # 用 Remove-Autostart.bat 取消过：双击时不再自动装
EXIT_MARK = DATA_DIR / "supervise_exit"        # 看门进程退出的时间：Start-Changri.bat 马上又拉起时认出来
RECENT_EXIT = 60
UAC_WAIT = 120                                 # UAC 弹窗最多等多少秒没人点，就先在窗口里运行
WAIT_UP = 90                                   # 交给任务后最多等设置平台多少秒


def _log(msg: str) -> None:
    print(time.strftime("%m-%d %H:%M:%S"), "[开机自启]", msg, flush=True)


def mark_exit() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EXIT_MARK.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def _relaunched() -> bool:
    try:
        return time.time() - EXIT_MARK.stat().st_mtime < RECENT_EXIT
    except OSError:
        return False


def _update_pending() -> bool:
    try:
        from ..core import updater
        return updater.read_pending() is not None
    except Exception:  # noqa: BLE001
        return False


def should_try() -> bool:
    return _base_ok() and not _relaunched() and not _update_pending()


def _base_ok() -> bool:
    return sys.platform.startswith("win") and layout.INSTALLED and os.environ.get("CHANGRI_SERVICE") != "1" \
        and not OFF_MARK.exists()


_need_cache: dict = {"t": 0.0, "v": None}
NEED_TTL = 300


def need_popup() -> dict | None:
    """后台弹窗用：Windows 安装包、在窗口里跑（不是开机自启起来的）、没装任务、没用 Remove-Autostart 取消过 → {"admin": 这个进程是不是管理员, "port": 设置平台端口}。
    schtasks 查一次要一百来毫秒，结果缓存 5 分钟；装好后 forget_need() 清掉。"""
    if not _base_ok():
        return None
    now = time.monotonic()
    if now - _need_cache["t"] > NEED_TTL:
        v = None if _task_exists() else {"admin": _is_admin(), "port": _admin_port() or 5091}
        _need_cache.update(t=now, v=v)
    return _need_cache["v"]


def forget_need() -> None:
    _need_cache.update(t=0.0, v=None)


def install_from_admin(local: bool) -> str:
    """后台弹窗里点「现在设好」。这个进程是管理员：直接装（任务马上跑一次，看到端口上已经有长日bot 就退出，
    窗口照常开着，关掉后 5 分钟内任务接上）。不是管理员、而且是在服务器上打开的后台：弹 UAC，在后台线程里等。
    都不是：返回去服务器上怎么做。返回给页面的一句话。"""
    if not _base_ok():
        return "不用设：这里不是 Windows 安装包，或者已经是开机自启在运行。"
    if _is_admin():
        from . import service
        try:
            service.install()
        except Exception as e:  # noqa: BLE001
            return f"没设上（{e}）。可以到服务器上右键 Install-Autostart.bat，选「以管理员身份运行」。"
        finally:
            forget_need()
        return "已设为开机自动运行：服务器重启后长日bot 会自己在后台起来（没有窗口）。现在开着的黑色窗口可以继续开着，关掉以后 5 分钟内后台会接上。"
    if not local:
        return "要在服务器上做：右键安装文件夹里的 Install-Autostart.bat，选「以管理员身份运行」。"

    def go():
        try:
            _elevate_install()
        finally:
            forget_need()
    threading.Thread(target=go, name="autostart-uac", daemon=True).start()
    return "服务器上弹出了 Windows 的确认「是否允许此应用对你的设备进行更改」，点「是」就设好了。过一会儿刷新这个页面，提示不见了就是设好了。"


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _elevate_install() -> int | None:
    """弹 UAC，以管理员身份运行 scripts/autostart_elevated.bat（里面就是 service install），等它跑完。
    返回它的退出码；用户点了「否」、弹不出来返回 None。"""
    import ctypes
    from ctypes import wintypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    sei = SEI()
    sei.cbSize = ctypes.sizeof(SEI)
    sei.fMask = 0x00000040                                  # SEE_MASK_NOCLOSEPROCESS：要拿进程句柄等它结束
    sei.lpVerb = "runas"
    sei.lpFile = str(PKG_DIR / "scripts" / "autostart_elevated.bat")
    sei.lpParameters = f'"{APP_ROOT}"'                      # Windows 路径里不会有引号
    sei.lpDirectory = str(APP_ROOT)
    sei.nShow = 7                                           # SW_SHOWMINNOACTIVE：一闪的黑窗口缩在任务栏
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)) or not sei.hProcess:
        return None                                         # 1223 = 用户点了「否」；别的错误也一样照旧前台跑
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject(sei.hProcess, 180_000)
    code = wintypes.DWORD()
    k32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    k32.CloseHandle(sei.hProcess)
    return code.value


def _task_exists() -> bool:
    from .service import TASK, _run
    try:
        return _run(["schtasks", "/Query", "/TN", TASK], check=False).returncode == 0
    except OSError:
        return False


def _run_task() -> bool:
    from .service import TASK, _run
    try:
        return _run(["schtasks", "/Run", "/TN", TASK], check=False).returncode == 0
    except OSError:
        return False


def _admin_port() -> int | None:
    try:
        from .. import config
        return config.admin_port()
    except Exception:  # noqa: BLE001
        return None


def _wait_up(port: int, seconds: int = WAIT_UP) -> bool:
    from .portcheck import changri_on
    end = time.monotonic() + seconds
    while True:
        if changri_on(port):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(2)


def _open(port: int) -> None:
    try:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")
    except Exception:  # noqa: BLE001
        pass


def _install() -> bool:
    """装开机自启。装成了（任务建好了）返回 True。"""
    if _is_admin():
        from . import service
        try:
            _log(service.install())
        except Exception as e:  # noqa: BLE001
            _log(f"没装成：{e}")
            return False
        return _task_exists()
    _log("第一次运行：正在设置开机自动运行，Windows 会问「是否允许此应用对你的设备进行更改」，请点「是」。")
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("code", _elevate_install()), name="autostart-uac", daemon=True)
    t.start()
    t.join(UAC_WAIT)
    if t.is_alive():
        _log("一直没人点，这次先在这个窗口里运行。之后点了「是」也没关系：后台任务看到这里已经在跑，不会再开一个。")
        return False
    code = box.get("code")
    if code is None:
        _log("没有同意，这次先在这个窗口里运行（关掉窗口就停止，服务器重启后不会自己起来）。"
             "下次双击还会再问；也可以右键 Install-Autostart.bat，选「以管理员身份运行」。")
        return False
    if code != 0 or not _task_exists():
        _log(f"开机自启没装成（{code}），这次先在这个窗口里运行。可以右键 Install-Autostart.bat，选「以管理员身份运行」再试一次。")
        return False
    return True


def handoff() -> bool:
    """双击启动时调用。返回 True = 已经交给开机自启的后台任务、设置平台也起来了，这个窗口不用再启动长日bot。"""
    if not should_try():
        return False
    port = _admin_port()
    if port is None:
        return False
    from .portcheck import changri_on
    if changri_on(port):
        return False                                  # 已经有一个在跑：看门进程自己会打开设置平台、退出
    if _task_exists():
        if not _run_task():
            return False                              # 拉不起任务（没权限等）：照旧在窗口里跑
        installed_now = False
    else:
        if not _install():
            return False
        installed_now = True
    if not _wait_up(port):
        _log("后台任务没在 90 秒内起来，这次先在这个窗口里运行。")
        return False
    _open(port)
    _log(("已设为开机自动运行，" if installed_now else "") + "长日bot 在后台运行了（没有窗口），服务器重启后也会自己起来。"
         f"设置平台：http://127.0.0.1:{port}/（已经在浏览器里打开）。这个窗口 10 秒后自己关掉。")
    time.sleep(10)
    return True
