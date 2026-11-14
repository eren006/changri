import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "w.db"))
from core import windows  # noqa: E402
from core.db import init_db, set_config  # noqa: E402


class Windows(unittest.TestCase):
    def test_range(self):
        init_db()
        set_config("window_sms", "10-23")
        self.assertTrue(windows.is_open("sms", 10 * 60)[0])
        self.assertFalse(windows.is_open("sms", 23 * 60)[0])
        self.assertFalse(windows.is_open("sms", 9 * 60 + 59)[0])
        self.assertIn("10:00–23:00", windows.is_open("sms", 3 * 60)[1])
        set_config("window_sms", "20:00-02:00")                 # 跨午夜
        self.assertTrue(windows.is_open("sms", 23 * 60)[0])
        self.assertTrue(windows.is_open("sms", 60)[0])
        self.assertFalse(windows.is_open("sms", 12 * 60)[0])
        for bad in ("", "abc", "10-10", "25-30"):
            set_config("window_sms", bad)
            self.assertTrue(windows.is_open("sms", 12 * 60)[0], bad)    # 空/写错 = 全天


if __name__ == "__main__":
    unittest.main()
