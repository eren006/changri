"""按需下载的大文件（目前是匿名舞会的背景音乐）：安装包里不带，第一次用到时从主页下载一次，校验后存进 data/media/。

哪些文件、校验值多少，写在 changri/remote_media.json（发布者改了文件就重新生成，见 packaging/build.py --refresh-remote-media）。
开发时文件就在包里，直接用；安装后：包里 → 运行环境的 changri-media/ → data/media/ → 下载。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from ..paths import DATA_DIR, PKG_DIR

MANIFEST = PKG_DIR / "remote_media.json"
DEFAULT_BASE = "https://system.changri.work/media/"
MAX_SIZE = 30 * 1024 * 1024

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class MediaError(Exception):
    pass


def manifest() -> dict[str, dict]:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cache_dir() -> Path:
    return DATA_DIR / "media"


def base_url() -> str:
    return os.environ.get("CHANGRI_MEDIA_URL") or DEFAULT_BASE


def is_remote(rel: str) -> bool:
    return rel in manifest()


def _lock_for(rel: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(rel, threading.Lock())


def fetch(rel: str) -> Path:
    """确保 data/media/<rel> 在本地（没有就下载、校验）。返回本地路径；下载或校验失败抛 MediaError。"""
    item = manifest().get(rel)
    if not item:
        raise MediaError(f"{rel} 不是按需下载的文件")
    target = cache_dir() / rel
    if target.is_file():
        return target
    with _lock_for(rel):                       # 好几个人同时点开：只下载一次
        if target.is_file():
            return target
        url = urllib.parse.urljoin(base_url(), urllib.parse.quote(rel))
        scheme = urllib.parse.urlparse(url).scheme
        if scheme != "https" and not os.environ.get("CHANGRI_MEDIA_URL"):
            raise MediaError("下载地址必须是 https")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        h, size = hashlib.sha256(), 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "changri-bot-media"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 16):
                    size += len(chunk)
                    if size > MAX_SIZE:
                        raise MediaError("文件太大，已拒绝")
                    h.update(chunk)
                    f.write(chunk)
        except MediaError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            raise MediaError(f"下载失败（{type(e).__name__}）") from e
        if h.hexdigest() != item["sha256"]:
            tmp.unlink(missing_ok=True)
            raise MediaError("下载的文件校验不对，已丢弃")
        os.replace(tmp, target)
        return target
