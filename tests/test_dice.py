import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "d.db"))
os.environ["QQ_BOTS"] = "[]"
from changri.core import attrs, dice, roles  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


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
        self.assertIn("掷了 D100，出了", dice.rd(""))
        self.assertIn("掷了 D20，出了", dice.rd("d20"))
        self.assertIn("为「伤害」", dice.rd("2d6+3 伤害"))
        self.assertIn("5 轮", dice.rd("5#2d6"))
        self.assertIn("最多", dice.rd("11#d6"))
        self.assertIn("超出范围", dice.rd("101d6"))
        self.assertIn("弃", dice.rd("4d6k3"))
        self.assertIn("掷了 D100，出了", dice.rd("100"))
        self.assertIn("掷了 D6，出了", dice.rd("ｄ6"))                            # 全角

    def test_rd_reads_like_a_person_said_it(self):
        """2026-11-21：「D2　2」看不懂 → 有角色名时名字打头，点数带「点」，卡片同一句话。"""
        r = dice.rd("d2", "绵绵面")
        self.assertTrue(r.startswith("绵绵面掷了 D2，出了 "), r)
        self.assertIn(" 点。", r)
        self.assertIn("**绵绵面**掷了 D2，出了 **", r.card)
        self.assertTrue(dice.rd("d2").startswith("掷了 D2，出了 "))                    # 没角色的人不写名字
        self.assertIn("：", dice.rd("2d6+3", "甲").split("掷了 2D6+3")[1][:1])
        self.assertIn("一共", dice.rd("2d6+3", "甲"))
        self.assertIn("甲把 1D6 掷了 3 轮", dice.rd("3#d6", "甲"))

    def test_dc_ac_written_the_usual_ways(self):
        """2026-11-21：玩家常写 dc15 / DC 15 / ac14，以前被当成检定名，不判过没过。"""
        for raw in ("敏捷 15", "敏捷 dc15", "敏捷 DC15", "敏捷 DC 15"):
            out = dice.dnd_check(raw, 0, lookup_none)
            self.assertIn("vs DC15", out, raw)
            self.assertTrue(out.startswith("敏捷 检定"), out)
        for raw in ("长剑 +5 14 1d8+3", "长剑 +5 ac14 1d8+3", "长剑 +5 AC 14 1d8+3"):
            self.assertIn("vs AC14", dice.dnd_attack(raw), raw)

    def test_initiative_without_mod_no_double_equals(self):
        with mock.patch.object(dice.random, "randint", return_value=17):
            out = dice.initiative({}, "乙")
        self.assertIn("乙 先攻：D20=17。", out)
        self.assertNotIn("=17=17", out)

    def test_roll_always_in_range(self):
        for _ in range(300):
            node = dice.parse_expr("2d6+3")
            _, v = dice.roll_terms(node)
            self.assertTrue(5 <= v <= 15)
        for _ in range(100):
            v = int(dice.rd("d100").split("出了 ")[1].split(" 点")[0])
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
            self.assertTrue(1 <= v <= 100 and "奖励骰 2 颗" in note and "取小的" in note)
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
        self.assertIn("按 100 算", dice.coc_check("力量", 0, lambda n: ("某人", 150)))
        self.assertIn("替某人掷", dice.coc_check("力量", 0, lambda n: ("某人", 60), proxy=True))

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
        self.assertIn("（某人的理智）", dice.coc_sanity("1/1d6", lambda n: ("某人", 55)))

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
        self.assertIn("力量 16，调整值 +3", dice.dnd_check("力量 15", 0, lambda n: ("某人", 16)))
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

    def test_initiative_turns(self):
        t, u = {}, {}
        self.assertIn("空的", dice.initiative(t, "下一个", u))
        t.update({"甲": (18, 0), "乙": (13, 0), "丙": (5, 0)})
        self.assertIn("/ri 开始", dice.initiative(t, "", u))
        self.assertTrue(dice.initiative(t, "开始", u).startswith("第 1 轮 · 轮到 甲（18）\n下一个：乙"))
        dice.initiative(t, "下一个", u)
        self.assertEqual(u, {"round": 1, "cur": "乙"})
        dice.initiative(t, "丁 +0", u)                                                # 中途加人：轮到的还是乙
        self.assertEqual(u["cur"], "乙")
        self.assertIn("▶", dice.initiative(t, "", u))
        dice.initiative(t, "删 丁", u)
        dice.initiative(t, "next", u)
        out = dice.initiative(t, "下一个", u)
        self.assertTrue(out.startswith("第 2 轮 · 轮到 甲"), out)                      # 转一圈进下一轮
        out = dice.initiative(t, "删 甲", u)
        self.assertIn("现在轮到 乙", out)                                              # 删的是轮到的人：轮到他后面那位
        self.assertIn("改成 30", dice.initiative(t, "改 丙 30", u))
        self.assertEqual(t["丙"][0], 30)
        self.assertIn("没有「戊」", dice.initiative(t, "删 戊", u))
        self.assertIn("这么写", dice.initiative(t, "改 丙", u))
        dice.initiative(t, "清空", u)
        self.assertEqual((t, u), ({}, {}))

    def test_ww_and_rf(self):
        import random
        r = random.Random(3)
        out = dice.ww("5", r)
        self.assertRegex(out, r"^骰池 5D10（≥8 成功，10 加骰）：\[\d+(,\d+){4}\]")
        self.assertIn("潜行 骰池 6D10（≥6 成功", dice.ww("6 难度6 潜行", r))
        self.assertIn("≥8 加骰", dice.ww("5a8", r))
        self.assertIsNone(dice.ww("abc"))
        self.assertIn("1～40", dice.ww("0"))
        self.assertIn("5～10", dice.ww("5a3"))
        with mock.patch("random.Random.randint", return_value=10):                    # 一直掷 10：加骰有上限，不会停不下来
            self.assertLess(len(dice.ww("40", random.Random())), 3000)
        with mock.patch("random.Random.randint", return_value=1):
            self.assertIn("—— 失败", dice.ww("3", random.Random()))
        out = dice.rf("+2 运动", r)
        self.assertRegex(out, r"^运动 FATE：\[[−□+]( [−□+]){3}\] = [+-]\d \+2 → [+-]\d+，")
        self.assertIn("±12", dice.rf("+20"))
        self.assertEqual(dice.fate_word(3), "好（+3）")
        self.assertIn("超越传奇", dice.fate_word(10))
        self.assertIn("/ww 5", dice.help_text("ww", "coc"))

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


