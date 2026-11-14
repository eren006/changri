import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "d.db"))
os.environ["QQ_BOTS"] = "[]"
from core import attrs, dice, roles  # noqa: E402
from core.db import get_conn, init_db  # noqa: E402


def lookup_none(name):
    return "没有属性"


class DiceTests(unittest.TestCase):
    def setUp(self):
        random.seed(7)

    def test_parse(self):
        self.assertIsNotNone(dice.parse_expr("3d6+2d4-1"))
        self.assertIsNotNone(dice.parse_expr("4d6k3"))
        self.assertIsNotNone(dice.parse_expr("(1d6+2)*3"))
        self.assertIsNone(dice.parse_expr("abc"))
        self.assertIsNone(dice.parse_expr("1d"))
        self.assertIsNone(dice.parse_expr("d6d6d6d6d6d6d6d6d6d6"))      # 项数超限

    def test_rd_basic_and_limits(self):
        self.assertIn("D100 =", dice.rd(""))
        self.assertIn("D20 =", dice.rd("d20"))
        self.assertIn("伤害：", dice.rd("2d6+3 伤害"))
        self.assertIn("5 轮", dice.rd("5#2d6"))
        self.assertIn("最多", dice.rd("11#d6"))
        self.assertIn("超出范围", dice.rd("101d6"))
        self.assertIn("弃", dice.rd("4d6k3"))
        self.assertIn("D100 =", dice.rd("100"))
        self.assertIn("D6 =", dice.rd("ｄ6"))                            # 全角

    def test_roll_always_in_range(self):
        for _ in range(300):
            node = dice.parse_expr("2d6+3")
            _, v = dice.roll_terms(node)
            self.assertTrue(5 <= v <= 15)
        for _ in range(100):
            v = int(dice.rd("d100").split("=")[1].split("。")[0])
            self.assertTrue(1 <= v <= 100)

    def test_judge_success_table(self):
        self.assertEqual(dice.judge_success(1, 30), "大成功")
        self.assertEqual(dice.judge_success(5, 60), "大成功")
        self.assertEqual(dice.judge_success(6, 60), "极难成功" if 6 <= 12 else "x")
        self.assertEqual(dice.judge_success(100, 60), "大失败")
        self.assertEqual(dice.judge_success(96, 40), "大失败")
        self.assertEqual(dice.judge_success(97, 60), "失败")
        self.assertEqual(dice.judge_success(30, 60), "困难成功")
        self.assertEqual(dice.judge_success(45, 60), "成功")
        self.assertEqual(dice.judge_success(61, 60), "失败")

    def test_bonus_penalty_dice(self):
        for _ in range(200):
            v, note = dice.roll_d100(2)
            self.assertTrue(1 <= v <= 100 and "奖励骰×2" in note)
            v, note = dice.roll_d100(-1)
            self.assertTrue(1 <= v <= 100 and "惩罚骰" in note)

    def test_coc_check_forms(self):
        self.assertIn("侦查 检定：D100=", dice.coc_check("侦查 70", 0, lookup_none))
        self.assertIn("困难要求≤35", dice.coc_check("困难 侦查 70", 0, lookup_none))
        self.assertIn("奖励骰", dice.coc_check("b2 射击 60", 0, lookup_none))
        self.assertIn("格式", dice.coc_check("", 0, lookup_none))
        self.assertEqual(dice.coc_check("力量", 0, lookup_none), "没有属性")
        self.assertIn("最多 5 颗", dice.coc_check("b9 侦查 60", 0, lookup_none))
        self.assertIn("数值有误", dice.coc_check("侦查 0", 0, lookup_none))
        self.assertIn("按100算", dice.coc_check("力量", 0, lambda n: ("某人", 150)))
        self.assertIn("代某人投", dice.coc_check("力量", 0, lambda n: ("某人", 60), proxy=True))

    def test_difficulty_verdict(self):
        random.seed(1)
        seen = {dice.coc_check("极难 侦查 70", 0, lookup_none).split("—— ")[1].split("。")[0] for _ in range(200)}
        self.assertTrue(seen & {"极难成功", "极难失败"})

    def test_sanity(self):
        out = dice.coc_sanity("1/1d6 60", lookup_none)
        self.assertIn("理智检定", out)
        self.assertIn("理智 60 →", out)
        self.assertIn("格式", dice.coc_sanity("乱写", lookup_none))
        self.assertIn("看不懂", dice.coc_sanity("x/y 60", lookup_none))
        self.assertIn("取自某人的 SAN", dice.coc_sanity("1/1d6", lambda n: ("某人", 55)))

    def test_growth(self):
        outs = {dice.coc_growth("侦查 55")[:10] for _ in range(1)}
        self.assertTrue(all("成长检定" in dice.coc_growth("侦查 55") for _ in range(30)))
        self.assertIn("格式", dice.coc_growth("侦查"))

    def test_dnd(self):
        self.assertEqual(dice.ability_mod(8), -1)
        self.assertEqual(dice.ability_mod(15), 2)
        self.assertIn("vs DC15", dice.dnd_check("侦察 +3 15", 0, lookup_none))
        self.assertIn("优势", dice.dnd_check("优势 +2", 0, lookup_none))
        self.assertIn("劣势", dice.dnd_check("", -1, lookup_none))
        self.assertIn("修正最多", dice.dnd_check("+99", 0, lookup_none))
        self.assertIn("力量16→+3", dice.dnd_check("力量 15", 0, lambda n: ("某人", 16)))
        stats = dice.dnd_stats("2")
        self.assertIn("第2组", stats)
        self.assertIn("一次最多", dice.dnd_stats("9"))

    def test_attack_crit_and_miss(self):
        random.seed(3)
        outs = [dice.dnd_attack("长剑 +5 15 1d8+3") for _ in range(300)]
        self.assertTrue(any("暴击" in o for o in outs))
        self.assertTrue(any("未命中" in o for o in outs))
        self.assertTrue(any("自然1" in o for o in outs))
        self.assertTrue(any("命中" in o and "伤害：" in o for o in outs))
        crit = next(o for o in outs if "暴击" in o)
        self.assertIn("2D8+3", crit)                                    # 暴击骰子翻倍，加值不变
        self.assertIn("格式", dice.dnd_attack("长剑"))

    def test_initiative(self):
        t = {}
        self.assertIn("空的", dice.initiative(t, ""))
        dice.initiative(t, "阿尔 +2")
        out = dice.initiative(t, "贝塔 +0")
        self.assertIn("先攻顺序", out)
        self.assertEqual(len(t), 2)
        self.assertIn("清掉", dice.initiative(t, "清空"))
        self.assertEqual(t, {})

    def test_help(self):
        self.assertIn("COC7", dice.help_text("", "coc"))
        self.assertIn("DND5e", dice.help_text("dnd", "coc"))
        self.assertIn("录属性", dice.help_text("属性", "coc"))


class AttrTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM roles")
        ok, msg = roles.create_role("甲", "b1", "g1", "m1")
        assert ok, msg
        self.rid = roles.get_role_by_name("甲")["id"]

    def test_parse_pairs(self):
        self.assertEqual(attrs.parse_pairs("力量60 敏捷 70 SAN:55"), [("力量", 60), ("敏捷", 70), ("SAN", 55)])
        self.assertEqual(attrs.parse_pairs("理智５５"), [("SAN", 55)])
        self.assertEqual(attrs.parse_pairs("san 40 san 41"), [("SAN", 41)])
        self.assertEqual(attrs.parse_pairs("没有数字"), [])

    def test_set_get_delete(self):
        ok, _ = attrs.set_attrs(self.rid, attrs.parse_pairs("力量60 str 70"))
        self.assertTrue(ok)
        self.assertEqual(attrs.get_attr(self.rid, "力量"), 60)
        self.assertEqual(attrs.get_attr(self.rid, "STR"), 70)
        self.assertEqual(attrs.get_attr(self.rid, "str"), 70)           # 英文不分大小写
        attrs.set_attrs(self.rid, [("力量", 65)])
        self.assertEqual(attrs.get_attr(self.rid, "力量"), 65)
        self.assertTrue(attrs.delete_attr(self.rid, "力量"))
        self.assertFalse(attrs.delete_attr(self.rid, "力量"))
        self.assertEqual(attrs.clear_attrs(self.rid), 1)

    def test_limit_and_cascade(self):
        ok, err = attrs.set_attrs(self.rid, [(f"项{chr(0x4e00 + i)}", i) for i in range(attrs.MAX_ATTRS + 1)])
        self.assertFalse(ok)
        attrs.set_attrs(self.rid, [("力量", 1)])
        roles.delete_role(self.rid)
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM role_attrs").fetchone()[0], 0)    # 删角色时属性一起删


class HouseRuleAndMadnessTests(unittest.TestCase):
    def test_house_rules(self):
        j = dice.judge_success
        self.assertEqual(j(3, 70, "1"), "大成功")
        self.assertEqual(j(3, 70, "0"), "极难成功" if 3 <= 70 // 5 else "x")     # 规则书：只有 1 才大成功
        self.assertEqual(j(1, 30, "0"), "大成功")
        self.assertEqual(j(99, 70, "1"), "失败")
        self.assertEqual(j(99, 70, "5"), "大失败")                              # 房规 5：满 50 出 99-100 大失败
        self.assertEqual(j(98, 40, "3"), "大失败")                              # 房规 3：96-100 都算
        self.assertEqual(j(4, 3, "2"), "失败")                                  # 房规 2：大成功要 ≤ 成功率
        self.assertEqual(j(4, 40, "2"), "大成功")
        self.assertEqual(j(97, 20, "2"), "大失败")
        self.assertEqual(j(97, 98, "2"), "成功")
        self.assertEqual(j(5, 60, "4"), "大成功" if 5 <= 60 // 10 else "极难成功")   # 60//10=6，5 ≤ 6
        self.assertEqual(j(7, 60, "4"), "极难成功" if 7 <= 12 else "x")
        self.assertEqual(j(100, 30, "4"), "大失败")
        self.assertEqual(j(2, 100, "5"), "大成功")
        self.assertEqual(j(3, 100, "5"), "极难成功")
        for h in dice.HOUSE_RULES:
            for r in range(1, 101):
                self.assertIn(j(r, 55, h), dice.LEVEL_RANK)

    def test_check_uses_house(self):
        random.seed(1)
        for h in ("0", "3", "5"):
            self.assertIn("检定", dice.coc_check("侦查 60", 0, lookup_none, house=h))
        self.assertIn("房规 3", dice.house_text("3"))

    def test_madness(self):
        random.seed(3)
        t = dice.madness("", False)
        self.assertIn("临时疯狂", t)
        self.assertIn("轮", t)
        s = dice.madness("甲", True)
        self.assertIn("甲 的总结疯狂", s)
        self.assertIn("小时", s)
        self.assertEqual(len(dice.TEMP_MADNESS), 10)
        self.assertEqual(len(dice.SUMMARY_MADNESS), 10)


if __name__ == "__main__":
    unittest.main()
