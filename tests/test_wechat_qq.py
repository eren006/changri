"""「微信走 QQ 群」开关：开着时微信从备用群池领群（不看聊天室开关），关着只在小手机；同一组人只能有一个没结束的微信。"""
import os
import tempfile
import unittest

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from changri.core import godmode, overview, pool, roles, rooms  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class WechatQQ(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("room_members", "rooms", "role_groups", "seasons", "roles", "config", "pool_groups"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        for n in "甲乙丙":
            ok, msg = roles.create_role(n, "B1", "P" + n, "M" + n)
            assert ok, msg
        self.id = {n: roles.get_role_by_name(n)["id"] for n in "甲乙丙"}
        set_config("game_day", "3")

    def _pool(self, n=2):
        for i in range(n):
            pool.register("B1", f"POOL{i}", real_gid=f"99000{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)

    def _wechat(self, *names):
        return rooms.create("微信", self.id["甲"], [self.id[n] for n in names], "")

    def test_off_stays_on_phone(self):
        self._pool()
        ok, info, rid = self._wechat("乙")
        self.assertTrue(ok, info)
        room = rooms.get(rid)
        self.assertIsNone(room["pool_id"])
        self.assertIn("小手机", overview.group_line(room))

    def test_on_takes_pool_group_even_with_chatroom(self):
        self._pool()
        set_config("ext:wechat", "1")
        set_config("ext:phone", "1")
        set_config("ext:chatroom", "1")             # 私约在小手机里聊，微信照样走 QQ 群
        self.assertTrue(rooms.web_mode())
        ok, info, rid = self._wechat("乙", "丙")
        self.assertTrue(ok, info)
        room = rooms.get(rid)
        self.assertIsNotNone(room["pool_id"])
        self.assertEqual(room["day"], None)          # 不占时间线
        self.assertIn("请申请入群：群号 9900", overview.group_line(room))
        ok, info, rid2 = rooms.create("约会", self.id["甲"], [self.id["乙"]], "1400-1500", "咖啡厅")
        self.assertTrue(ok, info)
        self.assertIsNone(rooms.get(rid2)["pool_id"])

    def test_on_without_free_group_fails(self):
        set_config("ext:wechat", "1")
        ok, info, _ = self._wechat("乙")
        self.assertFalse(ok)
        self.assertIn("备用群池", info)
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)

    def test_same_people_only_one_open(self):
        self._pool(3)
        set_config("ext:wechat", "1")
        ok, info, rid = self._wechat("乙")
        self.assertTrue(ok, info)
        ok, info, _ = rooms.create("微信", self.id["乙"], [self.id["甲"]], "")      # 反过来发也算同一组
        self.assertFalse(ok)
        self.assertIn(f"R{rid}", info)
        ok, info, _ = self._wechat("乙", "丙")                                   # 多一个人是另一组
        self.assertTrue(ok, info)
        rooms.end(rid)
        ok, info, _ = self._wechat("乙")                                         # 结束了可以重开
        self.assertTrue(ok, info)

    def test_godmode_label(self):
        self.assertNotIn("微信", godmode.shown_features())
        set_config("ext:wechat", "1")
        self.assertEqual(godmode.shown_features()["微信"], "微信（QQ 群）")

    def test_cap(self):
        self._pool(3)
        set_config("ext:wechat", "1")
        set_config("wechat_max_groups", "1")
        self.assertTrue(self._wechat("乙")[0])
        ok, info, _ = self._wechat("丙")
        self.assertFalse(ok)
        self.assertIn("开满了", info)
        ok, info, _ = rooms.create("约会", self.id["甲"], [self.id["丙"]], "1400-1500", "咖啡厅")   # 私约不受影响
        self.assertTrue(ok, info)

    def test_dedicated_set(self):
        self._pool(2)                                                   # A组 两个
        pool.register("B1", "WX0", real_gid="88000", set_name="微信组")
        pool.set_in_pool(pool.is_pool_group("B1", "WX0")["id"], True)
        set_config("ext:wechat", "1")
        set_config("wechat_pool_set", "微信组")
        ok, info, rid = self._wechat("乙")
        self.assertTrue(ok, info)
        self.assertEqual(pool.get(rooms.get(rid)["pool_id"])["set_name"], "微信组")
        ok, info, _ = self._wechat("丙")                                 # 微信组用完了：不去占 A组
        self.assertFalse(ok)
        self.assertIn("备用群池", info)
        for t in ("1000-1100", "1200-1300"):                            # 私约只用 A组，用完也不碰微信组
            ok, info, r = rooms.create("约会", self.id["乙"], [self.id["丙"]], t, "咖啡厅")
            self.assertTrue(ok, info)
            self.assertEqual(pool.get(rooms.get(r)["pool_id"])["set_name"], "A组")
        set_config("ext:wechat", "0")                                    # 扩展关了：专用组不再生效
        self.assertEqual(pool.wechat_set(), "")


if __name__ == "__main__":
    unittest.main()
