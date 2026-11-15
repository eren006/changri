"""数据库备份与恢复：全局库 + 每个机器人的工作区库。用 SQLite 的在线备份，程序开着也能拷出一致的副本。"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from ..paths import DATA_DIR

KEEP = 5


def databases(data_dir: Path = DATA_DIR) -> list[Path]:
    out = [data_dir / "changri.db"] if (data_dir / "changri.db").exists() else []
    out += sorted((data_dir / "bots").glob("*/changri.db"))
    return out


def snapshot(dest: Path, data_dir: Path = DATA_DIR) -> Path:
    """把所有数据库拷到 dest/（保持相对路径），再带上 config.toml 和密钥。返回 dest。"""
    dest.mkdir(parents=True, exist_ok=True)
    for db in databases(data_dir):
        target = dest / db.relative_to(data_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        src, out = sqlite3.connect(db, timeout=30), sqlite3.connect(target)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    for name in ("config.toml", ".secret_key"):
        if (data_dir / name).exists():
            shutil.copy2(data_dir / name, dest / name)
    return dest


def restore(src: Path, data_dir: Path = DATA_DIR) -> None:
    """用 snapshot 拷出来的目录覆盖数据库（程序必须是停着的）。WAL/SHM 一起删掉，免得旧日志被重放。"""
    for db in sorted(src.rglob("changri.db")):
        target = data_dir / db.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        for ext in ("-wal", "-shm"):
            target.with_name(target.name + ext).unlink(missing_ok=True)
        tmp = target.with_name(target.name + ".restore")
        shutil.copyfile(db, tmp)
        tmp.replace(target)


def make(kind: str, label: str = "", data_dir: Path = DATA_DIR, keep: int = KEEP) -> Path:
    """data/backups/<kind>_<label>_<时间>/，同一种只留最近 keep 份。"""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = "_".join(x for x in (kind, label, stamp) if x)
    dest = snapshot(data_dir / "backups" / name, data_dir)
    old = sorted((data_dir / "backups").glob(f"{kind}_*"))
    for d in old[:-keep]:
        shutil.rmtree(d, ignore_errors=True)
    return dest
