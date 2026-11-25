"""申请加入的三道闸（官约不能申请、上帝模式关了约会、同意时重查拉黑），以及改名后旧名字别人拿不走。"""
import os
import tempfile
import unittest

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from changri.core import blocks, charprofile, godmode, npc, pool, roles, rooms
from changri.core.db import get_conn, init_db, set_config


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("join_requests", "room_members", "rooms", "role_perms", "blocks", "role_former_names",
                      "role_profiles", "role_groups", "seasons", "roles", "config", "pool_groups"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        for n in "甲乙丙丁":
            ok, msg = roles.create_role(n, "B1", "P" + n, "M" + n)
            assert ok, msg
        self.id = {n: roles.get_role_by_name(n)["id"] for n in "甲乙丙丁"}
        for i in range(4):
            pool.register("B1", f"POOL{i}", real_gid=f"99000{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)
        set_config("game_day", "3")
        set_config("ext:joinreq", "1")


class JoinGuardTest(Base):
    def _date(self):
        ok, info, rid = rooms.create("约会", self.id["甲"], [self.id["乙"]], "1400-1500", "咖啡厅")
        self.assertTrue(ok, info)
        return rid

    def test_official_not_joinable(self):
        ok, info, rid = rooms.create("官约", self.id["甲"], [self.id["乙"]], "1600-1700", "礼堂", official=True)
        self.assertTrue(ok, info)
        set_config("join_kinds", "约会,官约,心愿")                    # 老设置里填了官约也不算
        self.assertNotIn("官约", rooms.joinable_kinds())
        self.assertEqual(rooms.find_joinable(self.id["甲"]), [])
        ok, why, _ = rooms.request_join(rid, self.id["丙"])
        self.assertFalse(ok); self.assertIn("管理员安排", why)
        ok, why = rooms.join(rid, self.id["丙"])
        self.assertFalse(ok)

    def test_deferred_official_not_joinable(self):
        ok, info, rid = rooms.create("官约", self.id["甲"], [self.id["乙"]], "1600-1700", "礼堂", official=True, day=6, defer=True)
        self.assertTrue(ok, info)
        ok, why, _ = rooms.request_join(rid, self.id["丙"])
        self.assertFalse(ok)
        self.assertEqual(len(rooms.members(rid)), 2)

    def test_godmode_blocks_join(self):
        rid = self._date()
        godmode.set_perm(self.id["丙"], "约会", False)
        ok, why, _ = rooms.request_join(rid, self.id["丙"])
        self.assertFalse(ok); self.assertIn("暂时关闭", why)
        # 申请以后才被关：同意时拦下
        godmode.set_perm(self.id["丙"], "约会", True)
        ok, why, qid = rooms.request_join(rid, self.id["丙"])
        self.assertTrue(ok, why)
        godmode.set_perm(self.id["丙"], "约会", False)
        ok, why, _ = rooms.answer_request(qid, self.id["乙"], True)
        self.assertFalse(ok); self.assertIn("申请人", why)
        self.assertEqual(len(rooms.members(rid)), 2)

    def test_block_after_request_rechecked_on_accept(self):
        rid = self._date()
        ok, why, qid = rooms.request_join(rid, self.id["丁"])
        self.assertTrue(ok, why)
        blocks.block(self.id["甲"], self.id["丁"], silent=True)       # 甲拉黑丁，乙去同意
        ok, why, _ = rooms.answer_request(qid, self.id["乙"], True)
        self.assertFalse(ok)
        self.assertNotIn("甲", why)                                    # 不告诉乙是谁拉黑的
        self.assertEqual(len(rooms.members(rid)), 2)


class FormerNameTest(Base):
    def test_rename_frees_nothing_for_others(self):
        b = roles.get_role(self.id["乙"])
        self.assertIn("✅", charprofile.rename(b, "乙二", admin=True))
        # 别人不能抢「乙」当名字或简称
        self.assertIn("以前用过", charprofile.rename(roles.get_role(self.id["甲"]), "乙", admin=True))
        self.assertIn("以前用过", charprofile.set_short(roles.get_role(self.id["丙"]), "乙"))
        self.assertFalse(roles.create_role("乙", "B1", "P新", "M新")[0])
        self.assertFalse(npc.create("乙")[0])
        # 写旧名字照样找到原来的人
        self.assertEqual(roles.get_role_by_name("乙")["id"], self.id["乙"])
        self.assertEqual(roles.get_role_by_name("乙二")["id"], self.id["乙"])
        # 自己可以改回来
        self.assertIn("✅", charprofile.rename(roles.get_role(self.id["乙"]), "乙", admin=True))
        self.assertEqual(roles.get_role_by_name("乙")["id"], self.id["乙"])

    def test_old_short_kept(self):
        c = roles.get_role(self.id["丙"])
        self.assertIn("✅", charprofile.set_short(c, "小丙"))
        self.assertIn("✅", charprofile.set_short(c, "阿丙"))
        self.assertIn("以前用过", charprofile.set_short(roles.get_role(self.id["丁"]), "小丙"))
        self.assertEqual(roles.get_role_by_name("小丙")["id"], self.id["丙"])
        self.assertEqual(roles.get_role_by_name("阿丙")["id"], self.id["丙"])

    def test_deleted_role_releases_old_names(self):
        charprofile.rename(roles.get_role(self.id["丁"]), "丁二", admin=True)
        with get_conn() as c:
            c.execute("DELETE FROM roles WHERE id=?", (self.id["丁"],))
        self.assertIsNone(roles.get_role_by_name("丁"))
        self.assertTrue(roles.create_role("丁", "B1", "P新", "M新")[0])


if __name__ == "__main__":
    unittest.main()
