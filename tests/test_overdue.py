import asyncio
import os
import tempfile
import time
import unittest

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from changri.core import overdue, roles, rooms
from changri.core.db import get_conn, init_db, set_config


class OverdueTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("room_lines", "room_members", "rooms", "messages", "seasons", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for n in "甲乙丙":
            roles.create_role(n, "B1", "P" + n, "M" + n)
        with get_conn() as c:
            self.a, self.b, self.c = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        from changri.core import pool
        with get_conn() as c:
            c.execute("DELETE FROM pool_groups")
        for i in range(6):
            pool.register("B1", f"POOL{i}", real_gid=f"99000{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)
        set_config("game_day", "1")
        set_config("ext:overdue", "1")
        set_config("overdue_quiet", "")              # 别让半夜跑测试时被免打扰挡掉
        self.now = time.time()

    def tearDown(self):
        set_config("game_day", "")

    def _room(self, kind="约会", others=None, t="1400-1500", place="花园"):
        ok, info, rid = rooms.create(kind, self.a, others or [self.b], t, place if rooms.KINDS[kind] else "")
        self.assertTrue(ok, info)
        return rid

    def _line(self, room_id, role_id, ago_h):
        with get_conn() as c:
            c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,1,10)",
                      (room_id, int(self.now - ago_h * 3600), role_id, "x", "x"))

    def _age(self, room_id, ago_h):
        with get_conn() as c:
            c.execute("UPDATE rooms SET created_ts=? WHERE id=?", (int(self.now - ago_h * 3600), room_id))

    def test_off_by_default(self):
        set_config("ext:overdue", "0")
        self.assertFalse(overdue.enabled())

    def test_two_person_turn(self):
        r = self._room()
        self._line(r, self.a, 4)                     # 甲 4 小时前写完，轮到乙
        d = overdue.due(self.now)
        self.assertEqual([(x["room_id"], x["role_id"]) for x in d], [(r, self.b)])
        self.assertEqual(d[0]["others"], ["甲"])
        self._line(r, self.a, 3.5)                   # 甲接着写不重新计
        self.assertEqual(overdue.due(self.now)[0]["since"], int(self.now - 4 * 3600))

    def test_not_yet(self):
        r = self._room()
        self._line(r, self.a, 2)                     # 约会 3 小时才算
        self.assertEqual(overdue.due(self.now), [])

    def test_phone_one_hour(self):
        r = self._room("电话")
        self._line(r, self.a, 1.2)
        self.assertEqual([x["role_id"] for x in overdue.due(self.now)], [self.b])
        set_config("overdue_phone_hours", "0")       # 0 = 电话不提醒
        self.assertEqual(overdue.due(self.now), [])
        set_config("overdue_phone_hours", "2")
        self.assertEqual(overdue.due(self.now), [])

    def test_no_lines_waits_on_creator(self):
        r = self._room()
        self._age(r, 4)
        self.assertEqual([x["role_id"] for x in overdue.due(self.now)], [self.a])

    def test_npc_not_reminded(self):
        r = self._room()
        with get_conn() as c:
            c.execute("UPDATE roles SET is_npc=1 WHERE id=?", (self.b,))
        self._line(r, self.a, 4)
        self.assertEqual(overdue.due(self.now), [])

    def test_once_per_stretch_and_merged(self):
        r1 = self._room()
        r2 = self._room("约会", [self.c], t="1600-1700")
        self._line(r1, self.a, 5)
        self._line(r2, self.c, 4)                    # 甲在 r2 被等
        self._line(r1, self.b, 4.5)                  # 乙回了，轮到甲
        sent = []

        async def send(bot_id, gid, text, **kw):
            sent.append((gid, text))
            return "m"
        self.assertEqual(asyncio.run(overdue.push(send, self.now)), 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "P甲")
        self.assertIn("2 个房间", sent[0][1])
        self.assertIn(f"R{r1}", sent[0][1])
        self.assertIn(f"R{r2}", sent[0][1])
        self.assertEqual(asyncio.run(overdue.push(send, self.now)), 0)     # 同一段只提醒一次
        self._line(r1, self.a, 4)                    # 甲回了，乙又被等了 4 小时：新的一段
        self.assertEqual([x["role_id"] for x in overdue.due(self.now)], [self.b])

    def _at(self, hh, mm=0):
        t = time.localtime(self.now)
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, 0, 0, 0, -1))

    def test_quiet_hours(self):
        set_config("overdue_quiet", "0000-0800")
        self.assertTrue(overdue.in_quiet(self._at(0)))
        self.assertTrue(overdue.in_quiet(self._at(7, 59)))
        self.assertFalse(overdue.in_quiet(self._at(8)))
        self.assertFalse(overdue.in_quiet(self._at(23, 59)))
        set_config("overdue_quiet", "23:00-07:00")   # 跨午夜
        self.assertTrue(overdue.in_quiet(self._at(23, 30)))
        self.assertTrue(overdue.in_quiet(self._at(6)))
        self.assertFalse(overdue.in_quiet(self._at(12)))
        set_config("overdue_quiet", "乱写")
        self.assertFalse(overdue.in_quiet(self._at(3)))

    def test_quiet_defers_then_sends(self):
        set_config("overdue_quiet", "0000-0800")
        r = self._room()
        night, morning = self._at(3), self._at(8, 1)
        with get_conn() as c:
            c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,1,10)",
                      (r, int(night - 4 * 3600), self.a, "x", "x"))
        sent = []

        async def send(bot_id, gid, text, **kw):
            sent.append(text)
            return "m"
        self.assertEqual(asyncio.run(overdue.push(send, night)), 0)      # 半夜不发，也不记成发过
        self.assertEqual(asyncio.run(overdue.push(send, morning)), 1)    # 8 点后补发
        self.assertIn("没有回复", sent[0])
        self.assertIn(f"R{r} 约会（和 甲）的群", sent[0])

    def test_ended_room_cleared(self):
        r = self._room()
        self._line(r, self.a, 4)
        overdue.mark_sent(overdue.due(self.now))
        with get_conn() as c:
            c.execute("UPDATE rooms SET status='ended' WHERE id=?", (r,))
        overdue.mark_sent([])
        self.assertEqual(overdue._sent(), {})


if __name__ == "__main__":
    unittest.main()
