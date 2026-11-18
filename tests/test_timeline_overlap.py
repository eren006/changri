"""时间线不能重叠：一个时间段只能有一件事——进行中和已结束的约会、还没被摘的心愿都占时间（只有取消的不占）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = tempfile.mkdtemp()
os.environ["CHANGRI_DB_PATH"] = str(Path(_tmp) / "t.db")

from changri.core import pool, roles, rooms, season, storycal, wishes  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


class OverlapTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("wishes", "room_members", "rooms", "roles"):
                c.execute(f"DELETE FROM {t}")
        for p in (mock.patch.object(season, "phase", lambda: "playing"), mock.patch.object(season, "current_day", lambda: 1),
                  mock.patch.object(pool, "allocate", lambda rid: True)):
            p.start()
            self.addCleanup(p.stop)
        for n in "甲乙丙丁":
            roles.create_role(n, "B1", "G" + n, "M" + n)
        self.a, self.b, self.c, self.d = (roles.get_role_by_name(n)["id"] for n in "甲乙丙丁")

    def test_ended_room_still_occupies(self):
        ok, _, rid = rooms.create("约会", self.b, [self.d], "0800-0900", "花园")
        self.assertTrue(ok)
        rooms.end(rid)
        ok, info, _ = rooms.create("约会", self.b, [self.c], "0830-0930", "湖边")
        self.assertFalse(ok); self.assertIn("撞了", info)
        ok, info, _ = rooms.create("电话", self.c, [self.d], "0800-0900")
        self.assertFalse(ok); self.assertIn("「丁」", info)

    def test_cancelled_room_frees_slot(self):
        ok, _, rid = rooms.create("约会", self.b, [self.d], "0800-0900", "花园")
        rooms.end(rid, "cancelled")
        self.assertTrue(rooms.create("约会", self.b, [self.d], "0800-0900", "花园")[0])

    def test_ended_room_blocks_reschedule_and_join(self):
        _, _, r1 = rooms.create("约会", self.a, [self.b], "1000-1100", "花园")
        rooms.end(r1)
        _, _, r2 = rooms.create("约会", self.a, [self.c], "1200-1300", "花园")
        self.assertFalse(rooms.propose_change(r2, self.a, 1, "10:30", "11:30")[0])
        _, _, r3 = rooms.create("约会", self.c, [self.d], "1000-1100", "湖边")
        self.assertFalse(rooms.request_join(r3, self.b)[0])

    def test_open_wish_occupies_poster(self):
        ok, wid = wishes.post(self.a, "1400-1500", "花园", "散步")
        self.assertTrue(ok)
        self.assertFalse(rooms.create("约会", self.a, [self.b], "1430-1530", "咖啡厅")[0])     # 自己约
        self.assertFalse(rooms.create("约会", self.b, [self.a], "1400-1500", "咖啡厅")[0])     # 被别人约
        self.assertFalse(wishes.post(self.a, "1430-1530", "湖边", "看书")[0])                 # 再挂一条重叠的
        ok, info, rid = wishes.take(int(wid), self.c)                                       # 摘的时候不算这条心愿自己
        self.assertTrue(ok, info)
        self.assertFalse(rooms.create("约会", self.a, [self.b], "1400-1500", "咖啡厅")[0])     # 摘走后房间接着占

    def test_withdrawn_wish_frees_slot(self):
        _, wid = wishes.post(self.a, "1400-1500", "花园", "散步")
        wishes.withdraw(int(wid), self.a)
        self.assertTrue(rooms.create("约会", self.a, [self.b], "1400-1500", "咖啡厅")[0])

    def test_taker_with_own_wish_cannot_take(self):
        _, mine = wishes.post(self.c, "2000-2100", "花园", "a")
        _, theirs = wishes.post(self.d, "2000-2100", "湖边", "b")
        ok, info, _ = wishes.take(int(theirs), self.c)
        self.assertFalse(ok); self.assertIn("你在", info)
        self.assertEqual(wishes.get(int(mine))["status"], "open")

    def test_take_wish_under_story_calendar(self):
        with mock.patch.object(storycal, "enabled", lambda: True), mock.patch.object(storycal, "day_list", lambda d: [(3, 5), (3, 6)]), \
                mock.patch.object(storycal, "parse_span", lambda t: ((3, 5), (3, 5)) if t == "3月5日" else "x"):
            ok, wid = wishes.post(self.a, "3月5日", "花园", "散步")
            self.assertTrue(ok, wid)
            ok, info, _ = wishes.take(int(wid), self.b)
            self.assertTrue(ok, info)

    def test_old_wishes_not_listed_or_counted(self):
        with get_conn() as c:
            c.execute("INSERT INTO wishes(poster_id, day, start_hm, end_hm, place, content, dkey, created_ts) VALUES(?,0,'10:00','11:00','x','y','',0)", (self.a,))
        self.assertEqual(wishes.list_open(), [])


if __name__ == "__main__":
    unittest.main()
