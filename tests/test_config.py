import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from changri import config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.env = self.tmp / ".env"
        self.patches = [mock.patch.object(config, "DATA_DIR", self.data), mock.patch.object(config, "LEGACY_ENV", self.env),
                        mock.patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        for k in ("HOST", "PORT", "LOG_LEVEL", "CHANGRI_TZ"):
            os.environ.pop(k, None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_defaults_written_on_first_start(self):
        config.ensure()
        self.assertTrue(config.path().exists())
        self.assertEqual(config.load(), {"host": "0.0.0.0", "port": 5091, "log_level": "INFO", "timezone": "Asia/Shanghai"})

    def test_imports_legacy_env_once(self):
        self.env.write_text("HOST=127.0.0.1\nPORT=6001\nFLASK_SECRET_KEY=abc\nLOG_LEVEL=DEBUG\n")
        config.ensure()
        self.assertEqual(config.load(), {"host": "127.0.0.1", "port": 6001, "log_level": "DEBUG", "timezone": "Asia/Shanghai"})
        self.assertEqual((self.data / ".secret_key").read_text(), "abc")
        self.env.write_text("PORT=7000\n")          # 之后再改 .env 不再生效
        config.ensure()
        self.assertEqual(config.get("port"), 6001)

    def test_env_overrides_file(self):
        config.save({"host": "0.0.0.0", "port": 5091, "log_level": "INFO"})
        os.environ["PORT"] = "5191"
        self.assertEqual(config.admin_port(), 5191)

    def test_bad_value_in_legacy_env_falls_back(self):
        self.env.write_text("PORT=abc\n")
        config.ensure()
        self.assertEqual(config.get("port"), 5091)

    def test_moves_legacy_extensions_into_data(self):
        root = self.tmp / "app"
        (root / "extensions" / "demo").mkdir(parents=True)
        (root / "extensions" / "demo" / "__init__.py").write_text("X=1")
        with mock.patch("changri.paths.APP_ROOT", root):
            config.ensure()
        self.assertTrue((self.data / "extensions" / "demo" / "__init__.py").exists())
        self.assertFalse((root / "extensions").exists())

    def test_migrate_legacy_install(self):
        from changri.platform import legacy
        old = self.tmp / "old"
        (old / "data" / "bots" / "123").mkdir(parents=True)
        (old / "data" / "changri.db").write_text("db")
        (old / "data" / "bots" / "123" / "changri.db").write_text("bot")
        (old / "data" / "changri.db-wal").write_text("wal")
        (old / "extensions" / "x").mkdir(parents=True)
        (old / ".env").write_text("PORT=6001\n")
        done = legacy.migrate(old, self.data)
        self.assertEqual(len(done), 3)
        self.assertEqual((self.data / "bots" / "123" / "changri.db").read_text(), "bot")
        self.assertTrue((self.data / "changri.db-wal").exists())           # WAL 一起带上，里面可能有没写回主库的数据
        self.assertTrue((self.data / "extensions" / "x").is_dir())
        self.assertEqual(config.get("port"), 6001)
        self.assertTrue((old / "data" / "changri.db").exists())             # 老目录不动
        with self.assertRaises(RuntimeError):
            legacy.migrate(old, self.data)                                   # 新目录已有数据：不覆盖


if __name__ == "__main__":
    unittest.main()


class TimezoneTests(unittest.TestCase):
    """换天、心动信派送按 time.localtime()：服务器是 UTC 时以前 23:59 换天会变成北京时间早上 7:59。"""

    def setUp(self):
        self.old = os.environ.get("TZ")

    def tearDown(self):
        import time
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        if hasattr(time, "tzset"):
            time.tzset()

    @unittest.skipUnless(hasattr(__import__("time"), "tzset"), "Windows 不能单独设时区")
    def test_utc_server_runs_on_beijing_time(self):
        import time
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.assertEqual(config.apply_timezone("Asia/Shanghai"), "Asia/Shanghai")
        self.assertEqual(time.strftime("%H:%M", time.localtime(0)), "08:00")     # 1970-01-01 00:00 UTC = 北京 08:00

    @unittest.skipUnless(hasattr(__import__("time"), "tzset"), "Windows 不能单独设时区")
    def test_no_zoneinfo_falls_back_to_fixed_offset(self):
        import time
        with mock.patch.object(Path, "exists", lambda self: False):
            config.apply_timezone("Asia/Shanghai")
        self.assertEqual(os.environ["TZ"], "CST-8")
        self.assertEqual(time.strftime("%H:%M", time.localtime(0)), "08:00")
        with mock.patch.object(Path, "exists", lambda self: False):
            self.assertIn("没有时区数据", config.apply_timezone("Europe/Nowhere"))

    def test_empty_follows_system(self):
        os.environ.pop("TZ", None)
        self.assertEqual(config.apply_timezone(""), "跟着系统时区")
        self.assertNotIn("TZ", os.environ)
