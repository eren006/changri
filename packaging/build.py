#!/usr/bin/env python3
"""打包发布（只在发布者的电脑上运行；需要联网下载独立 Python 和依赖，下载过的会缓存）。

    .venv/bin/python packaging/build.py --notes "这次更新了什么"
    .venv/bin/python packaging/build.py --platforms macos-arm64 --test-key     # 本机端到端测试用，不要发布

产出 release_out/<版本>/site/，按 system.changri.work 上的路径放好，整个传上去即可（脚本最后会打印命令）：
  latest.json                         在线更新清单（签名）
  get.sh                              Linux 一键安装（下载地址和校验值写死在里面）
  install/index.html                  安装说明页（网址 /install）
  downloads/changri-bot-<版本>-<平台>.zip|tar.gz   完整安装包（自带 Python）
  downloads/app-<版本>.zip            更新包：只有程序代码，几百 KB
  downloads/runtime-<编号>-<平台>.tar.gz   运行环境（Python + 依赖 + 字体）：依赖变了才会有新编号
  media/...                           按需下载的背景音乐、季末海报皮肤图（src/changri/remote_media.json 里登记的文件）

运行环境编号 = Python 版本 + requirements.lock + 字体 + 精简规则 的哈希。不变的话，已安装的机器更新时不用再下载它。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
PKG = SRC / "changri"
CACHE = Path(os.environ.get("CHANGRI_BUILD_CACHE") or Path.home() / ".cache" / "changri-build")
BASE_URL = "https://system.changri.work"
DEFAULT_KEY = Path.home() / ".changri_release" / "private.pem"

PBS_TAG, PY_VER = "20261110", "3.12.16"       # python-build-standalone 的发布标签和 Python 版本
PBS_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}"
PRUNE_RULES = "v1"                            # 改了下面的精简规则就加一，运行环境编号会跟着变

# Linux：最低 glibc 2.28（Ubuntu 20.04+ / Debian 10+ / CentOS 8+）。Pillow 13 起 Linux 只有 manylinux_2_28 的包；get.sh 会先检查
PLATFORMS = {
    "windows-x64": {"triple": "x86_64-pc-windows-msvc", "pip": ["win_amd64"], "archive": "zip",
                    "env": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"}},
    "linux-x64": {"triple": "x86_64-unknown-linux-gnu", "pip": ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_26_x86_64", "manylinux_2_25_x86_64", "manylinux_2_24_x86_64", "manylinux_2_23_x86_64", "manylinux_2_22_x86_64", "manylinux_2_21_x86_64", "manylinux_2_20_x86_64", "manylinux_2_19_x86_64", "manylinux_2_18_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"], "archive": "tar",
                  "env": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "x86_64"}},
    "linux-arm64": {"triple": "aarch64-unknown-linux-gnu", "pip": ["manylinux_2_28_aarch64", "manylinux_2_27_aarch64", "manylinux_2_26_aarch64", "manylinux_2_25_aarch64", "manylinux_2_24_aarch64", "manylinux_2_23_aarch64", "manylinux_2_22_aarch64", "manylinux_2_21_aarch64", "manylinux_2_20_aarch64", "manylinux_2_19_aarch64", "manylinux_2_18_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64"], "archive": "tar",
                    "env": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "aarch64"}},
    # 只用于在 Mac 上端到端测试安装、启动、更新、回滚；不对外发布
    "macos-arm64": {"triple": "aarch64-apple-darwin", "pip": ["macosx_11_0_arm64", "macosx_12_0_arm64", "macosx_13_0_arm64", "macosx_14_0_arm64",
                                                               "macosx_10_9_universal2", "macosx_11_0_universal2"], "archive": "tar",
                    "env": {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix", "platform_machine": "arm64"}},
}
PUBLIC = ("windows-x64", "linux-x64", "linux-arm64")

# 运行时用不到的标准库和文件：删掉能省一大截
PRUNE_POSIX = ["include", "share", "lib/pkgconfig", "lib/python3.12/test", "lib/python3.12/idlelib", "lib/python3.12/tkinter",
               "lib/python3.12/turtledemo", "lib/python3.12/ensurepip", "lib/python3.12/lib2to3", "lib/python3.12/site-packages/pip",
               "bin/idle3", "bin/idle3.12", "bin/pydoc3", "bin/pydoc3.12", "bin/2to3", "bin/2to3-3.12", "bin/pip", "bin/pip3", "bin/pip3.12"]
PRUNE_POSIX_GLOB = ["lib/python3.12/config-3.12-*", "lib/libtcl*", "lib/libtk*", "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*",
                    "lib/python3.12/lib-dynload/_tkinter*"]
PRUNE_WIN = ["include", "libs", "tcl", "Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/ensurepip", "Lib/lib2to3",
             "Lib/site-packages/pip", "Scripts", "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll"]


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"  下载 {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "changri-build"}), timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


# ---------- 运行环境 ----------

def version() -> str:
    return (PKG / "VERSION").read_text(encoding="utf-8").strip()


MEDIA_SUFFIXES = {".otf", ".ttf", ".ttc", ".m4a", ".mp3", ".ogg", ".wav"}
MEDIA_MIN_SIZE = 512 * 1024


def is_media(p: Path) -> bool:
    """大的媒体文件：放进运行环境（changri-media/），不进每次的更新包。程序用 platform.layout.asset() 找它们。"""
    return p.suffix.lower() in MEDIA_SUFFIXES or p.stat().st_size >= MEDIA_MIN_SIZE


REMOTE_MANIFEST = PKG / "remote_media.json"


def remote_manifest() -> dict[str, dict]:
    return json.loads(REMOTE_MANIFEST.read_text(encoding="utf-8")) if REMOTE_MANIFEST.exists() else {}


def is_remote(p: Path) -> bool:
    """按需下载的文件（背景音乐）：哪个包都不带，传到主页 media/ 下，程序第一次用到时下载（platform/remote_media.py）。"""
    return p.relative_to(PKG).as_posix() in remote_manifest()


def package_files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store" and not is_remote(p))


def media_files() -> list[Path]:
    return [p for p in package_files() if is_media(p)]


def refresh_remote_manifest() -> None:
    """按包里现有的音乐文件、季末海报皮肤图重新算校验值（换了、加了之后运行一次）。"""
    out = {}
    for f in sorted((PKG / "phone" / "static" / "masquerade").glob("*.m4a")) + sorted((PKG / "assets" / "skins").glob("*.png")):
        out[f.relative_to(PKG).as_posix()] = {"sha256": sha256_of(f), "size": f.stat().st_size}
    REMOTE_MANIFEST.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"已更新 {REMOTE_MANIFEST.relative_to(ROOT)}：{len(out)} 个文件")


def copy_remote_media(site: Path) -> int:
    total = 0
    for rel, item in remote_manifest().items():
        src = PKG / rel
        if not src.is_file() or sha256_of(src) != item["sha256"]:
            sys.exit(f"{rel} 和 remote_media.json 对不上：换了文件请先运行 build.py --refresh-remote-media")
        dst = site / "media" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        total += item["size"]
    return total


def runtime_id() -> str:
    h = hashlib.sha256()
    for part in (PBS_TAG, PY_VER, PRUNE_RULES, (HERE / "requirements.lock").read_text(encoding="utf-8")):
        h.update(part.encode() + b"\0")
    for f in media_files():
        h.update(f.relative_to(PKG).as_posix().encode() + sha256_of(f).encode())
    return "r" + h.hexdigest()[:10]


def lock_for(plat: str) -> list[str]:
    """requirements.lock 里适用于这个平台的行（条件按目标平台判断，不按本机）。"""
    try:
        from packaging.markers import Marker
    except ImportError:
        from pip._vendor.packaging.markers import Marker
    env = {"python_version": PY_VER.rsplit(".", 1)[0], "python_full_version": PY_VER, "platform_python_implementation": "CPython",
           "implementation_name": "cpython", "extra": "", **PLATFORMS[plat]["env"]}
    out = []
    for line in (HERE / "requirements.lock").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        req, _, marker = line.partition(";")
        if not marker.strip() or Marker(marker.strip()).evaluate(env):
            out.append(req.strip())
    return out


def pbs_archive(plat: str) -> Path:
    name = f"cpython-{PY_VER}+{PBS_TAG}-{PLATFORMS[plat]['triple']}-install_only_stripped.tar.gz"
    path = fetch(f"{PBS_URL}/{name}", CACHE / "pbs" / name)
    sums = fetch(f"{PBS_URL}/SHA256SUMS", CACHE / "pbs" / f"SHA256SUMS-{PBS_TAG}")
    want = next((ln.split()[0] for ln in sums.read_text().splitlines() if ln.strip().endswith(name)), "")
    if not want or sha256_of(path) != want:
        path.unlink(missing_ok=True)
        sys.exit(f"{name} 的校验值不对（可能没下完整），已删掉，请重新运行。")
    return path


def _remove(base: Path, rel: str) -> None:
    p = base / rel
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


def build_runtime(plat: str, rid: str, dest: Path) -> None:
    """dest = 运行环境目录（里面直接就是 bin/ 或 python.exe）。"""
    log(f"  准备运行环境 {rid}（{plat}）")
    with tempfile.TemporaryDirectory() as tmp:
        def keep(member, path):
            # share/、include/ 反正要删；Linux 版的 share/terminfo 里还有循环链接，安全过滤会报错，直接跳过
            if member.name.startswith(("python/share/", "python/include/")):
                return None
            return tarfile.data_filter(member, path)
        with tarfile.open(pbs_archive(plat)) as t:
            t.extractall(tmp, filter=keep)
        shutil.move(str(Path(tmp) / "python"), dest)
    win = plat.startswith("windows")
    for rel in PRUNE_WIN if win else PRUNE_POSIX:
        _remove(dest, rel)
    if not win:
        for pattern in PRUNE_POSIX_GLOB:
            for p in dest.glob(pattern):
                _remove(dest, str(p.relative_to(dest)))
    site = dest / ("Lib/site-packages" if win else "lib/python3.12/site-packages")
    reqs = Path(tempfile.mkstemp(suffix=".txt")[1])
    reqs.write_text("\n".join(lock_for(plat)) + "\n", encoding="utf-8")
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-deps", "--no-compile",
           "--only-binary=:all:", "--target", str(site), "--python-version", PY_VER.rsplit(".", 1)[0], "--implementation", "cp",
           "--abi", "cp312", *[x for p in PLATFORMS[plat]["pip"] for x in ("--platform", p)], "-r", str(reqs)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    reqs.unlink(missing_ok=True)
    if r.returncode != 0:
        sys.exit(f"装依赖失败（{plat}）：\n{r.stderr[-3000:]}")
    _remove(site, "bin")                       # pip 生成的命令行脚本，用不到
    for p in list(dest.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)   # 第一次运行时会重新生成
    for f in media_files():
        target = dest / "changri-media" / f.relative_to(PKG)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
    for lic in (PKG / "assets" / "fonts").glob("*.txt"):          # 字体的授权说明跟着字体走
        shutil.copy2(lic, dest / "changri-media" / "assets" / "fonts" / lic.name)
    _smoke_test(plat, dest)
    (dest / "CHANGRI_RUNTIME.txt").write_text(f"{rid}\nPython {PY_VER} ({PBS_TAG})\n" + "\n".join(lock_for(plat)) + "\n", encoding="utf-8")


def _smoke_test(plat: str, dest: Path) -> None:
    """目标平台就是本机时，用刚做好的运行环境把程序依赖的库都导入一遍（别的平台只能等真机测试）。"""
    import platform as _pf
    host = ("windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux") + "-" + \
        ("arm64" if _pf.machine().lower() in ("arm64", "aarch64") else "x64")
    if host != plat:
        return
    py = dest / ("python.exe" if plat.startswith("windows") else "bin/python3")
    code = ("import nonebot; nonebot.init(driver='~fastapi+~httpx+~websockets'); "        # 和程序实际用的驱动组合一样
            "from nonebot.adapters.qq import Adapter; nonebot.get_driver().register_adapter(Adapter); "
            "import nonebot_plugin_apscheduler, flask, a2wsgi, PIL, cryptography, dotenv")
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True, env={"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", "")})
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        sys.exit(f"运行环境自检没通过（{plat}，退出码 {r.returncode}），多半是 requirements.lock 少了包：\n{(r.stderr or r.stdout)[-2000:]}")
    log("  运行环境自检通过（本机平台，核心库都能导入）")


def runtime_dir(plat: str, rid: str) -> Path:
    """缓存好的运行环境目录（同一编号只做一次）。"""
    d = CACHE / "runtimes" / f"{rid}-{plat}"
    if not (d / "CHANGRI_RUNTIME.txt").exists():
        shutil.rmtree(d, ignore_errors=True)
        d.parent.mkdir(parents=True, exist_ok=True)
        build_runtime(plat, rid, d)
    return d


# ---------- 打包 ----------

# 打包结果要「同样的内容 → 一模一样的文件」：重打以后服务器上已有的/传了一半的文件还能续传、能跳过。
# 所以压缩包里不记打包时的时间和本机用户：时间统一成 FIXED_MTIME，属主统一成 root，gzip 头里不记时间和文件名。
FIXED_MTIME = 1735689600            # 2025-01-01 00:00 UTC
FIXED_ZIP_TIME = (2025, 1, 1, 0, 0, 0)


def _norm_tarinfo(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.mtime = FIXED_MTIME
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.pax_headers = {}
    return ti


class _ReproTar:
    """with _ReproTar(out) as t: t.add(...)：固定时间、固定属主、gzip 头不记时间。"""

    def __init__(self, out: Path):
        import gzip
        self._raw = open(out, "wb")
        self._gz = gzip.GzipFile(filename="", fileobj=self._raw, mode="wb", compresslevel=9, mtime=0)
        self._tar = tarfile.open(fileobj=self._gz, mode="w", format=tarfile.GNU_FORMAT)

    def add(self, path: Path, arcname: str) -> None:
        self._tar.add(path, arcname=arcname, filter=_norm_tarinfo)        # 目录会按文件名排序递归加进去

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._tar.close()
        self._gz.close()
        self._raw.close()


def _zip_add(z: zipfile.ZipFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _tar_dir(src: Path, out: Path, prefix: str = "") -> None:
    """src 里的东西打进 tar.gz；prefix 为空时不带顶层目录（更新器解压到 runtime/<编号>/ 里）。保留执行权限和链接。"""
    with _ReproTar(out) as t:
        for p in sorted(src.iterdir()):
            t.add(p, arcname=f"{prefix}{p.name}" if prefix else p.name)


def _zip_dir(src: Path, out: Path, prefix: str) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                _zip_add(z, f"{prefix}{p.relative_to(src).as_posix()}", p.read_bytes(), p.stat().st_mode)


def _local_secrets() -> dict[str, str]:
    """发布者本机上不该进安装包的东西：家目录路径、用户名、.env 里的口令、~/.changri_secrets 里的值。只拿来比对，不打印。"""
    import re
    home = Path.home()
    out = {"本机家目录路径": str(home), "本机用户名": home.name}
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(errors="ignore").splitlines():
            k, _, v = line.partition("=")
            v = v.strip().strip("\"'")
            if len(v) >= 8 and any(t in k.upper() for t in ("SECRET", "TOKEN", "PASSWORD", "KEY", "QQ_BOTS")):
                out[f".env 的 {k.strip()}"] = v
    sec = home / ".changri_secrets"
    if sec.is_dir():
        for f in sec.iterdir():
            if f.is_file():
                for i, v in enumerate(re.findall(r"[A-Za-z0-9_\-]{10,}", f.read_text(errors="ignore"))):
                    out[f"~/.changri_secrets/{f.name} 第 {i + 1} 个值"] = v
    return out


def check_no_secrets(files: dict[str, bytes]) -> None:
    """打进包里的程序文件（不含第三方运行环境）里不能出现发布者本机的口令、用户名、路径和私钥；有就停止打包。"""
    import re
    bad = []
    for name, value in _local_secrets().items():
        needle = value.encode()
        for rel, data in files.items():
            if needle and needle in data:
                bad.append(f"{rel}：含有{name}")
    for rel, data in files.items():
        if re.search(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{40,}", data):
            bad.append(f"{rel}：像是一段私钥")
        if Path(rel).name in (".env", ".secret_key", ".setup_code") or rel.endswith((".db", ".pem", ".key")):
            bad.append(f"{rel}：不该打进安装包的文件")
    if bad:
        sys.exit("安装包里发现敏感内容，已停止打包：\n  " + "\n  ".join(sorted(set(bad))[:30]))
    log(f"  敏感信息检查通过（{len(files)} 个程序文件）")


def app_files(rid: str, pubkey: str | None) -> dict[str, bytes]:
    """更新包 / versions/<版本>/ 里的文件：changri/、changri_api.py、RUNTIME。"""
    files: dict[str, bytes] = {}
    for p in package_files():
        if not is_media(p):
            files[p.relative_to(SRC).as_posix()] = bat_bytes(p.read_text(encoding="utf-8")) if p.suffix == ".bat" else p.read_bytes()
    files["changri_api.py"] = (SRC / "changri_api.py").read_bytes()
    files["RUNTIME"] = rid.encode()
    if pubkey:      # 测试包：换成临时公钥，好让本机测试用临时私钥签的更新包
        files["changri/core/update_pubkey.py"] = f'"""测试包专用的临时公钥——这个包不能发布。"""\nPUBLIC_KEY_B64 = "{pubkey}"\n'.encode()
    return files


def crlf(text: str, encoding: str = "utf-8") -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode(encoding)


def bat_bytes(text: str) -> bytes:
    """.bat 存成 GBK + CRLF，不再 chcp 65001。
    UTF-8 的 .bat 在 65001 下，cmd 会把含中文的行读错位：「找不到程序文件」被截成「不到程序文件……'is not recognized'」，
    前面的 call 根本没执行。中文 Windows 的 cmd 默认就是 GBK（936），按它存最稳。源文件仍是 UTF-8，方便编辑。"""
    if "chcp 65001" in text:
        sys.exit(".bat 里不要再写 chcp 65001：打包时会存成 GBK，切到 65001 中文反而会乱。")
    try:
        return crlf(text, "gbk")
    except UnicodeEncodeError as e:
        sys.exit(f".bat 里有 GBK 存不下的字符 {text[e.start:e.end]!r}，换个写法。")


def fill(text: str, **kw) -> str:
    for k, v in kw.items():
        text = text.replace(f"@{k}@", v)
    return text


def full_package(plat: str, ver: str, rid: str, rt: Path, app: dict[str, bytes], out_dir: Path, base: str) -> Path:
    name = f"changri-bot-{ver}-{plat}"
    with tempfile.TemporaryDirectory() as tmp:
        top = Path(tmp) / name
        shutil.copytree(rt, top / "runtime" / rid, symlinks=True)
        for rel, data in app.items():
            f = top / "versions" / ver / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        (top / "current").write_text(ver + "\n", encoding="utf-8")
        if plat.startswith("windows"):
            for f in (HERE / "windows").iterdir():
                text = fill(f.read_text(encoding="utf-8"), VERSION=ver, BASE=base)
                data = bat_bytes(text) if f.suffix == ".bat" else crlf(text)   # .bat 要 CRLF，不然 goto / 标签会出怪问题
                if f.suffix == ".txt":
                    data = b"\xef\xbb\xbf" + data                       # 带 BOM，老记事本也不乱码
                (top / f.name).write_bytes(data)
            out = out_dir / f"{name}.zip"
            _zip_dir(top, out, f"{name}/")
        else:
            shutil.copy2(HERE / "linux" / "changri", top / "changri")
            (top / "changri").chmod(0o755)
            (top / "README.txt").write_text(fill((HERE / "linux" / "README.txt").read_text(encoding="utf-8"), VERSION=ver, BASE=base), encoding="utf-8")
            out = out_dir / f"{name}.tar.gz"
            with _ReproTar(out) as t:
                t.add(top, arcname=name)
    return out


# ---------- 签名 ----------

def load_key(path: Path | None, test: bool):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if test:
        # 测试密钥存在构建缓存里，前后两次测试构建用同一把，才能测「从上一个测试版更新到下一个」
        tk = CACHE / "test_key.pem"
        if not tk.exists():
            tk.parent.mkdir(parents=True, exist_ok=True)
            tk.write_bytes(Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                      serialization.NoEncryption()))
        key = serialization.load_pem_private_key(tk.read_bytes(), password=None)
    else:
        if not path or not path.exists():
            sys.exit(f"找不到签名私钥 {path}。正式发布必须用它签名；本机测试请加 --test-key。")
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    if not test:
        sys.path.insert(0, str(SRC))
        from changri.core.update_pubkey import PUBLIC_KEY_B64
        if pub != PUBLIC_KEY_B64:
            sys.exit("私钥和 src/changri/core/update_pubkey.py 里的公钥不是一对：这样签出来的包，已安装的机器会拒绝更新。")
    return key, pub


def sign(key, message: bytes) -> str:
    return base64.b64encode(key.sign(message)).decode()


# ---------- 更新说明 ----------

CHANGELOG = ROOT / "packaging" / "changelog.json"


def load_changelog() -> list[dict]:
    """packaging/changelog.json：每个版本一条 {version, date, features:[新功能…], fixes:[修 bug…]}，新的在前。
    最近 20 条随 latest.json 发出去，设置平台「更新」页按版本、按「新功能 / 修 bug」分开列。"""
    try:
        rows = json.loads(CHANGELOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("version")] if isinstance(rows, list) else []


def notes_from(entry: dict) -> str:
    """给 0.21.6 及更早的版本看的纯文字说明（它们只认 notes）。"""
    parts = []
    if entry.get("features"):
        parts.append("新功能：\n" + "\n".join("· " + x for x in entry["features"]))
    if entry.get("fixes"):
        parts.append("修 bug：\n" + "\n".join("· " + x for x in entry["fixes"]))
    return "\n\n".join(parts)


# ---------- 主流程 ----------

def main() -> None:
    ap = argparse.ArgumentParser(description="打包长日bot")
    ap.add_argument("--platforms", default=",".join(PUBLIC), help=f"逗号分隔，可选：{', '.join(PLATFORMS)}")
    ap.add_argument("--notes", default="", help="更新说明（会显示在设置平台的「更新」页）")
    ap.add_argument("--key", default=str(DEFAULT_KEY), help="签名私钥")
    ap.add_argument("--test-key", action="store_true", help="用临时密钥签名、并把临时公钥打进包里：只给本机测试，不能发布")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--out", default=str(ROOT / "release_out"))
    ap.add_argument("--refresh-remote-media", action="store_true", help="重新生成按需下载文件（背景音乐）的清单后退出")
    a = ap.parse_args()
    if a.refresh_remote_media:
        refresh_remote_manifest()
        return

    sys.path.insert(0, str(SRC))
    from changri.core import updater

    plats = [p.strip() for p in a.platforms.split(",") if p.strip()]
    if bad := [p for p in plats if p not in PLATFORMS]:
        sys.exit(f"不认识的平台：{bad}")
    ver, rid, base = version(), runtime_id(), a.base_url.rstrip("/")
    key, pub = load_key(Path(a.key), a.test_key)
    out = Path(a.out) / (ver + ("-test" if a.test_key else ""))
    site = out / "site"
    shutil.rmtree(out, ignore_errors=True)
    (site / "downloads").mkdir(parents=True)
    log(f"长日bot {ver}，运行环境 {rid}，平台：{', '.join(plats)}" + ("（测试密钥，不能发布）" if a.test_key else ""))

    app = app_files(rid, pub if a.test_key else None)
    check_no_secrets(app)
    app_zip = site / "downloads" / f"app-{ver}.zip"
    with zipfile.ZipFile(app_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, data in sorted(app.items()):
            _zip_add(z, rel, data)
    app_sha = sha256_of(app_zip)
    changes = load_changelog()
    entry = next((c for c in changes if c["version"] == ver), None)
    notes = a.notes if a.notes and a.notes != ver else (notes_from(entry) if entry else a.notes)
    if not entry:
        log(f"  提示：packaging/changelog.json 里没有 {ver} 这一条，「更新」页只能显示 --notes 的文字。")
    manifest = {"version": ver, "notes": notes, "changes": changes[:20], "runtime": rid,
                "app": {"url": f"downloads/{app_zip.name}", "sha256": app_sha, "signature": sign(key, updater.sign_message(ver, app_sha)),
                        "size": app_zip.stat().st_size},
                "runtimes": {}, "full": {}}

    for plat in plats:
        log(f"\n[{plat}]")
        rt = runtime_dir(plat, rid)
        rt_tar = site / "downloads" / f"runtime-{rid}-{plat}.tar.gz"
        _tar_dir(rt, rt_tar)
        rsha = sha256_of(rt_tar)
        manifest["runtimes"][plat] = {"url": f"downloads/{rt_tar.name}", "sha256": rsha,
                                      "signature": sign(key, updater.runtime_sign_message(rid, plat, rsha)), "size": rt_tar.stat().st_size}
        pkg = full_package(plat, ver, rid, rt, app, site / "downloads", base)
        manifest["full"][plat] = {"url": f"downloads/{pkg.name}", "sha256": sha256_of(pkg), "size": pkg.stat().st_size}
        log(f"  完整安装包 {pkg.name}：{pkg.stat().st_size / 1048576:.1f} MB；运行环境包 {rt_tar.stat().st_size / 1048576:.1f} MB")

    remote = copy_remote_media(site)
    (site / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    full = manifest["full"]
    get_sh = fill((HERE / "linux" / "get.sh.in").read_text(encoding="utf-8"), BASE=base, VERSION=ver,
                  LINUX_X64_FILE=Path(full.get("linux-x64", {}).get("url", "")).name, LINUX_X64_SHA=full.get("linux-x64", {}).get("sha256", ""),
                  LINUX_ARM64_FILE=Path(full.get("linux-arm64", {}).get("url", "")).name, LINUX_ARM64_SHA=full.get("linux-arm64", {}).get("sha256", ""))
    (site / "get.sh").write_text(get_sh, encoding="utf-8")

    def link(plat: str, label: str) -> str:
        it = full.get(plat)
        return f'<a class="btn" href="{base}/{it["url"]}">{label}（{it["size"] / 1048576:.0f} MB）</a>' if it else ""
    page = fill((HERE / "site" / "install.html.in").read_text(encoding="utf-8"), BASE=base, VERSION=ver,
                WINDOWS_LINK=link("windows-x64", "下载 Windows 版") or "（这个版本没有 Windows 包）",
                LINUX_LINKS=" ".join(x for x in (link("linux-x64", "x86_64"), link("linux-arm64", "arm64")) if x) or "（这个版本没有 Linux 包）",
                SHA_TABLE="\n".join(f'<tr><td>{label}</td><td><code>{Path(full[p]["url"]).name}</code><br><code style="word-break:break-all">{full[p]["sha256"]}</code></td></tr>'
                                    for p, label in (("windows-x64", "Windows"), ("linux-x64", "Linux x86_64"), ("linux-arm64", "Linux arm64")) if p in full))
    (site / "install").mkdir(exist_ok=True)
    (site / "install" / "index.html").write_text(page, encoding="utf-8")      # 网址是 /install（nginx 不会自动补 .html）
    # 主页就是使用手册（安装、恋综指南、指令大全、申请专属 App）；/guide 是旧链接，跳回主页
    (site / "index.html").write_text(fill((HERE / "site" / "guide.html.in").read_text(encoding="utf-8"), BASE=base, VERSION=ver), encoding="utf-8")
    shutil.copytree(HERE / "site" / "assets", site / "assets", dirs_exist_ok=True)   # 主页用的 3D 渲染图（packaging/site/studio 里重新渲染）
    (site / "guide").mkdir(exist_ok=True)
    (site / "guide" / "index.html").write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/"><link rel="canonical" href="/"><a href="/">长日系统</a>', encoding="utf-8")

    log(f"\n完成：{site}")
    log(f"  更新包 app-{ver}.zip：{app_zip.stat().st_size / 1024:.0f} KB；按需下载的背景音乐和海报皮肤 {remote / 1048576:.1f} MB（site/media/）")
    if a.test_key:
        log("  ⚠ 这是测试包（临时密钥），不要上传。")
    else:
        log("\n上传到主页（在 system.changri.work 的服务器上，先备份旧的 latest.json）：")
        deploy_host = os.environ.get("CHANGRI_DEPLOY_HOST", "<服务器>")   # ssh 主机名（~/.ssh/config 里的别名）
        log(f"  ssh {deploy_host} 'mkdir -p /var/www/system/downloads && cp /var/www/system/latest.json /var/www/system/latest.json.bak 2>/dev/null; true'")
        log(f"  rsync -av {site}/downloads/ {deploy_host}:/var/www/system/downloads/")
        log(f"  rsync -av {site}/media/ {deploy_host}:/var/www/system/media/")
        log(f"  rsync -av {site}/get.sh {site}/install {site}/index.html {site}/guide {site}/assets {deploy_host}:/var/www/system/")
        log(f"  rsync -av {site}/latest.json {deploy_host}:/var/www/system/latest.json      # 最后传：传上去以后各处才会看到新版本")


if __name__ == "__main__":
    main()
