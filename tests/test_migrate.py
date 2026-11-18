import hashlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from changri.storage import migrate as M  # noqa: E402

# 0001 基线冻结了：改了它，老库不会再跑一遍。要加表/加列请新建 0002_xxx.sql。
BASELINE_SHA256 = "72e1d0444c5a6b7daaf7700615f5eecf859b40a2f2d5a881cf4072aa6f078b68"


def tables(path):
    c = sqlite3.connect(path)
    try:
        return {r[0]: c.execute(f"SELECT COUNT(*) FROM \"{r[0]}\"").fetchone()[0]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        c.close()


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_baseline_is_frozen(self):
        sha = hashlib.sha256((M.MIGRATIONS_DIR / "0001_baseline.sql").read_bytes()).hexdigest()
        self.assertEqual(sha, BASELINE_SHA256, "0001_baseline.sql 不能改；新表、新列请加 0002_xxx.sql")

    def test_numbering_is_contiguous(self):
        self.assertEqual([n for n, _ in M.available()], list(range(1, M.latest() + 1)))
        bad = self.tmp / "m"; bad.mkdir()
        (bad / "0001_a.sql").write_text("")
        (bad / "0003_b.sql").write_text("")
        with self.assertRaises(M.MigrationError):
            M.available(bad)

    def test_fresh_db_gets_latest(self):
        p = self.tmp / "a.db"
        self.assertEqual(M.migrate(p), M.latest())
        self.assertIn("roles", tables(p))
        self.assertEqual(M.migrate(p), M.latest())         # 再跑一遍不出错

    def test_old_db_runs_legacy_then_baseline_and_keeps_rows(self):
        p = self.tmp / "old.db"
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE roles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, member_openid TEXT NOT NULL, created_ts INTEGER NOT NULL)")
        c.execute("INSERT INTO roles(name, member_openid, created_ts) VALUES('甲', 'o1', 1)")
        c.commit(); c.close()
        called = []
        M.migrate(p, legacy=lambda path: called.append(path))
        self.assertEqual(called, [p])
        self.assertEqual(tables(p)["roles"], 1)
        self.assertIn("messages", tables(p))
        M.migrate(p, legacy=lambda path: called.append(path))
        self.assertEqual(len(called), 1)                    # 版本号记上了，旧逻辑只跑一次

    def test_failed_step_rolls_back_and_retries(self):
        folder = self.tmp / "m"; folder.mkdir()
        (folder / "0001_a.sql").write_text("CREATE TABLE t (v INTEGER);")
        (folder / "0002_b.py").write_text("def up(conn):\n    conn.execute('INSERT INTO t VALUES (1)')\n    raise RuntimeError('boom')\n")
        p = self.tmp / "x.db"
        with self.assertRaises(RuntimeError):
            M.migrate(p, folder=folder)
        c = sqlite3.connect(p)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM t").fetchone()[0], 0)
        c.close()
        (folder / "0002_b.py").write_text("def up(conn):\n    conn.execute('INSERT INTO t VALUES (1)')\n")
        self.assertEqual(M.migrate(p, folder=folder), 2)

    def test_refuses_db_newer_than_program(self):
        p = self.tmp / "new.db"
        c = sqlite3.connect(p); c.execute(f"PRAGMA user_version={M.latest() + 5}"); c.close()
        with self.assertRaises(M.MigrationError):
            M.migrate(p)

    def test_real_database_copy_if_present(self):
        """本机有测试服务器的库副本（data/，不进 git）就拿来升一遍，不能丢数据：行数不能变。
        设置表（config）例外，只要不变少：迁移可能补上新的默认设置（比如 0023 打开小手机）。"""
        root = Path(__file__).resolve().parent.parent / "data"
        dbs = [p for p in [root / "changri.db", *root.glob("bots/*/changri.db")] if p.exists()]
        if not dbs:
            self.skipTest("本机没有真实库副本")
        from changri.core import db
        for src in dbs:
            p = self.tmp / f"{src.parent.name}.db"
            s = sqlite3.connect(src); d = sqlite3.connect(p); s.backup(d); s.close(); d.close()
            before = tables(p)
            M.migrate(p, legacy=db._migrate)
            after = tables(p)
            for t, n in before.items():
                if t == "config":
                    self.assertGreaterEqual(after.get(t, 0), n, f"{src}：设置表少了")
                else:
                    self.assertEqual(after.get(t), n, f"{src}：表 {t} 行数变了")


if __name__ == "__main__":
    unittest.main()
