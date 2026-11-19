"""晚餐小游戏：送甜点（毒果）。"""
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "dessert.db"))

from changri.core import dinner, dinner_dessert as D  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class DessertTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")
        dinner.start(4, "无菜", "B", "G")
        for i, n in enumerate(["甲", "乙", "丙"], 1):
            dinner.sit(n, i)

    def inv(self, name):
        return D._load()["inv"].get(name, [])

    def test_pool_is_unique_and_big_enough(self):
        self.assertEqual(len(D.POOL), len(set(D.POOL)))
        self.assertGreaterEqual(len(D.POOL), 30 * 3)                     # 30 个座位每人 3 道

    def test_claim_needs_dinner_and_seat_and_only_once(self):
        self.assertIn("入座", D.claim("丁")[1])
        ok, text = D.claim("甲", random.Random(1))
        self.assertTrue(ok)
        self.assertEqual(text.count("有毒"), 2)
        self.assertEqual(text.count("真甜点"), 1)
        got = self.inv("甲")
        self.assertEqual(len(got), 3)
        self.assertEqual(sum(d["poison"] for d in got), 2)
        self.assertIn("领过", D.claim("甲")[1])
        dinner.stop()
        self.assertIn("没有进行中的晚餐", D.claim("甲")[1])

    def test_names_unique_within_a_dinner(self):
        dinner.start(30, "无菜", "B", "G")
        names = [f"人{i}" for i in range(30)]
        for i, n in enumerate(names, 1):
            dinner.sit(n, i)
            self.assertTrue(D.claim(n)[0])
        all_names = [d["name"] for n in names for d in self.inv(n)]
        self.assertEqual(len(all_names), 90)
        self.assertEqual(len(set(all_names)), 90)

    def test_give_checks_then_cooldown(self):
        D.claim("甲")
        first = self.inv("甲")[0]["name"]
        self.assertIn("用法", D.give("甲", "", "", now=100)[1])
        self.assertIn("自己", D.give("甲", "甲", first, now=100)[1])
        self.assertIn("不在晚餐座位", D.give("甲", "丁", first, now=100)[1])
        self.assertIn("没有「不存在」", D.give("甲", "乙", "不存在", now=100)[1])
        ok, _ = D.give("甲", "乙", first, now=100)                       # 前面的错都不算冷却
        self.assertTrue(ok)
        self.assertEqual(self.inv("乙")[0]["from"], "甲")
        self.assertEqual(len(self.inv("甲")), 2)
        second = self.inv("甲")[0]["name"]
        self.assertIn("秒后", D.give("甲", "丙", second, now=150)[1])
        self.assertTrue(D.give("甲", "丙", second, now=100 + 180)[0])
        set_config("dinner_dessert_cooldown_sec", "0")
        self.assertTrue(D.give("乙", "丙", first, now=281)[0])          # 收到的也能再转送

    def test_views_hide_or_show_truth(self):
        D.claim("甲")
        d = self.inv("甲")[0]
        D.give("甲", "乙", d["name"], now=1)
        mine = D.mine_text("乙")
        self.assertIn(d["name"], mine)
        self.assertIn("甲 送的", mine)
        self.assertNotIn("有毒", mine)
        self.assertNotIn("真甜点", mine.replace("真假", ""))
        self.assertIn("没有甜点", D.mine_text("丙"))
        full = D.all_text()
        self.assertIn("来自 甲", full)
        self.assertIn("有毒", full)

    def test_reveal_ranks_and_locks(self):
        self.assertFalse(D.reveal()[0])
        g = D._load()
        g["inv"] = {"甲": [{"name": "a", "poison": True, "from": ""}, {"name": "b", "poison": False, "from": ""}],
                    "乙": [{"name": "c", "poison": False, "from": "甲"}, {"name": "d", "poison": False, "from": ""}],
                    "丙": [{"name": "e", "poison": False, "from": ""}]}
        D._save(g)
        ok, text = D.reveal()
        self.assertTrue(ok)
        lines = [x for x in text.splitlines() if "道 ·" in x]
        self.assertTrue(lines[0].startswith("🥇 乙"))
        self.assertTrue(lines[1].startswith("🥈 丙"))                   # 都是 1 道真甜点，丙没吃到毒排前面
        self.assertTrue(lines[2].startswith("🥉 甲"))
        self.assertIn("甲 送的", text)
        self.assertIn("揭晓", D.give("乙", "甲", "c")[1])
        self.assertIn("揭晓", D.claim("甲")[1])

    def test_dinner_start_and_stop_clear_desserts(self):
        D.claim("甲")
        self.assertEqual(D.broadcast_target(), ("B", "G"))
        dinner.start(3, "无菜", "B2", "G2")
        self.assertEqual(self.inv("甲"), [])
        dinner.sit("甲", 1)
        D.claim("甲")
        dinner.stop()
        self.assertEqual(self.inv("甲"), [])
        self.assertIsNone(D.broadcast_target())


if __name__ == "__main__":
    unittest.main()
