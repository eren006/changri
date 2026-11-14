"""在线更新：从发布者的主页拉新版本，校验签名后覆盖代码，失败自动回滚。

流程：读 latest.json → 比版本 → 下载 zip → 校验 sha256 和 Ed25519 签名（公钥内置，主页被改也推不进恶意代码）
→ 备份当前代码和数据库 → 覆盖代码 → 需要的话装新依赖 → 写「待确认」标记 → 重启。
新版本起来后 90 秒内没出问题才算成功；连续崩溃 3 次，启动时自动用备份回滚。
只覆盖代码目录和脚本；data/、.env、venv 永远不碰；.bat 不自动覆盖（正在运行的批处理被改会出错）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://system.changri.work/latest.json"
MAX_ZIP = 120 * 1024 * 1024
ALLOWED_DIRS = ("admin", "core", "plugins", "assets", "docs")
ALLOWED_FILES = {"bot.py", "changri_api.py", "requirements.txt", ".env.example", "VERSION", "lib.sh", "start.sh", "install.sh"}
PRUNE_DIRS = ("admin", "core", "plugins")          # 这些目录里新版本没有的 .py/.html 要删掉（比如被移除的插件）
PRUNE_EXT = (".py", ".html")
KEEP_BACKUPS = 3
MAX_ATTEMPTS = 3

_lock = threading.Lock()


class UpdateError(Exception):
    """带着中文说明，可以直接显示给用户。"""


# ---------- 版本 ----------

def current_version(root: Path = ROOT) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")) if re.fullmatch(r"\d+(\.\d+){1,3}", v or "") else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and a > b


# ---------- 签名 ----------

def sign_message(version: str, sha256: str) -> bytes:
    return f"changri-bot-update\n{version}\n{sha256}".encode()


def verify_signature(version: str, sha256: str, signature_b64: str, pubkey_b64: str | None = None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if pubkey_b64 is None:
        from .update_pubkey import PUBLIC_KEY_B64 as pubkey_b64
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(
            base64.b64decode(signature_b64), sign_message(version, sha256))
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------- 取清单 / 下载 ----------

def _allow_insecure() -> bool:
    return os.environ.get("CHANGRI_UPDATE_ALLOW_FILE") == "1"       # 只给测试用


def _open(url: str, timeout: float):
    scheme = urllib.parse.urlparse(url).scheme
    if scheme != "https" and not (_allow_insecure() and scheme in ("file", "http")):
        raise UpdateError("更新地址必须是 https。")
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "changri-bot-updater"}), timeout=timeout)


def manifest_url() -> str:
    return os.environ.get("CHANGRI_UPDATE_URL") or DEFAULT_URL


def fetch_manifest(url: str | None = None) -> dict:
    url = url or manifest_url()
    try:
        with _open(url, 10) as r:
            data = json.loads(r.read(200_000).decode("utf-8"))
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001
        raise UpdateError(f"连不上更新服务器（{type(e).__name__}）。请稍后再试。") from e
    if not isinstance(data, dict):
        raise UpdateError("更新信息的格式不对。")
    version, sha, sig = str(data.get("version", "")), str(data.get("sha256", "")).lower(), str(data.get("signature", ""))
    if not parse_version(version) or not re.fullmatch(r"[0-9a-f]{64}", sha) or not sig:
        raise UpdateError("更新信息不完整，已忽略。")
    zip_url = urllib.parse.urljoin(url, str(data.get("url", "")))
    if urllib.parse.urlparse(zip_url).netloc != urllib.parse.urlparse(url).netloc and not _allow_insecure():
        raise UpdateError("更新包不在同一个站点上，已拒绝。")
    return {"version": version, "sha256": sha, "signature": sig, "url": zip_url,
            "notes": str(data.get("notes", ""))[:2000], "size": int(data.get("size") or 0)}


def check(url: str | None = None, root: Path = ROOT) -> dict:
    m = fetch_manifest(url)
    cur = current_version(root)
    return {**m, "current": cur, "newer": is_newer(m["version"], cur)}


def download(manifest: dict, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "update.zip"
    h, size = hashlib.sha256(), 0
    try:
        with _open(manifest["url"], 60) as r, open(path, "wb") as f:
            while chunk := r.read(1 << 16):
                size += len(chunk)
                if size > MAX_ZIP:
                    raise UpdateError("更新包太大，已拒绝。")
                h.update(chunk); f.write(chunk)
    except UpdateError:
        path.unlink(missing_ok=True); raise
    except Exception as e:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise UpdateError(f"下载失败（{type(e).__name__}）。") from e
    if h.hexdigest() != manifest["sha256"]:
        path.unlink(missing_ok=True)
        raise UpdateError("下载下来的文件和清单对不上（可能传输出错），已丢弃。")
    if not verify_signature(manifest["version"], manifest["sha256"], manifest["signature"]):
        path.unlink(missing_ok=True)
        raise UpdateError("签名校验没通过，这个更新包不是官方发布的，已拒绝。")
    return path


# ---------- 应用 ----------

def _safe_target(name: str) -> str | None:
    """zip 里的路径合法并且属于允许覆盖的范围才返回规范路径，否则 None（忽略）。非法路径直接抛错。"""
    if not name or name.endswith("/"):
        return None
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ".." in n.split("/"):
        raise UpdateError(f"更新包里有不安全的路径：{name}")
    n = re.sub(r"^\./", "", n)
    parts = n.split("/")
    if len(parts) == 1:
        return n if n in ALLOWED_FILES else None
    if parts[0] in ALLOWED_DIRS and "__pycache__" not in parts:
        return n
    return None


def _code_files(root: Path) -> list[str]:
    out = []
    for f in ALLOWED_FILES:
        if (root / f).is_file():
            out.append(f)
    for d in ALLOWED_DIRS:
        for p in (root / d).rglob("*") if (root / d).is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts:
                out.append(p.relative_to(root).as_posix())
    return sorted(out)


def _backup(root: Path, data_dir: Path, version: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bdir = data_dir / "backups" / f"update_{version}_{stamp}"
    bdir.mkdir(parents=True, exist_ok=True)
    skip_fonts = True        # 字体不会变，备份里不带，省空间
    with zipfile.ZipFile(bdir / "code.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for rel in _code_files(root):
            if skip_fonts and rel.startswith("assets/fonts/"):
                continue
            z.write(root / rel, rel)
    db = data_dir / "changri.db"
    if db.exists():
        src, dst = sqlite3.connect(db), sqlite3.connect(bdir / "changri.db")
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
    old = sorted((data_dir / "backups").glob("update_*"))
    for d in old[:-KEEP_BACKUPS]:
        shutil.rmtree(d, ignore_errors=True)
    return bdir


def _restore_code(bdir: Path, root: Path) -> None:
    with zipfile.ZipFile(bdir / "code.zip") as z:
        names = set(z.namelist())
        for rel in names:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".restore")
            tmp.write_bytes(z.read(rel)); os.replace(tmp, target)
    _prune(root, names)


def _prune(root: Path, keep: set[str]) -> None:
    for d in PRUNE_DIRS:
        base = root / d
        for p in base.rglob("*") if base.is_dir() else []:
            if p.is_file() and p.suffix in PRUNE_EXT and "__pycache__" not in p.parts and p.relative_to(root).as_posix() not in keep:
                p.unlink(missing_ok=True)


def _pip_install(root: Path) -> None:
    import subprocess
    base = [sys.executable, "-m", "pip", "install", "-q", "--default-timeout", "30", "-r", str(root / "requirements.txt")]
    for extra in ([], ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]):
        r = subprocess.run(base + extra, capture_output=True, text=True, timeout=600, cwd=root)
        if r.returncode == 0:
            return
    raise UpdateError("新版本需要的依赖没装上（检查服务器能不能上网），已回滚到原来的版本。")


def apply_zip(zip_path: Path, expected_version: str, root: Path = ROOT, data_dir: Path | None = None,
              install_deps: bool = True) -> dict:
    data_dir = data_dir or root / "data"
    if not _lock.acquire(blocking=False):
        raise UpdateError("正在更新中，请稍等。")
    try:
        with zipfile.ZipFile(zip_path) as z:
            files: dict[str, str] = {}
            for info in z.infolist():
                rel = _safe_target(info.filename)
                if rel:
                    files[rel] = info.filename
            if "VERSION" not in files or z.read(files["VERSION"]).decode().strip() != expected_version:
                raise UpdateError("更新包里的版本号和清单不一致，已拒绝。")
            if "bot.py" not in files:
                raise UpdateError("更新包不完整（没有 bot.py），已拒绝。")
            old_version = current_version(root)
            old_req = (root / "requirements.txt").read_bytes() if (root / "requirements.txt").exists() else b""
            bdir = _backup(root, data_dir, old_version)
            try:
                for rel, orig in files.items():
                    target = root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_name(target.name + ".new")
                    tmp.write_bytes(z.read(orig))
                    os.replace(tmp, target)
                _prune(root, set(files))
                if install_deps and (root / "requirements.txt").read_bytes() != old_req:
                    _pip_install(root)
            except Exception:
                _restore_code(bdir, root)
                raise
        (data_dir).mkdir(parents=True, exist_ok=True)
        (data_dir / "update_pending.json").write_text(json.dumps(
            {"from": old_version, "to": expected_version, "backup": str(bdir), "attempts": 0, "ts": int(time.time())}), encoding="utf-8")
        return {"from": old_version, "to": expected_version, "backup": str(bdir)}
    except zipfile.BadZipFile as e:
        raise UpdateError("更新包损坏，已拒绝。") from e
    finally:
        _lock.release()


def update_now(root: Path = ROOT, data_dir: Path | None = None) -> dict:
    """检查 → 下载校验 → 应用。成功后调用方负责 restart()。"""
    info = check(root=root)
    if not info["newer"]:
        raise UpdateError("已经是最新版本。")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = download(info, Path(tmp))
        res = apply_zip(path, info["version"], root, data_dir)
    return res


# ---------- 重启与回滚保护 ----------

def restart(delay: float = 1.5) -> None:
    """让进程退出，由 systemd / start.sh / start.bat 的循环重新拉起。延迟一下，好让页面先返回。"""
    try:
        from . import extensions as _e
        _e.confirm_boot()      # 更新重启不是崩溃
    except Exception:  # noqa: BLE001
        pass
    threading.Timer(delay, lambda: os._exit(3)).start()


def startup_guard(root: Path = ROOT, data_dir: Path | None = None) -> str | None:
    """bot.py 最先调用：刚更新完、新版本又连续起不来，就用备份回滚（代码和数据库都回到更新前）。"""
    data_dir = data_dir or root / "data"
    pending = data_dir / "update_pending.json"
    if not pending.exists():
        return None
    try:
        info = json.loads(pending.read_text(encoding="utf-8"))
    except ValueError:
        pending.unlink(missing_ok=True); return None
    info["attempts"] = int(info.get("attempts", 0)) + 1
    if info["attempts"] <= MAX_ATTEMPTS:
        pending.write_text(json.dumps(info), encoding="utf-8")
        return None
    bdir = Path(info.get("backup", ""))
    msg = None
    if (bdir / "code.zip").exists():
        _restore_code(bdir, root)
        if (bdir / "changri.db").exists():
            shutil.copyfile(bdir / "changri.db", data_dir / "changri.db")
            for ext in ("-wal", "-shm"):
                (data_dir / ("changri.db" + ext)).unlink(missing_ok=True)
        msg = f"新版本 {info.get('to')} 连续 {MAX_ATTEMPTS} 次没能启动，已自动回滚到 {info.get('from')}。"
        (data_dir / "update_failed.txt").write_text(msg, encoding="utf-8")
    pending.unlink(missing_ok=True)
    return msg


def confirm_healthy(data_dir: Path | None = None) -> None:
    (data_dir or ROOT / "data").joinpath("update_pending.json").unlink(missing_ok=True)
