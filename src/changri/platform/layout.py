"""安装目录的样子（打包安装后；开发时没有这些，INSTALLED 为 False）：

<安装目录>/
  runtime/<运行环境编号>/   独立的 Python + 全部依赖 + 字体（很少变：依赖或字体变了才换新编号）
  versions/<版本>/          程序代码：changri/、changri_api.py、RUNTIME（这一版要用哪个运行环境）
  current                   当前用哪个版本（一行文本）
  data/                     数据（数据库、config.toml、备份、用户扩展），更新永远不碰
  Start-Changri.bat / changri   启动脚本：读 current 和 RUNTIME，找到对应的 Python 运行

更新 = 把新版本解压到 versions/<新版本>/（需要的话再放一个新的 runtime/），最后改写 current。
正在运行的文件一个都不覆盖，所以 Windows 上也不会被锁住；回滚 = 把 current 改回去。
"""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from ..paths import APP_ROOT, PKG_DIR

VERSIONS = APP_ROOT / "versions"
RUNTIMES = APP_ROOT / "runtime"
CURRENT = APP_ROOT / "current"
INSTALLED = CURRENT.is_file() and VERSIONS.is_dir()
VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
RUNTIME_RE = re.compile(r"^r[0-9a-f]{8,16}$")
KEEP_VERSIONS = 3


def package_version() -> str:
    try:
        return (PKG_DIR / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def read_current() -> str:
    try:
        v = CURRENT.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return v if VERSION_RE.match(v) else ""


def write_current(version: str) -> None:
    if not VERSION_RE.match(version) or not (VERSIONS / version).is_dir():
        raise ValueError(f"没有这个版本：{version}")
    tmp = CURRENT.with_name("current.tmp")
    tmp.write_text(version + "\n", encoding="utf-8")
    os.replace(tmp, CURRENT)


def runtime_of(version: str) -> str:
    try:
        rt = (VERSIONS / version / "RUNTIME").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return rt if RUNTIME_RE.match(rt) else ""


def current_runtime() -> str:
    """正在跑的这个 Python 属于哪个运行环境（开发时为空）。"""
    try:
        p = Path(sys.prefix).resolve()
        if p.parent == RUNTIMES.resolve() and RUNTIME_RE.match(p.name):
            return p.name
    except OSError:
        pass
    return ""


def platform_tag() -> str:
    """这台机器用哪种安装包：windows-x64 / linux-x64 / linux-arm64 / macos-arm64（最后一个只用于开发测试）。"""
    m = platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else "x64" if m in ("x86_64", "amd64") else m
    osn = "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux"
    return f"{osn}-{arch}"


def python_in(runtime: str) -> Path:
    base = RUNTIMES / runtime
    return base / "python.exe" if platform_tag().startswith("windows") else base / "bin" / "python3"


# 大的媒体文件（字体、音频、大图）：安装包里放在运行环境的 changri-media/ 下（路径和在包里时一样），
# 不进每次的更新包。哪些算「大」由 packaging/build.py 的 is_media() 决定。
MEDIA_DIR = Path(sys.prefix) / "changri-media"


def asset(rel: str) -> Path:
    """包里的文件（相对 changri/ 的路径）：包里有就用包里的（开发时），没有就去运行环境里找。"""
    p = PKG_DIR / rel
    return p if p.exists() else MEDIA_DIR / rel


def fonts_dir() -> Path:
    return asset("assets/fonts/SansSC-Regular.otf").parent       # 按字体文件找：包里还留着很小的授权说明


def installed_versions() -> list[str]:
    if not VERSIONS.is_dir():
        return []
    vs = [p.name for p in VERSIONS.iterdir() if p.is_dir() and VERSION_RE.match(p.name)]
    return sorted(vs, key=lambda v: tuple(int(x) for x in v.split(".")))


def prune(keep_also: tuple[str, ...] = ()) -> list[str]:
    """旧版本只留最近 KEEP_VERSIONS 个（当前的和 keep_also 里的一定留）；没有版本在用的运行环境也删掉。返回删了什么。"""
    import shutil
    cur = read_current()
    vs = installed_versions()
    keep = set(vs[-KEEP_VERSIONS:]) | {cur, *keep_also}
    removed = []
    for v in vs:
        if v not in keep:
            shutil.rmtree(VERSIONS / v, ignore_errors=True)
            removed.append(f"versions/{v}")
    used = {runtime_of(v) for v in installed_versions()} | {current_runtime()}
    if RUNTIMES.is_dir():
        for p in RUNTIMES.iterdir():
            if p.is_dir() and RUNTIME_RE.match(p.name) and p.name not in used:
                shutil.rmtree(p, ignore_errors=True)
                removed.append(f"runtime/{p.name}")
    for p in list(VERSIONS.glob("*.tmp")) + list(RUNTIMES.glob("*.tmp")) if INSTALLED else []:
        shutil.rmtree(p, ignore_errors=True)         # 上次没解压完的半成品
    return removed


def ensure_win_root_scripts() -> str | None:
    """Windows 安装目录根上的 .bat 在线更新不会换，新加的入口要自己补上（现在只有 Reset-Owner-Password.bat）。
    缺了才写；返回给看门进程打印的一句话，没写返回 None。"""
    if not (INSTALLED and sys.platform.startswith("win")):
        return None
    dest = APP_ROOT / "Reset-Owner-Password.bat"
    if dest.exists():
        return None
    try:
        dest.write_bytes((PKG_DIR / "scripts" / "Reset-Owner-Password.bat").read_bytes())   # 打包时已经存成 GBK + CRLF
    except OSError:
        return None
    return "已在安装文件夹里放好 Reset-Owner-Password.bat（主账号忘了密码时双击）。"
