"""心动信按天上限：D0:1 D2:5 这样的特殊天，没写到的天用默认。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "l.db"))

from core import lovemail  # noqa: E402
from core.db import init_db, set_config  # noqa: E402


class DayLimits(unittest.TestCase):
    def test_per_day(self):
        init_db()
        set_config("lovemail_default_limit", "3")
        set_config("lovemail_day_limits", "D0:1 D2:5 D3:0")
        for day, want in (("0", 1), ("1", 3), ("2", 5), ("3", 0), ("", 3)):
            set_config("game_day", day)
            self.assertEqual(lovemail.today_limit(), want, day)
        set_config("lovemail_day_limits", "")
        set_config("game_day", "0")
        self.assertEqual(lovemail.today_limit(), 3)


if __name__ == "__main__":
    unittest.main()
