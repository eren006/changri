import os
import tempfile
import time
import unittest

os.environ.setdefault("CHANGRI_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

from changri.core import godmode, relations, roles, rooms, season
from changri.core.db import get_conn, init_db, set_config


class GodModeTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("votes", "replies", "posts", "time_locks", "role_perms", "count_resets", "rel_details", "relations", "room_members", "rooms", "messages", "seasons", "roles"):
                c.execute(f"DELETE FROM {t}")
        for n in "甲乙丙":
            roles.create_role(n, "B1", "P" + n, "M" + n)
        with get_conn() as c:
            self.a, self.b, self.c = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        set_config("game_day", "1")

    def tearDown(self):
        with get_conn() as c:
            c.execute("DELETE FROM seasons")
        set_config("game_day", "")

    def test_perm_blocks_creator_and_invitee(self):
        godmode.set_perm(self.a, "私约", False)
        ok, info, _ = rooms.create("私约", self.a, [self.b], "1400-1500", "花园")
        self.assertFalse(ok)
        self.assertIn("私约", info)
        ok, info, _ = rooms.create("私约", self.b, [self.a], "1400-1500", "花园")        # 不能被邀请
        self.assertFalse(ok)
        self.assertIn("「甲」", info)
        ok, _, _ = rooms.create("电话", self.b, [self.a], "1400-1500")                  # 别的功能照常
        self.assertTrue(ok)
        godmode.set_perm(self.a, "私约", True)
        self.assertEqual(godmode.check(self.a, "私约"), "")

    def test_time_lock(self):
        ok, _ = godmode.add_lock(self.a, 1, "1400-1500", "录像")
        self.assertTrue(ok)
        self.assertFalse(godmode.add_lock(self.a, 1, "乱写")[0])
        ok, info, _ = rooms.create("私约", self.b, [self.a], "1430-1530", "花园")
        self.assertFalse(ok)
        self.assertIn("管理员锁定", info)
        self.assertTrue(rooms.create("私约", self.b, [self.a], "1600-1700", "花园")[0])
        godmode.remove_lock(godmode.locks()[0]["id"])
        self.assertTrue(rooms.create("私约", self.c, [self.a], "1430-1530", "花园")[0])

    def test_reset_today_counts(self):
        from changri.core import messaging
        for _ in range(3):
            messaging.queue_message("sms", self.a, self.b, "", "hi")
        self.assertEqual(messaging.day_count(self.a, "sms"), 3)
        self.assertEqual(godmode.reset_today(self.a, ["sms"]), 1)
        self.assertEqual(messaging.day_count(self.a, "sms"), 0)
        messaging.queue_message("sms", self.a, self.b, "", "hi")
        self.assertEqual(messaging.day_count(self.a, "sms"), 1)        # 清零之后重新计
        self.assertEqual(godmode.today_counts(self.a)["sms"], 1)

    def test_global_off_and_global_lock(self):
        godmode.set_global("私约", False)
        ok, info, _ = rooms.create("私约", self.a, [self.b], "1400-1500", "花园")
        self.assertFalse(ok)
        self.assertIn("所有人", info)
        godmode.set_global("私约", True)
        self.assertTrue(godmode.add_global_lock(None, "1400-1500", "停电")[0])
        ok, info, _ = rooms.create("私约", self.a, [self.b], "1430-1530", "花园")
        self.assertFalse(ok)
        self.assertIn("管理员锁定", info)
        godmode.remove_global_lock(godmode.global_locks()[0]["id"])
        self.assertTrue(rooms.create("私约", self.a, [self.b], "1430-1530", "花园")[0])

    def test_forum_blocked_by_global_even_if_personal_on(self):
        from changri.core import forum
        pid = forum.new_post(self.a, "路人", "hi")
        self.assertTrue(forum.new_reply(pid, self.b, "乙", "回")[0])
        godmode.set_global("forum", False)
        godmode.set_perm(self.b, "forum", True)                      # 个人开着也没用
        ok, why, _ = forum.new_reply(pid, self.b, "乙", "再回")
        self.assertFalse(ok)
        self.assertIn("所有人", why)
        self.assertFalse(forum.vote(f"p{pid}", self.b, 1)[0])
        godmode.set_global("forum", True)
        self.assertTrue(forum.vote(f"p{pid}", self.b, 1)[0])
        godmode.set_perm(self.b, "forum", False)                     # 全体开着，个人关了也不行
        self.assertFalse(forum.new_reply(pid, self.b, "乙", "x")[0])

    def test_clear_rels(self):
        from changri.core.db import get_conn as gc
        with gc() as c:
            lo, hi = sorted((self.a, self.b))
            rid = c.execute("INSERT INTO relations(role_lo,role_hi,initiator_id,confirmed,created_ts) VALUES(?,?,?,1,?)", (lo, hi, self.a, int(time.time()))).lastrowid
            c.execute("INSERT INTO rel_details(rel_id,from_role_id,text,created_ts) VALUES(?,?,?,?)", (rid, self.a, "x", int(time.time())))
        self.assertEqual(godmode.clear_rels(self.a), 1)
        self.assertIsNone(relations.get_rel(self.a, self.b))


if __name__ == "__main__":
    unittest.main()
