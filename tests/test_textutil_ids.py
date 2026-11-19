"""编号写法（textutil.parse_id）：全部「填编号」的指令共用一套写法。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from changri.core import drinking, forum, gifts, wishes  # noqa: E402
from changri.core.textutil import parse_id  # noqa: E402


class ParseId(unittest.TestCase):
    def test_plain_forms(self):
        for s in ("3", "03", "#3", "＃3", "[3]", "【3】", "(3)", "（3）", "「3」", "３", " 3 ", "# 3"):
            self.assertEqual(parse_id(s), 3, s)

    def test_rejects(self):
        for s in ("", "a", "三", "1 2", "3a", "²", "-3", "3.5", "R3", "[3", "D3"):
            self.assertIsNone(parse_id(s), s)

    def test_room_prefix(self):
        for s in ("R12", "r12", "[R12]", "【R12】", "#R12", "Ｒ１２", "12", "#12"):
            self.assertEqual(parse_id(s, "R"), 12, s)
        self.assertIsNone(parse_id("D12", "R"))

    def test_callers_share_forms(self):
        self.assertEqual(wishes.parse_id("[4]"), 4)
        self.assertEqual(forum.parse_post_ref("【001】"), 1)
        self.assertEqual(gifts.parse_code("#003"), 3)
        self.assertIsNone(forum.parse_post_ref("1234567"))


if __name__ == "__main__":
    unittest.main()
