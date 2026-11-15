"""数据库迁移：storage/migrations/ 里按编号排好的文件，版本号记在 SQLite 的 PRAGMA user_version。

- NNNN_名字.sql：一段 SQL，在一个事务里执行。
- NNNN_名字.py ：里面写 def up(conn)；conn 已经在事务里（外键检查关着，重建表不会级联删数据）。
每个文件成功后 user_version 记成它的编号；失败整段回滚，下次启动再试。编号必须从 1 连续往上。

老库（架构 v2 之前建的，user_version 还是 0）：先跑一遍旧的补列逻辑（legacy），再跑 0001 基线补齐缺的表。
"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path
from typing import Callable

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_NAME = re.compile(r"^(\d{4})_[\w\-]+\.(sql|py)$")


class MigrationError(Exception):
    pass


def available(folder: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    found = []
    for p in folder.iterdir():
        m = _NAME.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    found.sort()
    nums = [n for n, _ in found]
    if nums != list(range(1, len(nums) + 1)):
        raise MigrationError(f"迁移文件编号必须从 0001 连续往上，现在是：{nums}")
    return found


def latest(folder: Path = MIGRATIONS_DIR) -> int:
    return len(available(folder))


def baseline_sql() -> str:
    return (MIGRATIONS_DIR / "0001_baseline.sql").read_text(encoding="utf-8")


def version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _apply(conn: sqlite3.Connection, num: int, path: Path) -> None:
    try:
        if path.suffix == ".sql":
            # executescript 会先提交当前事务，所以 SQL 自己包一层事务，版本号写在同一个事务里
            conn.executescript(f"BEGIN;\n{path.read_text(encoding='utf-8')}\n;PRAGMA user_version={num};\nCOMMIT;")
            return
        conn.execute("BEGIN")
        spec = importlib.util.spec_from_file_location(f"changri_migration_{num}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.up(conn)
        conn.execute(f"PRAGMA user_version={num}")
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def migrate(path: Path, legacy: Callable[[Path], None] | None = None, folder: Path = MIGRATIONS_DIR) -> int:
    """把 path 这个库升到最新。返回升级后的版本号。"""
    steps = available(folder)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")
        current = version(conn)
        if current > len(steps):
            raise MigrationError(f"数据库版本 {current} 比程序认识的 {len(steps)} 新：可能装回了旧版本程序，请换回新版本或用备份恢复。")
        if current == 0 and legacy is not None:
            conn.close()
            legacy(path)                    # 旧逻辑自己开连接、自己管事务
            conn = sqlite3.connect(path, timeout=10, isolation_level=None)
            conn.execute("PRAGMA foreign_keys=OFF")
        for num, p in steps:
            if num > current:
                _apply(conn, num, p)
        return version(conn)
    finally:
        conn.close()
