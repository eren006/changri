import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("CHANGRI_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

from changri.core import roles, rooms, sighting
from changri.core.db import get_conn, init_db, set_config


class SightingTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("sightings", "time_locks", "room_members", "rooms", "messages", "seasons", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for n in "甲乙丙丁":
            roles.create_role(n, "B1", "P" + n, "M" + n)
        with get_conn() as c:
            self.a, self.b, self.c, self.d = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        from changri.core import pool                          # 备用群池没有空群时房间开不成（不排队），先放几个群
        with get_conn() as c:
            c.execute("DELETE FROM pool_groups")
        for i in range(6):
            pool.register("B1", f"POOL{i}", real_gid=f"99000{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)
        set_config("game_day", "1")
        set_config("sighting_chance", "100")
        for k, v in (("ext:sighting", "0"), ("sighting_seen_mode", "随机"), ("sighting_notify_seen", "1"), ("sighting_overlap_pct", "30"), ("sighting_daily_limit", "5"), ("sighting_include_ended", "0")):
            set_config(k, v)

    def tearDown(self):
        with get_conn() as c:
            c.execute("DELETE FROM seasons")
        set_config("game_day", "")

    def _two_rooms(self, place2="花园", t2="1430-1530"):
        self.assertTrue(rooms.create("私约", self.a, [self.b], "1400-1500", "花园")[0])
        return rooms.create("私约", self.c, [self.d], t2, place2)

    def test_off_by_default(self):
        self._two_rooms()
        self.assertEqual(sighting.stats()["n"], 0)

    def test_both_sides_see_each_other(self):
        set_config("ext:sighting", "1")
        set_config("sighting_seen_mode", "点名")
        self._two_rooms()
        st = sighting.stats()
        self.assertEqual(st["saw"], 4)                 # 四个人各看见对方那一桌
        self.assertEqual(st["seen"], 4)
        txt = {r["role_name"]: r["text"] for r in sighting.recent() if r["kind"] == "saw"}
        self.assertIn("丙、丁", txt["甲"])
        seen = [r["text"] for r in sighting.recent() if r["kind"] == "seen"]
        self.assertTrue(all("有人" not in t for t in seen))

    def test_anonymous_seen(self):
        set_config("ext:sighting", "1")
        set_config("sighting_seen_mode", "匿名")
        self._two_rooms()
        seen = [r for r in sighting.recent() if r["kind"] == "seen"]
        self.assertTrue(seen and all(r["anon"] == 1 and "有人" in r["text"] for r in seen))

    def test_not_seen_notice_when_disabled(self):
        set_config("ext:sighting", "1")
        set_config("sighting_notify_seen", "0")
        self._two_rooms()
        self.assertEqual(sighting.stats()["seen"], 0)
        self.assertEqual(sighting.stats()["saw"], 4)

    def test_filters(self):
        set_config("ext:sighting", "1")
        self._two_rooms(place2="教室")                 # 地点不同
        self.assertEqual(sighting.stats()["n"], 0)

    def test_overlap_threshold_and_daily_cap(self):
        set_config("ext:sighting", "1")
        set_config("sighting_overlap_pct", "80")
        self.assertTrue(rooms.create("私约", self.a, [self.b], "1400-1500", "花园")[0])
        rooms.create("私约", self.c, [self.d], "1430-1530", "花园")        # 只重叠 50%
        self.assertEqual(sighting.stats()["n"], 0)
        set_config("sighting_overlap_pct", "30")
        set_config("sighting_daily_limit", "1")
        rooms.create("官约", self.c, [self.d], "1000-1100", "花园", official=True)
        rooms.create("官约", self.a, [self.b], "1010-1100", "花园", official=True)
        self.assertLessEqual(sum(1 for r in sighting.recent() if r["kind"] == "saw" and r["role_name"] == "甲"), 1)

    def test_push_pending(self):
        set_config("ext:sighting", "1")
        self._two_rooms()
        sent = []

        async def send(bot, gid, text):
            sent.append(text)
            return "m"
        n = asyncio.run(sighting.push_pending(send, 50))
        self.assertGreaterEqual(n + sighting.stats()["failed"], 1)
        self.assertEqual(sighting.stats()["pending"], 0)


if __name__ == "__main__":
    unittest.main()