def lookup_60(name):
    return "阿明", 60


class FixRegressionTests(unittest.TestCase):
    """2026-11-14 修的几处：rd 原因写前面、/ra 技能+修正、DND 优势劣势抵消，以及连掷 / 对抗 / 写回字段 / 卡片。"""

    def setUp(self):
        random.seed(11)

    def test_rt_difficulty_dice(self):
        for _ in range(200):
            res = dice.rt("12 7")
            self.assertTrue(1 <= res.roll <= 12)
            self.assertEqual(res.verdict, "成功" if res.roll <= 7 else "失败")
            self.assertIn(f"D12={res.roll}/7", res)
            self.assertIn("难度骰", res.card)
        self.assertEqual(dice.rt("1 1").verdict, "成功")                   # D1 永远掷 1

    def test_rt_remark_and_marks(self):
        from changri.core import dicecard
        with mock.patch("random.randint", return_value=7):
            r = dice.rt("12 7")                                          # 正好压线
        self.assertTrue(any(x in r for x in dice.RT_REMARKS["edge"]))
        self.assertTrue(any(x in r.card for x in dice.RT_REMARKS["edge"]))
        self.assertIn(r.card.split()[1], dicecard.MARKS["难度骰成功"].split())
        with mock.patch("random.randint", return_value=8):
            r = dice.rt("12 7")                                          # 只差 1
        self.assertIn("—— 失败。", r)                                    # 跑团记录靠这个认结果
        self.assertTrue(any(x in r for x in dice.RT_REMARKS["near"]))
        self.assertIn(r.card.split()[1], dicecard.MARKS["难度骰失败"].split())
        with mock.patch("random.randint", return_value=12):
            r = dice.rt("12 7")
        self.assertTrue(any(x in r for x in dice.RT_REMARKS["fail"]))
        with mock.patch("random.randint", return_value=1):
            r = dice.rt("1 1")
        self.assertTrue(any(x in r for x in dice.RT_REMARKS["pass"]))    # 面数 ≤ 成功线不算压线
        seen = {dicecard.mark_for("难度骰成功") for _ in range(200)}
        self.assertGreater(len(seen), 1)                                 # 表情会换
        self.assertEqual({dicecard.mark_for("大成功") for _ in range(20)}, {"🎉"})   # 别的等级照旧一个
        self.assertEqual(dice.rt("16 0 x"), "成功线至少是 1。")
        self.assertIn("撬锁：D16=", dice.rt("d16 7 撬锁"))                 # 面数前可以带 d，后面是原因
        self.assertIn("D16=", dice.rt("１６ ７"))                          # 全角数字
        self.assertIn("用法", dice.rt(""))
        self.assertIn("用法", dice.rt("12"))
        self.assertIn("用法", dice.rt("12 撬锁"))
        self.assertIn("面数要在", dice.rt("0 7"))
        self.assertIn("面数要在", dice.rt("5000 7"))

    def test_rt_skill_from_card(self):
        look = lambda name: ("小明", 7) if name == "侦查" else "没有这个属性"
        for _ in range(100):
            res = dice.rt("12 侦查 找线索", look)
            self.assertIn(f"小明的侦查 找线索：D12={res.roll}/7", res)
            self.assertEqual(res.verdict, "成功" if res.roll <= 7 else "失败")
        self.assertEqual(dice.rt("12 潜行", look), "没有这个属性")
        self.assertIn("初始值", dice.rt("12 侦查", lambda n: ("小明", 25, "，初始值")))

    def test_rd_reason_before_expression(self):
        out = dice.rd("伤害 2d6+3")
        self.assertIn("为「伤害」掷了 2D6+3：", out)
        self.assertNotIn("D100", out)
        self.assertIn("掷了 2D6+3：", dice.rd("2d6+3 伤害"))                    # 写后面的老写法不变
        self.assertIn("掷了 D100，出了", dice.rd("第 3 轮"))                          # 末尾没有 d 的词不会被当成 D3
        self.assertIn("为「攻击」", dice.rd("3#d6 攻击"))
        self.assertIn("掷了 3 轮", dice.rd("攻击 3#d6"))
        self.assertIn("超出范围", dice.rd("伤害 101d6"))

    def test_coc_modifier_glued_to_skill(self):
        self.assertIn("/70（阿明的侦查 60+10）", dice.coc_check("侦查+10", 0, lookup_60))
        self.assertIn("/50（阿明的侦查 60-10）", dice.coc_check("侦查-10", 0, lookup_60))
        self.assertIn("/50（70-20）", dice.coc_check("侦查 70-20", 0, lookup_none))
        self.assertIn("/45（阿明的侦查 60-15）", dice.coc_check("侦查-10 -5", 0, lookup_60))      # 连着写几个
        self.assertIn("/100（阿明的侦查 60+60，按 100 算）", dice.coc_check("侦查+60", 0, lookup_60))
        self.assertIn("掷不出成功", dice.coc_check("侦查-70", 0, lookup_60))
        self.assertIn("只剩", dice.coc_check("侦查 30-40", 0, lookup_none))
        self.assertIn("数值有误", dice.coc_check("侦查 99+999", 0, lookup_none))
        self.assertIn("/70", dice.coc_check("困难 b 侦查+10", 0, lookup_60))                       # 和奖励骰、难度一起用
        self.assertIn("/60", dice.coc_check("侦查 60", 0, lookup_none))                            # 没写修正的不受影响
        self.assertIn("格式", dice.coc_check("+10", 0, lookup_none))                               # 只有修正没有技能：当作没写

    def test_dnd_advantage_and_disadvantage_cancel(self):
        for raw in ("运动 优势 劣势", "劣势 运动 adv", "dis adv"):
            out = dice.dnd_check(raw, 0, lookup_none)
            self.assertNotIn("（优势", out)
            self.assertNotIn("（劣势", out)
        self.assertNotIn("（优势", dice.dnd_check("优势", -1, lookup_none))                    # /rp 又写了优势：抵消
        self.assertIn("（优势", dice.dnd_check("优势", 0, lookup_none))
        self.assertIn("（劣势", dice.dnd_check("劣势", 0, lookup_none))
        out = dice.dnd_attack("长剑 +5 15 1d8+3 优势 劣势")
        self.assertNotIn("（优势", out)
        self.assertNotIn("（劣势", out)

    def test_dnd_glued_modifier(self):
        out = dice.dnd_check("侦察+3 15", 0, lookup_none)
        self.assertIn("侦察 检定", out)
        self.assertIn("vs DC15", out)
        self.assertRegex(out, r"D20=\d+\+3=")

    def test_dnd_ability_vs_skill_bonus(self):
        self.assertIn("力量 16，调整值 +3", dice.dnd_check("力量", 0, lambda n: ("某人", 16)))
        self.assertIn("STR 16，调整值 +3", dice.dnd_check("STR", 0, lambda n: ("某人", 16)))
        out = dice.dnd_check("运动", 0, lambda n: ("某人", 5))                               # 技能存的是加值
        self.assertIn("运动加值 +5", out)
        self.assertRegex(out, r"D20=\d+\+5=")
        self.assertIn("修正最多", dice.dnd_check("运动", 0, lambda n: ("某人", 45)))

    def test_multi_check(self):
        out = dice.coc_check("3#侦查 60", 0, lookup_none)
        self.assertIn("连续检定 ×3", out)
        self.assertEqual(sum(1 for ln in out.splitlines() if ln.startswith("第")), 3)
        self.assertRegex(out, r"成功 \d 次，失败 \d 次")
        self.assertIn("最多连掷", dice.coc_check("11#侦查 60", 0, lookup_none))
        self.assertIn("格式", dice.coc_check("3#", 0, lookup_none))
        self.assertIn("没有属性", dice.coc_check("3#力量", 0, lookup_none))                  # 读不到属性的提示照旧

    def test_oppose(self):
        self.assertIn("格式", dice.coc_oppose("力量 60", lookup_none))
        self.assertIn("甲方：", dice.coc_oppose("力量 vs 力量 50", lookup_none))                # 一边读不到数值，指明是哪一边
        with mock.patch.object(dice, "roll_d100", side_effect=[(10, ""), (90, "")]):
            out = dice.coc_oppose("力量 60 vs 力量 50", lookup_none)
        self.assertIn("甲方胜出（成功等级更高）", out)
        with mock.patch.object(dice, "roll_d100", side_effect=[(40, ""), (45, "")]):          # 都是普通成功：比数值
            out = dice.coc_oppose("力量 60 vs 力量 50", lookup_none)
        self.assertIn("甲方胜出（等级相同，数值更高）", out)
        with mock.patch.object(dice, "roll_d100", side_effect=[(90, ""), (80, "")]):          # 都失败：平局
            self.assertIn("平局", dice.coc_oppose("力量 60 对抗 力量 50", lookup_none))
        with mock.patch.object(dice, "roll_d100", side_effect=[(70, ""), (3, "")]):
            self.assertIn("乙方胜出", dice.coc_oppose("b 力量 60 VS 力量 50", lookup_none))

    def test_sanity_hints_and_result_fields(self):
        out = dice.coc_sanity("5/5 60", lookup_none)
        self.assertIn("智力检定", out)                                                       # 单次损失 ≥5
        self.assertNotIn("智力检定", dice.coc_sanity("1/4 60", lookup_none))                          # 损失 <5 不提醒
        self.assertIn("永久疯狂", dice.coc_sanity("99/99 50", lookup_none))                  # 理智归零
        res = dice.coc_sanity("5/5", lambda n: ("某人", 55))
        self.assertTrue(res.from_lookup)
        self.assertEqual((res.san_before, res.san_left, res.loss, res.who), (55, 50, 5, "某人"))
        self.assertFalse(dice.coc_sanity("5/5 55", lookup_none).from_lookup)                 # 自己写的数值：不写回

    def test_growth_from_card(self):
        self.assertIn("格式", dice.coc_growth("侦查"))
        self.assertIn("没有属性", dice.coc_growth("侦查", lookup_none))
        gains = []
        for _ in range(40):
            res = dice.coc_growth("侦查", lambda n: ("某人", 5))
            self.assertTrue(res.from_lookup)
            self.assertEqual((res.label, res.value, res.who), ("侦查", 5, "某人"))
            gains.append(res.gain)
        self.assertTrue(any(g > 0 for g in gains) and all(0 <= g <= 10 for g in gains))
        self.assertFalse(dice.coc_growth("侦查 55", lambda n: ("某人", 5)).from_lookup)       # 写了数值就不读卡

    def test_cards(self):
        c = dice.coc_check("侦查 70", 0, lookup_none).card
        self.assertTrue(c.startswith("### ") and "D100" in c and "目标 ≤ 70" in c)
        self.assertIn("困难要求", dice.coc_check("困难 侦查 70", 0, lookup_none).card)
        self.assertIn("奖励骰", dice.coc_check("b2 侦查 70", 0, lookup_none).card)
        self.assertIn("＊粗＊", dice.coc_check("*粗* 70", 0, lookup_none).card)               # 玩家写的字不会变成格式
        self.assertIn("D20", dice.dnd_check("运动 +2 15", 0, lookup_none).card)
        self.assertIn("理智", dice.coc_sanity("1/1d6 60", lookup_none).card)
        self.assertIn("成长检定", dice.coc_growth("侦查 55").card)
        self.assertTrue(dice.rd("2d6+3").card.startswith("### "))
        self.assertIn("第3轮", dice.rd("3#2d6").card)
        outs = [dice.dnd_attack("长剑 +5 15 1d8+3") for _ in range(200)]
        self.assertTrue(all(o.card.startswith("### ") for o in outs))
        self.assertTrue(any("伤害" in o.card for o in outs))
        for o in (dice.coc_check("", 0, lookup_none), dice.coc_check("侦查-70", 0, lookup_60), dice.rd("101d6")):    # 报错提示不做卡片
            self.assertIsNone(getattr(o, "card", None))


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

    def test_get_attr_aliases(self):
        attrs.set_attrs(self.rid, attrs.parse_pairs("力量60 SAN55"))
        self.assertEqual(attrs.get_attr(self.rid, "STR"), 60)            # 存的是中文名，用英文名也能读
        self.assertEqual(attrs.get_attr(self.rid, "理智"), 55)
        attrs.set_attrs(self.rid, attrs.parse_pairs("DEX70"))
        self.assertEqual(attrs.get_attr(self.rid, "敏捷"), 70)           # 反过来也行
        self.assertIsNone(attrs.get_attr(self.rid, "意志"))

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

    def test_madness_sub_tables(self):
        from changri.core import madness_tables as mt
        self.assertEqual((len(mt.PHOBIAS), len(mt.MANIAS)), (100, 100))
        for table in (mt.PHOBIAS, mt.MANIAS):
            self.assertEqual(len({n for n, _ in table}), 100)                       # 名字不重复
            self.assertTrue(all(n and d for n, d in table))
        self.assertNotIn("请掷 1D100", "".join(dice.TEMP_MADNESS + dice.SUMMARY_MADNESS))   # 不再让人自己去掷
        for idx, kind, table in ((9, "恐惧症状", mt.PHOBIAS), (10, "狂躁症状", mt.MANIAS)):
            for summary in (False, True):
                with mock.patch.object(dice.random, "randint", side_effect=[idx, 3, 42]):
                    out = dice.madness("", summary)
                self.assertIn(f"{kind}（1D100=42）：{table[41][0]}，", out)
        with mock.patch.object(dice.random, "randint", side_effect=[5, 3]):
            self.assertNotIn("症状（1D100", dice.madness("", False))                  # 别的症状不带子表
        for _ in range(100):                                                         # 边界点数 1 和 100 都取得到
            self.assertIn("1D100=", dice.phobia())
            self.assertIn("1D100=", dice.mania())
        with mock.patch.object(dice.random, "randint", side_effect=[1, 100]):
            self.assertIn(mt.PHOBIAS[0][0], dice.phobia())
            self.assertIn(mt.MANIAS[99][0], dice.mania())

    def test_multi_check_lines_end_with_period(self):
        out = dice.coc_check("3#侦查 60", 0, lookup_none)
        rounds = [ln for ln in out.splitlines() if ln.startswith("第")]
        self.assertEqual(len(rounds), 3)
        self.assertTrue(all(ln.endswith("。") for ln in rounds), rounds)

    def test_madness(self):
        random.seed(3)
        t = dice.madness("", False)
        self.assertIn("临时疯狂", t)
        self.assertIn("轮", t)
        s = dice.madness("甲", True)
        self.assertIn("甲的总结疯狂", s)
        self.assertIn("小时", s)
        self.assertEqual(len(dice.TEMP_MADNESS), 10)
        self.assertEqual(len(dice.SUMMARY_MADNESS), 10)


if __name__ == "__main__":
    unittest.main()
