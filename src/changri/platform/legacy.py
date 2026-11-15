"""从老版本（0.19 及以前：一个目录里 bot.py + data/ + .env + extensions/）搬到新安装。

  changri migrate-legacy <老安装目录>
把老目录的 data/ 整个拷进新的数据目录、.env 里的端口等设置导入 config.toml、用户扩展搬进 data/extensions/。
老目录一个文件都不改不删，确认新版本没问题后再自己删。新的数据目录里已经有数据库时拒绝（不会覆盖）。
老程序必须先停掉（Linux：sudo systemctl stop changri-bot；Windows：关掉窗口 / 取消开机自启），否则数据库可能拷到一半。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from ..paths import DATA_DIR


def migrate(old: Path, data_dir: Path = DATA_DIR) -> list[str]:
    old = old.expanduser().resolve()
    if not (old / "data").is_dir():
        raise RuntimeError(f"{old} 里没有 data 文件夹，不像是老版本的安装目录。")
    if (data_dir / "changri.db").exists():
        raise RuntimeError(f"新的数据目录 {data_dir} 里已经有数据了，为了不覆盖，没有搬。要重来请先把它挪走。")
    done = []
    data_dir.mkdir(parents=True, exist_ok=True)
    for p in (old / "data").iterdir():
        if p.name == "backups" or p.name.endswith("-shm"):          # -wal 要带上：里面可能有还没写回主库的数据；-shm 打开时会重建
            continue
        target = data_dir / p.name
        if p.is_dir():
            shutil.copytree(p, target, dirs_exist_ok=True)
        else:
            shutil.copy2(p, target)
    done.append(f"数据：{old / 'data'} → {data_dir}")
    if (old / "extensions").is_dir() and not (data_dir / "extensions").exists():
        shutil.copytree(old / "extensions", data_dir / "extensions")
        done.append("用户扩展 → data/extensions")
    if (old / ".env").exists() and not config.path().exists():
        with _legacy_env(old / ".env"):
            config.ensure()
        done.append(f"设置：.env → {config.path()}")
    return done


class _legacy_env:
    """临时把 config 读的老 .env 指到老目录里的那份。"""

    def __init__(self, path: Path):
        self.path, self.saved = path, None

    def __enter__(self):
        self.saved = config.LEGACY_ENV
        config.LEGACY_ENV = self.path

    def __exit__(self, *exc):
        config.LEGACY_ENV = self.saved
