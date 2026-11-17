"""在线更新：从发布者的主页拉新版本，校验签名后装到新的版本目录，切换 current，看门进程重启后生效；新版本起不来就自动切回。

latest.json（发布者用 packaging/build.py 生成并签名）：
  version / notes
  app       {url, sha256, signature, size}            只有程序代码（几百 KB）
  runtime   运行环境编号（Python + 依赖 + 字体）
  runtimes  {平台: {url, sha256, signature, size}}     这台机器上还没有这个运行环境时才下载
  full      {平台: {url, sha256, size}}                全新安装用的完整包（get.sh 和下载页用）

流程：下载 → 校验 sha256 和 Ed25519 签名（公钥内置，主页被改也推不进恶意代码）→ 解压到 versions/<版本>/
→ 备份所有数据库 → 写「待确认」标记 → 改写 current → 进程退出，看门进程用新版本拉起。
新版本 90 秒内连续崩溃 3 次：看门进程把数据库恢复成更新前的备份、current 改回旧版本（见 rollback_if_failing）。
data/ 永远不碰；下载的包装完就删，旧版本只留最近 3 个。开发时（直接从源码运行）不能在线更新。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from ..paths import DATA_DIR
from ..platform import layout

DEFAULT_URL = "https://system.changri.work/latest.json"
MAX_APP = 30 * 1024 * 1024
MAX_RUNTIME = 250 * 1024 * 1024
MAX_ATTEMPTS = 3
HEALTHY_AFTER = 90           # 新版本连续跑这么多秒就算成功
RESTART_CODE = 3             # 「请重启我」：看门进程重新拉起（current 变了就换新版本）

_lock = threading.Lock()


class UpdateError(Exception):
    """带着中文说明，可以直接显示给用户。"""


# ---------- 版本 ----------

def current_version() -> str:
    """装好的：current 文件里写的那个版本（刚装完更新、还没重启时就已经是新版本）；开发时：包里的 VERSION。"""
    return (layout.read_current() if layout.INSTALLED else "") or layout.package_version()


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")) if layout.VERSION_RE.match(v or "") else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and a > b


# ---------- 签名 ----------

def sign_message(version: str, sha256: str) -> bytes:
    return f"changri-bot-update\n{version}\n{sha256}".encode()


def runtime_sign_message(runtime: str, plat: str, sha256: str) -> bytes:
    return f"changri-bot-runtime\n{runtime}\n{plat}\n{sha256}".encode()


def _verify(message: bytes, signature_b64: str, pubkey_b64: str | None = None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if pubkey_b64 is None:
        from .update_pubkey import PUBLIC_KEY_B64 as pubkey_b64
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(base64.b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def verify_signature(version: str, sha256: str, signature_b64: str, pubkey_b64: str | None = None) -> bool:
    return _verify(sign_message(version, sha256), signature_b64, pubkey_b64)


# ---------- 取清单 ----------

def _allow_insecure() -> bool:
    return os.environ.get("CHANGRI_UPDATE_ALLOW_FILE") == "1"       # 只给测试用


def _open(url: str, timeout: float):
    scheme = urllib.parse.urlparse(url).scheme
    if scheme != "https" and not (_allow_insecure() and scheme in ("file", "http")):
        raise UpdateError("更新地址必须是 https。")
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "changri-bot-updater"}), timeout=timeout)


def manifest_url() -> str:
    return os.environ.get("CHANGRI_UPDATE_URL") or DEFAULT_URL


def _item(base_url: str, d, signed: bool = True) -> dict:
    if not isinstance(d, dict):
        raise UpdateError("更新信息不完整，已忽略。")
    sha, sig = str(d.get("sha256", "")).lower(), str(d.get("signature", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or (signed and not sig):
        raise UpdateError("更新信息不完整，已忽略。")
    url = urllib.parse.urljoin(base_url, str(d.get("url", "")))
    if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(base_url).netloc and not _allow_insecure():
        raise UpdateError("更新包不在同一个站点上，已拒绝。")
    return {"url": url, "sha256": sha, "signature": sig, "size": int(d.get("size") or 0)}


def fetch_manifest(url: str | None = None) -> dict:
    url = url or manifest_url()
    try:
        with _open(url, 10) as r:
            data = json.loads(r.read(500_000).decode("utf-8"))
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001
        raise UpdateError(f"连不上更新服务器（{type(e).__name__}）。请稍后再试。") from e
    if not isinstance(data, dict):
        raise UpdateError("更新信息的格式不对。")
    version, runtime = str(data.get("version", "")), str(data.get("runtime", ""))
    if not parse_version(version) or not layout.RUNTIME_RE.match(runtime):
        raise UpdateError("更新信息不完整，已忽略。")
    runtimes = {k: _item(url, v) for k, v in (data.get("runtimes") or {}).items()}
    return {"version": version, "notes": str(data.get("notes", ""))[:2000], "app": _item(url, data.get("app")),
            "runtime": runtime, "runtimes": runtimes, "size": int((data.get("app") or {}).get("size") or 0),
            "changes": parse_changes(data.get("changes"))}


def parse_changes(raw) -> list[dict]:
    """latest.json 里的「changes」：最近几个版本各自的「新功能 / 修 bug」，新的在前（0.21.7 起才有）。格式不对的条目丢掉。"""
    out = []
    for e in (raw if isinstance(raw, list) else [])[:30]:
        if not isinstance(e, dict) or not parse_version(str(e.get("version", ""))):
            continue

        def items(k):
            v = e.get(k)
            return [str(x).strip()[:300] for x in (v if isinstance(v, list) else [])[:30] if str(x).strip()]
        out.append({"version": str(e["version"]), "date": str(e.get("date", ""))[:10], "features": items("features"), "fixes": items("fixes")})
    return out


def pending_changes(changes: list[dict], current: str, latest: str) -> list[dict]:
    """从当前版本到最新版本之间（不含当前、含最新）每个版本的改动，新的在前。"""
    cur, top = parse_version(current), parse_version(latest)
    rows = [c for c in changes if cur < parse_version(c["version"]) <= top]
    return sorted(rows, key=lambda c: parse_version(c["version"]), reverse=True)


def update_badge() -> str:
    """设置平台「更新」入口要不要出小红点：有比当前新的版本、而且主人没点过「这个版本先不更新」，返回那个版本号；否则空。"""
    from .db import get_config
    latest = get_config("update_latest", "")
    if not latest or not is_newer(latest, current_version()):
        return ""
    return "" if get_config("update_skip", "") == latest else latest


def check(url: str | None = None) -> dict:
    m = fetch_manifest(url)
    cur = current_version()
    rt_needed = layout.INSTALLED and not (layout.RUNTIMES / m["runtime"]).is_dir()
    size = m["app"]["size"] + (m["runtimes"].get(layout.platform_tag(), {}).get("size", 0) if rt_needed else 0)
    pend = pending_changes(m["changes"], cur, m["version"])
    feats = [x for c in pend for x in c["features"]]           # 跳过了几个版本的，合成一份列出来，不按版本分
    fixes = [x for c in pend for x in c["fixes"]]
    return {**m, "current": cur, "newer": is_newer(m["version"], cur), "size": size, "runtime_needed": rt_needed, "pending": pend,
            "features": feats, "fixes": fixes}


# ---------- 下载 / 解压 ----------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


MIRROR_PREFIX = "https://ghfast.top/"      # 主页把下载跳转到这个 GitHub 镜像；镜像不稳时改走 GitHub 直连


def _direct_url(url: str) -> str | None:
    """主页的下载地址若跳转到镜像，返回去掉镜像前缀的直连地址；否则 None。"""
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        opener.open(urllib.request.Request(url, headers={"User-Agent": "changri-bot-updater"}), timeout=15)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc.startswith(MIRROR_PREFIX + "https://"):
            return loc[len(MIRROR_PREFIX):]
    except Exception:  # noqa: BLE001
        pass
    return None


def _fetch_to(url: str, dest: Path, limit: int, h) -> None:
    size = 0
    with _open(url, 60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            size += len(chunk)
            if size > limit:
                raise UpdateError("更新包太大，已拒绝。")
            h.update(chunk)
            f.write(chunk)


def _download(item: dict, dest: Path, limit: int, message: bytes | None) -> Path:
    """网络抖动时重试 2 次，仍失败再试一次 GitHub 直连（镜像不稳时）。校验 sha256 / 签名不变。"""
    h, last = hashlib.sha256(), None
    urls = [item["url"], item["url"], None]
    for i, url in enumerate(urls):
        if url is None:
            url = _direct_url(item["url"])
            if not url:
                break
        h = hashlib.sha256()
        try:
            _fetch_to(url, dest, limit, h)
            last = None
            break
        except UpdateError:
            dest.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            last = e
            if i < 2:
                time.sleep(2 * (i + 1))
    if last is not None:
        raise UpdateError(f"下载失败（{type(last).__name__}）。已重试并尝试直连，请换个网络再试。") from last
    if h.hexdigest() != item["sha256"]:
        dest.unlink(missing_ok=True)
        raise UpdateError("下载下来的文件和清单对不上（可能传输出错），已丢弃。")
    if message is not None and not _verify(message, item["signature"]):
        dest.unlink(missing_ok=True)
        raise UpdateError("签名校验没通过，这个更新包不是官方发布的，已拒绝。")
    return dest


def _safe_name(name: str) -> str:
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ".." in n.split("/"):
        raise UpdateError(f"更新包里有不安全的路径：{name}")
    return n


def _extract_app(zip_path: Path, version: str, runtime: str, versions_dir: Path) -> Path:
    target = versions_dir / version
    if target.is_dir():
        return target                       # 以前装过（比如回滚过），直接用
    tmp = versions_dir / f"{version}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        names = [_safe_name(n) for n in z.namelist()]
        if "changri/__init__.py" not in names or "changri/VERSION" not in names:
            raise UpdateError("更新包不完整，已拒绝。")
        if z.read("changri/VERSION").decode().strip() != version:
            raise UpdateError("更新包里的版本号和清单不一致，已拒绝。")
        if z.read("RUNTIME").decode().strip() != runtime:
            raise UpdateError("更新包要的运行环境和清单不一致，已拒绝。")
        z.extractall(tmp)
    os.replace(tmp, target)
    return target


def _extract_runtime(tar_path: Path, runtime: str, runtimes_dir: Path) -> Path:
    target = runtimes_dir / runtime
    if target.is_dir():
        return target
    tmp = runtimes_dir / f"{runtime}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(tar_path) as t:
        t.extractall(tmp, filter="data")          # 拒绝绝对路径、.. 和指向外面的链接
    os.replace(tmp, target)
    return target


# ---------- 更新 ----------

def update_now(data_dir: Path | None = None) -> dict:
    """检查 → 下载校验 → 装到新目录 → 备份 → 切换。成功后调用方负责 restart()。"""
    data_dir = data_dir or DATA_DIR
    if not layout.INSTALLED:
        raise UpdateError("现在是直接从源码运行的（开发环境），不能在线更新。")
    if not _lock.acquire(blocking=False):
        raise UpdateError("正在更新中，请稍等。")
    try:
        info = check()
        if not info["newer"]:
            raise UpdateError("已经是最新版本。")
        v, rt, plat = info["version"], info["runtime"], layout.platform_tag()
        if info["runtime_needed"] and plat not in info["runtimes"]:
            raise UpdateError(f"这个版本还没有适合这台机器（{plat}）的安装包，请等发布者补上。")
        with tempfile.TemporaryDirectory(dir=layout.APP_ROOT, prefix=".download_") as tmp:     # 下载的包用完就删
            app_zip = _download(info["app"], Path(tmp) / "app.zip", MAX_APP, sign_message(v, info["app"]["sha256"]))
            _extract_app(app_zip, v, rt, layout.VERSIONS)
            if info["runtime_needed"]:
                item = info["runtimes"][plat]
                rt_tar = _download(item, Path(tmp) / "runtime.tar.gz", MAX_RUNTIME, runtime_sign_message(rt, plat, item["sha256"]))
                _extract_runtime(rt_tar, rt, layout.RUNTIMES)
        cur = layout.read_current() or current_version()
        from ..platform import backup
        bdir = backup.make("update", f"{cur}_to_{v}", data_dir=data_dir)
        _pending_path(data_dir).write_text(json.dumps({"from": cur, "to": v, "backup": str(bdir), "attempts": 0, "ts": int(time.time())}),
                                   encoding="utf-8")
        layout.write_current(v)
        layout.prune(keep_also=(cur,))
        return {"from": cur, "to": v, "backup": str(bdir)}
    finally:
        _lock.release()


# ---------- 回滚（看门进程调用） ----------

def _pending_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "update_pending.json"


def rollback_if_failing(data_dir: Path = DATA_DIR) -> str | None:
    """新版本又一次很快就退出了：记一次；满 MAX_ATTEMPTS 次就恢复数据库、切回旧版本。返回给人看的一句话（没回滚就是 None）。"""
    p = _pending_path(data_dir)
    if not p.exists():
        return None
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        p.unlink(missing_ok=True)
        return None
    info["attempts"] = int(info.get("attempts", 0)) + 1
    if info["attempts"] < MAX_ATTEMPTS:
        p.write_text(json.dumps(info), encoding="utf-8")
        return None
    from ..platform import backup
    bdir = Path(info.get("backup", ""))
    if bdir.is_dir():
        backup.restore(bdir, data_dir)
    old = str(info.get("from", ""))
    if old and (layout.VERSIONS / old).is_dir():
        layout.write_current(old)
    msg = f"新版本 {info.get('to')} 连续 {MAX_ATTEMPTS} 次没能启动，已自动回到 {old}（数据库也恢复到了更新前）。"
    (data_dir / "update_failed.txt").write_text(msg, encoding="utf-8")
    p.unlink(missing_ok=True)
    return msg


def confirm_healthy(data_dir: Path = DATA_DIR) -> None:
    _pending_path(data_dir).unlink(missing_ok=True)


# ---------- 重启 ----------

def restart(delay: float = 1.5) -> None:
    """让进程退出，由看门进程重新拉起（current 变了就是新版本）。延迟一下，好让页面先返回。"""
    try:
        from . import extensions as _e
        _e.confirm_boot()      # 更新重启不是崩溃
    except Exception:  # noqa: BLE001
        pass
    threading.Timer(delay, _exit_for_restart).start()


def _exit_for_restart() -> None:
    """人为重启（点了「重启」、更新完成）：先把「正常退出」标记写好，再用退出码 3 让看门进程拉起；
    否则 os._exit 跳过收尾，下次启动会把这次重启记成崩溃。"""
    try:
        from . import errlog
        errlog.mark_clean_exit()
    except Exception:  # noqa: BLE001
        pass
    os._exit(RESTART_CODE)
