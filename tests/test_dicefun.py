"""小工具和 COC 制卡的纯函数测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from changri.core import dice, dicefun  # noqa: E402


class DiceFun(unittest.TestCase):
    def test_names(self):
        for kind in ("中", "英", "日"):
            out = dicefun.names(f"{kind} 6 女")
            self.assertEqual(len(out.split("：", 1)[1].split("、")), 6)
        self.assertIn("最多", dicefun.names("50"))
        self.assertIn("格式", dicefun.names("火星"))

    def test_who(self):
        out = dicefun.who("甲，乙 丙")
        for x in ("1.", "2.", "3.", "甲", "乙", "丙"):
            self.assertIn(x, out)
        self.assertIn("格式", dicefun.who("甲"))

    def test_draw_no_repeat(self):
        out = dicefun.draw("塔罗 10")
        self.assertEqual(len({line.split("（")[0] for line in out.splitlines()[1:]}), 10)
        self.assertEqual(len(dicefun.draw("扑克 10").split("：")[1].split()), 10)
        self.assertIn("没有「骰子」", dicefun.draw("骰子"))
        self.assertIn("最多", dicefun.draw("遭遇 11"))

    def test_jrrp_fixed_per_day(self):
        a = dicefun.jrrp_value("B|M1", "2026-11-15")
        self.assertEqual(a, dicefun.jrrp_value("B|M1", "2026-11-15"))
        self.assertTrue(1 <= a <= 100)
        vals = {dicefun.jrrp_value("B|M1", f"2026-11-{d:02d}") for d in range(1, 29)}
        self.assertGreater(len(vals), 5)

    def test_coc_db_build_and_stats(self):
        self.assertEqual(dice.coc_db_build(30, 30), ("-2", -2))
        self.assertEqual(dice.coc_db_build(40, 40), ("-1", -1))
        self.assertEqual(dice.coc_db_build(60, 60), ("0", 0))
        self.assertEqual(dice.coc_db_build(70, 60), ("+1D4", 1))
        self.assertEqual(dice.coc_db_build(90, 90), ("+1D6", 2))
        self.assertEqual(dice.coc_db_build(110, 100), ("+2D6", 3))
        self.assertEqual(dice.coc_db_build(150, 140), ("+3D6", 4))
        self.assertEqual(dice.coc_hp_max(50, 65), 11)
        self.assertEqual(dice.coc_mov(40, 40, 60), 7)
        self.assertEqual(dice.coc_mov(70, 70, 60), 9)
        self.assertEqual(dice.coc_mov(70, 40, 60), 8)
        out = dice.coc_stats("2")
        self.assertIn("第2组", out)
        self.assertIn("一次最多", dice.coc_stats("9"))


    def test_judge_dg(self):
        self.assertEqual(dice.judge_dg(1, 5), "大成功")
        self.assertEqual(dice.judge_dg(1, 0), "大失败")
        self.assertEqual(dice.judge_dg(33, 50), "大成功")
        self.assertEqual(dice.judge_dg(55, 50), "大失败")
        self.assertEqual(dice.judge_dg(100, 99), "大失败")
        self.assertEqual(dice.judge_dg(50, 50), "成功")
        self.assertEqual(dice.judge_dg(51, 50), "失败")
        self.assertEqual(dice.dg_hp_max(12, 11), 12)
        self.assertIn("崩溃点", dice.dg_stats("1"))


if __name__ == "__main__":
    unittest.main()
