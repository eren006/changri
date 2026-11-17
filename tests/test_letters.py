"""写信综（个性化模块）：寄信、额度、赏金、望远镜、羽毛笔、收尾清空、迁移。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "lt.db"))

from changri.core import letters as L, roles, rpg, season  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

LETTER = "【收件人】乙\n【内容】今天的月色很好，你呢？\n【日期】三月初五\n【附件】一片银杏叶"


class Letters(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("letter_effects", "letters", "rpg_inventory", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for i, n in enumerate("甲乙丙"):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        self.r = {n: roles.get_role_by_name(n) for n in "甲乙丙"}

    def send(self, who="甲", text=LETTER):
        p = L.parse(text)
        return L.compose(self.r[who], roles.get_role_by_name(p["to"]), p)

    def coins(self, who):
        return next((x["count"] for x in rpg.inventory(self.r[who]["id"]) if x["name"] == L.COIN), 0)

    def test_parse_and_send(self):
        p = L.parse(LETTER + "\n【署名】无名氏")
        self.assertEqual((p["to"], p["date"], p["sign"]), ("乙", "三月初五", "无名氏"))
        ok, reply, out = self.send()
        self.assertTrue(ok)
        self.assertIn("已寄给「乙」", reply)
        rid, text = out["deliver"]
        self.assertEqual(rid, self.r["乙"]["id"])
        self.assertIn("寄给 乙 的信", text)                       # 抬头让出图画成信纸
        self.assertIn("一片银杏叶", text)
        self.assertTrue(rpg.find_item("写信币") and rpg.find_item("望远镜") and rpg.find_item("羽毛笔"))   # 自动建好
        self.assertFalse(self.send(text="【收件人】乙")[0])          # 没内容
        self.assertFalse(L.compose(self.r["甲"], self.r["甲"], L.parse("【收件人】甲【内容】x"))[0])

    def test_limit_cooldown_reward(self):
        set_config("wl_daily_limit", "2"); set_config("wl_reward", "3"); set_config("wl_min_chars", "8")
        self.assertIn("写信币 +3", self.send()[1])
        self.assertIn("字数不到 8", self.send(text="【收件人】乙【内容】短")[1])
        self.assertEqual(self.coins("甲"), 3)
        self.assertIn("今天已经寄了 2/2", self.send()[1])
        set_config("wl_daily_limit", "0"); set_config("wl_cooldown_min", "30")
        self.assertIn("冷却", self.send()[1])
        self.assertIn("今天已寄 2", L.status(self.r["甲"]["id"]))

    def test_telescope_copies_next_letter(self):
        L.ensure_items()
        self.assertFalse(L.apply_item(self.r["丙"], self.r["甲"], "telescope")[0])   # 背包里没有
        rpg.adjust(self.r["丙"]["id"], "望远镜", 1)
        self.assertTrue(L.apply_item(self.r["丙"], self.r["甲"], "telescope")[0])
        ok, _, out = self.send()
        self.assertEqual(out["copy"][0], self.r["丙"]["id"])
        self.assertIn("望远镜抄录", out["copy"][1])
        self.assertIsNotNone(out["deliver"])
        self.assertIsNone(self.send()[2]["copy"])                                   # 只管下一封

    def test_quill_holds_and_edits(self):
        L.ensure_items()
        rpg.adjust(self.r["丙"]["id"], "羽毛笔", 1)
        L.apply_item(self.r["丙"], self.r["甲"], "quill")
        ok, reply, out = self.send()
        self.assertTrue(ok)
        self.assertIsNone(out["deliver"])                                          # 被截住了
        self.assertEqual(out["held"][0], self.r["丙"]["id"])
        self.assertIn("羽毛笔修改 1", out["held"][1])
        self.assertIn("1. 甲 → 乙", L.held_list(self.r["丙"]["id"]))
        self.assertFalse(L.quill_edit(self.r["丙"]["id"], 2, "x")[0])
        ok, _, letter = L.quill_edit(self.r["丙"]["id"], 1, "我讨厌月亮。")
        self.assertTrue(ok)
        self.assertEqual(letter[0], self.r["乙"]["id"])
        self.assertIn("我讨厌月亮", letter[1])
        self.assertIn("没有待修改", L.held_list(self.r["丙"]["id"]))

    def test_quill_skips_letter_to_applier(self):
        L.ensure_items()
        rpg.adjust(self.r["丙"]["id"], "羽毛笔", 1)
        L.apply_item(self.r["丙"], self.r["甲"], "quill")
        out = self.send(text="【收件人】丙【内容】给你的信")[2]
        self.assertIsNotNone(out["deliver"])                                       # 寄给拿羽毛笔的人：不截

    def test_wipe_clears_letters(self):
        self.send()
        season.wipe()
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM letters").fetchone()[0], 0)


    def test_cost_for_wish_and_date(self):
        from changri.core import extensions
        self.assertEqual(L.check_cost(self.r["甲"]["id"], "date"), "")                 # 没开写信综：不收
        extensions.set_enabled("letters", True)
        set_config("wl_date_cost", "3")
        self.assertEqual(L.check_cost(self.r["甲"]["id"], "wish"), "")                 # 心愿没设：不收
        self.assertIn("要花 3 个写信币，你只有 0 个", L.check_cost(self.r["甲"]["id"], "date"))
        L.ensure_items()
        rpg.adjust(self.r["甲"]["id"], L.COIN, 5)
        self.assertEqual(L.check_cost(self.r["甲"]["id"], "date"), "")
        self.assertIn("还剩 2 个", L.pay(self.r["甲"]["id"], "date"))
        extensions.set_enabled("letters", False)


if __name__ == "__main__":
    unittest.main()
