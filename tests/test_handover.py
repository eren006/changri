"""一个群同一时间只归一方：小号的群号组，或者官方机器人的备用群。来回转、占用中不能转、跨机器人也查官方备用群。"""
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
from changri.core import botconf, handover, pool, qqpool  # noqa: E402
from changri.core.db import get_conn, use_bot  # noqa: E402

A, B = "111111", "222222"


class Handover(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()
        botconf.add_account(A, "a" * 20, "甲号")
        botconf.add_account(B, "b" * 20, "乙号")
        self.lid = {}
        for b in (A, B):
            with use_bot(b):
                self.lid[b], _ = qqpool.add_link("小号", "ws://127.0.0.1:3001")

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def test_round_trip(self):
        with use_bot(A):
            self.assertEqual(qqpool.join_set(A, "500001", "A组", self.lid[A]), "")
            self.assertEqual(handover.check_to_qq(A, "500001"), "这个群现在就归小号管")
            ok, why = handover.to_official(A, "OPENID1", "500001")
            self.assertTrue(ok, why)
            self.assertEqual(handover.owner(A, "500001"), "official")
            self.assertFalse(qqpool.in_pool("500001"))
            self.assertEqual(pool.is_pool_group(A, "OPENID1")["set_name"], "A组")
            self.assertEqual(pool.counts()["free"], 1)
            g = [x for s in qqpool.sets(A) for x in s["groups"]][0]
            self.assertTrue(g["moved"])
            self.assertTrue(g["official_id"])
            self.assertFalse(pool.set_in_pool(handover.alt_row(A, "500001")["id"], True)[0])   # 归官方的时候小号那行放不回池

            ok, why = handover.to_qq(A, "500001")
            self.assertTrue(ok, why)
            self.assertEqual(handover.owner(A, "500001"), "qq")
            self.assertTrue(qqpool.in_pool("500001"))
            self.assertTrue(pool.is_pool_group(A, "OPENID1")["moved"])
            self.assertEqual(pool.counts()["free"], 1)

            ok, why = handover.to_official(A, "OPENID1", "500001")                  # 后台「转到官方备用群」走的就是这个
            self.assertTrue(ok, why)
            self.assertEqual(handover.owner(A, "500001"), "official")

    def test_busy_cannot_move(self):
        with use_bot(A):
            qqpool.join_set(A, "500001", "A组", self.lid[A])
            with get_conn() as c:
                c.execute("UPDATE pool_groups SET state='occupied', room_id=7 WHERE group_openid='qq:500001'")
            self.assertIn("R7", handover.check_to_official(A, "OPENID1", "500001"))
            self.assertFalse(handover.to_official(A, "OPENID1", "500001")[0])
            self.assertEqual(handover.owner(A, "500001"), "qq")

    def test_bad_gid_and_mismatch(self):
        with use_bot(A):
            self.assertIn("5 到 12 位", handover.check_to_official(A, "OPENID1", "12"))
            self.assertTrue(handover.to_official(A, "OPENID1", "500001")[0])
            self.assertIn("对不上", handover.check_to_official(A, "OPENID1", "500002"))
            self.assertIn("没进过小号的群号组", handover.check_to_qq(A, "500001"))

    def test_cross_bot_official_group(self):
        with use_bot(A):
            self.assertTrue(handover.to_official(A, "OPENID1", "500001")[0])
            qqpool.update_link(self.lid[A], "小号", "ws://127.0.0.1:3001", None, False)      # 小号连接关了，官方备用群照样算
        with use_bot(B):
            self.assertIn("甲号", qqpool.join_set(B, "500001", "B组", self.lid[B]))
            self.assertIn("甲号", handover.check_to_official(B, "OPENID9", "500001"))
            self.assertEqual(qqpool.held_by_other_bots(), {"500001": "甲号"})

    def test_moved_alt_group_does_not_block_other_bot(self):
        with use_bot(A):
            qqpool.join_set(A, "500001", "A组", self.lid[A])
            handover.to_official(A, "OPENID1", "500001")
            with get_conn() as c:
                c.execute("UPDATE pool_groups SET in_pool=0 WHERE group_openid='OPENID1'")   # 官方那边也「取消备用群池」了
        with use_bot(B):
            self.assertEqual(qqpool.held_by_other_bots(), {})


    def test_admin_buttons(self):
        import re
        from changri.admin import create_app
        from changri.core import webusers
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)   # noqa: E731
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        with use_bot(A):
            qqpool.join_set(A, "500001", "A组", self.lid[A])
            handover.to_official(A, "OPENID1", "500001")
        h = c.get(f"/b/{A}/qqlink").get_data(as_text=True)
        self.assertIn("归官方机器人", h)
        self.assertIn("转回小号群", h)
        self.assertIn("转回小号群", c.get(f"/b/{A}/backup-pool").get_data(as_text=True))
        r = c.post(f"/b/{A}/qqlink/handover/500001/to-qq", data={"_csrf": tok(f"/b/{A}/qqlink")}, follow_redirects=True)
        self.assertIn("小号现在不在群 500001 里", r.get_data(as_text=True))      # 小号没连着：不转
        with use_bot(A):
            self.assertEqual(handover.owner(A, "500001"), "official")
            handover.to_qq(A, "500001")
        h = c.get(f"/b/{A}/qqlink").get_data(as_text=True)
        self.assertIn("转到官方备用群", h)
        r = c.post(f"/b/{A}/qqlink/handover/500001/to-official", data={"_csrf": tok(f"/b/{A}/qqlink")}, follow_redirects=True)
        self.assertIn("已转给官方机器人", r.get_data(as_text=True))
        self.assertIn("请群主马上把群名改成", r.get_data(as_text=True))          # 小号没连着改不了名
        with use_bot(A):
            self.assertEqual(handover.owner(A, "500001"), "official")


if __name__ == "__main__":
    unittest.main()
