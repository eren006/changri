"""晚餐小游戏：围猎（并发 / 轮流）、巡逻。"""
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "dg.db"))

from changri.core import dinner, dinner_games as G, roles  # noqa: E402
from changri.core.db import get_conn, init_db, get_config  # noqa: E402


class HuntTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")
        G.rng = random.Random(7)
        dinner.start(4, "古代中餐")
        dinner.sit("甲", 1)
        dinner.sit("乙", 2)

    def put_prey(self, r, c):
        g = G._load(); g["state"]["pos"] = [r, c]; G._save(g)

    def test_needs_dinner_and_players(self):
        dinner.stop()
        self.assertFalse(G.hunt_start("B", "G", [])[0])
        dinner.start(3)
        self.assertIn("还没有人", G.hunt_start("B", "G", [])[1])

    def test_sync_two_phases_and_capture(self):
        ok, text = G.hunt_start("B", "G", [], now=1000)
        self.assertTrue(ok)
        self.assertIn("梅花鹿", text)                                  # 古代中餐 → 梅花鹿
        self.assertIn("侦查阶段", text)
        self.put_prey(1, 1)
        self.assertFalse(G.hunt_act("甲", "开枪", "B2", now=1001)[0])     # 侦查阶段不能开枪
        self.assertFalse(G.hunt_act("丙", "追踪", "B2", now=1001)[0])     # 没入座
        ok, t, _ = G.hunt_act("甲", "追踪", "B2", now=1001)
        self.assertIn("已就位（1/2）", t)
        self.assertFalse(G.hunt_act("甲", "追踪", "A1", now=1002)[0])     # 一阶段只能一次
        ok, t, ended = G.hunt_act("乙", "设陷阱", "C3", now=1002)
        self.assertIn("就在这一格", t)                                    # 出齐了：结算侦查
        self.assertIn("开火阶段", t)
        self.assertFalse(ended)
        G.hunt_act("甲", "开枪", "B2", now=1003)
        ok, t, ended = G.hunt_act("乙", "开枪", "B2", now=1004)           # 两枪都中：第二枪拿下
        self.assertTrue(ended)
        self.assertIn("今夜的功劳", t)
        self.assertIsNone(G.hunt_active())
        self.assertIn("现烤梅花鹿", dinner.state()["dishes"])
        rank = G.hunt_rank()
        self.assertIn("乙　6 分 · 拿下 1 次", rank)                       # 收尾 +5 +1
        self.assertIn("甲　3 分", rank)                                   # 重伤 +2 +1

    def test_sync_deadline_resolves_without_everyone(self):
        G.hunt_start("B", "G", [], now=1000)
        G.hunt_act("甲", "追踪", "A1", now=1001)
        self.assertIsNone(G.hunt_due(now=1050))
        bot, group, text, ended = G.hunt_due(now=1000 + G.PHASE_SECS["scout"] + 1)
        self.assertEqual((bot, group), ("B", "G"))
        self.assertIn("没来得及出手：乙", text)
        self.assertEqual(G.hunt_active()["state"]["phase"], "fire")

    def test_prey_escapes_after_rounds(self):
        G.hunt_start("B", "G", ["4"], now=0)
        st = G._load()["state"]
        t = 0
        for _ in range(st["max_round"] * 2):
            t += 1000
            out = G.hunt_due(now=t)
            if out and out[3]:
                break
        self.assertIn("没有人得分", out[2])
        self.assertIsNone(G.hunt_active())

    def test_turn_mode(self):
        ok, text = G.hunt_start("B", "G", ["轮流", "5", "白狐"])
        self.assertIn("白狐", text)
        self.assertIn("甲，你走在最前面", text)
        self.assertIn("现在轮到 甲", G.hunt_act("乙", "追踪", "A1")[1])
        self.assertFalse(G.hunt_act("甲", "追踪", "Z9")[0])              # 格子写错不算行动
        ok, t, _ = G.hunt_act("甲", "设陷阱", "A1")
        self.assertIn("乙，轮到你了", t)
        self.assertEqual(G.hunt_skip()[0], True)                          # 管理员跳过乙
        self.assertIn("第 2/", G.hunt_status())

    def test_trap_wounds_then_captures(self):
        G.hunt_start("B", "G", ["轮流"])
        g = G._load(); st = g["state"]
        st["pos"] = [0, 0]; st["traps"] = [{"r": 0, "c": 0, "owner": "乙"}]; st["hp"] = 1
        G._save(g)
        self.assertTrue(G._trap(st)[1])                                   # 已带伤再踩中：拿下，算设陷阱的人
        self.assertIn("乙", G.hunt_rank())

    def test_end_dinner_ends_hunt(self):
        G.hunt_start("B", "G", [])
        self.assertTrue(G.hunt_end())
        self.assertFalse(G.hunt_end())

    def test_map_image_and_text_split(self):
        G.hunt_start("B", "G", [], now=1)
        text = G.hunt_status(now=2)
        rest, had = G.split_map(text)
        self.assertTrue(had)
        self.assertNotIn("⬜", rest)                                     # 发图时文字里不再带格子地图和图例
        self.assertNotIn("🪤陷阱", rest)
        self.assertIn("侦查阶段", rest)
        self.assertIn("⬜", G.plain(text))                                # 出不了图：留着文字版
        self.assertTrue(G.hunt_map_png().startswith(b"\x89PNG"))


class GuardTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")
        G.rng = random.Random(3)

    def test_flow_until_three_left(self):
        self.assertFalse(G.guard_join(1, "甲")[0])                        # 还没开局
        self.assertTrue(G.guard_set_locations(["东", "西", "南", "北"])[0])
        self.assertFalse(G.guard_set_locations(["东"])[0])
        ok, text = G.guard_open("B", "G")
        self.assertIn("东、西、南、北", text)
        for i, n in enumerate("甲乙丙丁戊", 1):
            self.assertTrue(G.guard_join(i, n)[0])
        self.assertFalse(G.guard_hide(1, "天上")[0])
        self.assertFalse(G.guard_hide(9, "东")[0])                        # 没报名
        for i, place in zip(range(1, 6), ["东", "西", "南", "北", "东"]):
            G.guard_hide(i, place)
        rounds = 0
        while True:
            ok, r = G.guard_call()
            self.assertTrue(ok)
            rounds += 1
            self.assertLessEqual(len(r["text"].split("危险地点（")[1].split("）")[0]), 1)
            if r["ended"]:
                break
            self.assertFalse(G.guard_join(10, "己")[0])                   # 开始后不能加入
            for rid in r["alive"]:                                        # 活着的人随便再躲一次
                G.guard_hide(rid, random.Random(rid + rounds).choice(r["locations"]))
            self.assertLess(rounds, 20)
        self.assertLessEqual(len(r["alive"]), 3)
        self.assertIn("本场结束", r["text"])
        self.assertIn("已结束", G.guard_status())
        self.assertTrue(G.guard_end())

    def test_no_hide_is_caught(self):
        G.guard_open("B", "G")
        for i, n in enumerate("甲乙丙丁", 1):
            G.guard_join(i, n)
        G.guard_hide(1, "伦敦")
        ok, r = G.guard_call()
        caught = {rid for rid, _ in r["caught"]}
        self.assertTrue({2, 3, 4} <= caught)                              # 没躲的被捕


if __name__ == "__main__":
    unittest.main()
