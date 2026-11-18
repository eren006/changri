"""同一个 QQ 小号给几个官方机器人用时：一个群只能归一个机器人，授权的 QQ 号也不能两边都填。
那边的 QQ 号连接删了或关了（小号已经解绑），那边的群和授权号就不再算数。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, qqpool  # noqa: E402
from changri.core.db import use_bot  # noqa: E402


class CrossBot(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()
        botconf.add_account("111111", "a" * 20, "甲号")
        botconf.add_account("222222", "b" * 20, "乙号")
        self.lid = {}
        for b in ("111111", "222222"):
            with use_bot(b):
                self.lid[b], err = qqpool.add_link("小号", "ws://127.0.0.1:3001")
                self.assertEqual(err, "")

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def test_group_only_in_one_bot(self):
        with use_bot("111111"):
            self.assertEqual(qqpool.join_set("111111", "500001", "A组", self.lid["111111"]), "")
        with use_bot("222222"):
            err = qqpool.join_set("222222", "500001", "B组", self.lid["222222"])
            self.assertIn("甲号", err)
            self.assertIn("移出", err)
            self.assertEqual(qqpool.join_set("222222", "500002", "B组", self.lid["222222"]), "")   # 别的群照样能加
            self.assertEqual(qqpool.sets("222222")[0]["name"], "B组")
            self.assertEqual([g["real_gid"] for g in qqpool.sets("222222")[0]["groups"]], ["500002"])
            ok, bad = qqpool.add_rooms("222222", "500001\n500003", "B组")
            self.assertEqual(ok, 1)
            self.assertIn("500001", bad[0])
            self.assertEqual(qqpool.held_by_other_bots(), {"500001": "甲号"})
        with use_bot("111111"):                                   # 自己池里已有的群，再加一次照旧能挪组
            self.assertEqual(qqpool.join_set("111111", "500001", "A2组", self.lid["111111"]), "")

    def test_unlinked_bot_does_not_count(self):
        with use_bot("111111"):
            qqpool.join_set("111111", "500001", "A组", self.lid["111111"])
            qqpool.update_link(self.lid["111111"], "小号", "ws://127.0.0.1:3001", None, False)   # 甲号那边把小号连接关了
        with use_bot("222222"):
            self.assertEqual(qqpool.held_by_other_bots(), {})
            self.assertEqual(qqpool.join_set("222222", "500001", "B组", self.lid["222222"]), "")

    def test_auth_qq_only_in_one_bot(self):
        with use_bot("111111"):
            self.assertEqual(qqpool.save_auth("10001\n10002"), [])
        with use_bot("222222"):
            bad = qqpool.save_auth("10001 10003")
            self.assertEqual(len(bad), 1)
            self.assertIn("10001", bad[0])
            self.assertIn("甲号", bad[0])
            self.assertEqual(qqpool.auth_qqs(), {"10003"})
        with use_bot("111111"):
            qqpool.update_link(self.lid["111111"], "小号", "ws://127.0.0.1:3001", None, False)
        with use_bot("222222"):
            self.assertEqual(qqpool.save_auth("10001 10003"), [])
            self.assertEqual(qqpool.auth_qqs(), {"10001", "10003"})


if __name__ == "__main__":
    unittest.main()
