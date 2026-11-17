"""喝酒游戏（晚餐小游戏）。"""
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "dk.db"))

from changri.core import drinking as D  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class Drinking(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")
        D.rng = random.Random(5)

    def test_full_game(self):
        self.assertEqual(len(D.DRINKS), 54)
        self.assertFalse(D.start("G")[0])                                # 没人
        self.assertTrue(D.join("G", "甲")[0])
        self.assertFalse(D.join("G", "甲")[0])
        D.join("G", "乙")
        self.assertFalse(D.serve("G")[0])                                # 没开始
        self.assertTrue(D.start("G")[0])
        self.assertFalse(D.join("G", "丙")[0])                           # 开始后不能加入
        self.assertFalse(D.drink("G", "甲", "1")[0])                     # 没上酒
        ok, text = D.serve("G")
        self.assertIn("24 杯", text)
        self.assertNotIn("1 号", text)                                    # 不说哪杯在几号
        self.assertFalse(D.serve("G")[0])                                 # 没喝完不能再上
        self.assertFalse(D.drink("G", "甲", "25")[0])
        self.assertTrue(D.drink("G", "甲", "3")[0])
        self.assertIn("已经被喝掉", D.drink("G", "乙", "3")[1])
        self.assertNotIn("3", D.left("G").split("：")[1].split("、"))
        self.assertIn("甲：", D.status("G"))
        self.assertTrue(D.end("G")[0])
        self.assertFalse(D.end("G")[0])

    def test_drunk_list_and_threshold(self):
        D.set_threshold(10)
        self.assertFalse(D.set_threshold(5)[0])
        D.join("G", "甲"); D.start("G"); D.serve("G")
        s = D._get("G"); s["cups"] = [0] * D.CUPS; D._put("G", s)       # 全是拉格 +10
        ok, text = D.drink("G", "甲", "1")
        self.assertIn("喝醉了", text)
        self.assertIn("所有人都喝醉了", text)
        self.assertIn("甲：10 点", D.drunk_list("G"))
        self.assertIn("已经喝醉", D.drink("G", "甲", "2")[1])
        self.assertIn("🏆1. 甲", D.end("G")[1])

    def test_groups_are_separate(self):
        D.join("G1", "甲")
        self.assertIn("没开始", D.status("G2"))
        self.assertTrue(D.join("G2", "甲")[0])


if __name__ == "__main__":
    unittest.main()
