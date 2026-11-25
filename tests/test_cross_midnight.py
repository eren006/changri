import os
import tempfile
import unittest

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from changri.core import godmode, roles, rooms, sighting
from changri.core.db import get_conn, init_db, set_config
from changri.core.textutil import hm_text


class ParseTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(rooms.parse_hm("2300-0100"), ("23:00", "01:00"))
        self.assertEqual(rooms.parse_hm("1400-1500"), ("14:00", "15:00"))
        self.assertIsNone(rooms.parse_hm("1400-1400"))      # 一样的还是不行
        self.assertIsNone(rooms.parse_hm("2500-0100"))

    def test_parse_ime_forms(self):
        """手机输入法打出来的全角数字、全角冒号、各种横线 / 波浪线都认。"""
        for t in ("１４００－１５００", "14：00～15：00", "1400—1500", "1400–1500", "1400﹣1500", "1400 ~ 1500", "14:00 到 15:00"):
            self.assertEqual(rooms.parse_hm(t), ("14:00", "15:00"), t)

    def test_join_time_range(self):
        from changri.core.textutil import join_time_range
        self.assertEqual(join_time_range("1400 - 1500 咖啡厅 张三").split(), ["1400-1500", "咖啡厅", "张三"])
        self.assertEqual(join_time_range("14：00～15：00 咖啡厅 张三").split(), ["14:00-15:00", "咖啡厅", "张三"])
        self.assertEqual(join_time_range("D2 1400 到 1500 后山 甲/乙").split(), ["D2", "1400-1500", "后山", "甲/乙"])
        self.assertEqual(join_time_range("1400-1500 1-2教室 Ａ"), "1400-1500 1-2教室 Ａ")      # 别的字不动
        self.assertEqual(join_time_range("3月5日 咖啡厅 张三"), "3月5日 咖啡厅 张三")
        self.assertEqual(join_time_range("R12 D1 1400 - 1500"), "R12 D1 1400-1500")
        # 只并开头的时间段：故事历的日期后面、心愿内容里的数字原样不动
        self.assertEqual(join_time_range("3月5日 花园 1000 - 1100"), "3月5日 花园 1000 - 1100")
        self.assertEqual(join_time_range("3月5日 想 2000 - 2100 看星星"), "3月5日 想 2000 - 2100 看星星")
        self.assertEqual(join_time_range("1400-1500 2023 - 2024 甲"), "1400-1500 2023 - 2024 甲")

    def test_span_and_text(self):
        self.assertEqual(rooms.span(3, "23:00", "01:00"), (3 * 1440 + 1380, 4 * 1440 + 60))
        self.assertTrue(rooms.spans_overlap(3, "23:00", "01:00", 4, "00:30", "01:30"))
        self.assertFalse(rooms.spans_overlap(3, "23:00", "01:00", 4, "01:00", "02:00"))
        self.assertFalse(rooms.spans_overlap(3, "23:00", "01:00", 3, "00:30", "01:30"))   # 同一天凌晨在它前面
        self.assertEqual(hm_text("23:00", "01:00"), "23:00-次日01:00")
        self.assertEqual(hm_text("14:00", "15:00"), "14:00-15:00")

    def test_template_global(self):
        from changri.admin import create_app
        from changri.phone.app import create_phone_app
        for app in (create_app(), create_phone_app("t")):
            t = app.jinja_env.from_string("{{ a }}–{{ next_day(a, b) }}{{ b }}")
            self.assertEqual(t.render(a="23:00", b="01:00"), "23:00–次日01:00")
            self.assertEqual(t.render(a="14:00", b="15:00"), "14:00–15:00")

    def test_too_short(self):
        self.assertEqual(rooms.too_short("约会", "23:30", "00:30"), "")
        self.assertIn("至少", rooms.too_short("约会", "23:30", "00:10"))

    def test_sighting_ratio(self):
        self.assertAlmostEqual(sighting.overlap_ratio("23:00", "01:00", "00:00", "01:00", 3, 4), 1.0)
        self.assertEqual(sighting.overlap_ratio("23:00", "01:00", "00:00", "01:00", 3, 3), 0.0)
        self.assertAlmostEqual(sighting.overlap_ratio("14:00", "16:00", "15:00", "17:00"), 0.5)


class DbTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("time_locks", "wishes", "room_members", "rooms", "seasons", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM pool_groups")
        for n in "甲乙丙":
            roles.create_role(n, "B1", "P" + n, "M" + n)
        with get_conn() as c:
            self.a, self.b, self.c = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        from changri.core import pool
        for i in range(6):
            pool.register("B1", f"POOL{i}", real_gid=f"99000{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)
        set_config("game_day", "3")

    def tearDown(self):
        set_config("game_day", "")

    def test_create_cross_midnight_and_conflict_next_day(self):
        ok, info, rid = rooms.create("约会", self.a, [self.b], "2300-0100", "花园")
        self.assertTrue(ok, info)
        with get_conn() as c:
            r = c.execute("SELECT day, start_hm, end_hm FROM rooms WHERE id=?", (rid,)).fetchone()
        self.assertEqual((r["day"], r["start_hm"], r["end_hm"]), (3, "23:00", "01:00"))
        hit = rooms.conflict(self.b, 4, "00:30", "01:30")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], rid)
        self.assertIsNone(rooms.conflict(self.b, 4, "01:00", "02:00"))
        self.assertIsNone(rooms.conflict(self.b, 3, "00:30", "01:30"))
        # 反过来：第二天凌晨已经有约，前一天晚上的跨午夜约会撞上
        self.assertIsNone(rooms.conflict(self.c, 3, "23:00", "01:00"))
        with get_conn() as c:
            c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, created_ts) VALUES('约会',?,4,'00:15','01:15','湖边',0)", (self.c,))
            nid = c.execute("SELECT MAX(id) FROM rooms").fetchone()[0]
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (nid, self.c))
        self.assertEqual(rooms.conflict(self.c, 3, "23:00", "01:00")["id"], nid)

    def test_clash_message_names_the_day_of_the_other_room(self):
        """撞上的是前一天 / 后一天那场时，提示写那场自己的天，不是要约的那天。"""
        ok, info, rid = rooms.create("约会", self.a, [self.b], "2300-0100", "花园")      # D3 23:00-次日01:00
        self.assertTrue(ok, info)
        ok, msg, _ = rooms.create("约会", self.a, [self.c], "0030-0130", "湖边", day=4)
        self.assertFalse(ok)
        self.assertIn("D3 23:00-次日01:00", msg)
        self.assertNotIn("D4 23:00", msg)
        # 反过来：D4 凌晨有约，在 D3 晚上约跨午夜
        with get_conn() as c:
            c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, created_ts) VALUES('约会',?,4,'00:15','01:15','湖边',0)", (self.c,))
            nid = c.execute("SELECT MAX(id) FROM rooms").fetchone()[0]
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (nid, self.c))
        hit = rooms.conflict(self.c, 3, "23:00", "01:00")
        self.assertEqual(rooms.clash_when(hit, 3), "D4 00:15-01:15")
        # 管理员锁定（每天都锁）撞在第二天凌晨：写第二天
        godmode.add_lock(self.b, None, "0100-0200")
        hit = rooms.conflict(self.b, 5, "23:30", "01:30")
        self.assertEqual(rooms.clash_when(hit, 5), "D6 01:00-02:00")

    def test_lock_cross_midnight(self):
        godmode.add_lock(self.a, 3, "2330-0030")
        self.assertIsNotNone(rooms.conflict(self.a, 4, "00:00", "01:00"))
        self.assertIsNone(rooms.conflict(self.a, 4, "00:30", "01:30"))
        godmode.add_lock(self.b, None, "0100-0200")            # 每天都锁
        self.assertIsNotNone(rooms.conflict(self.b, 3, "23:30", "01:30"))

    def test_blocked_hours_next_day(self):
        rooms.save_blocked_hours({4: [0]})
        msg = rooms.blocked_hit(3, "23:00", "01:00")
        self.assertIn("D4 的 0点", msg)
        self.assertEqual(rooms.blocked_hit(3, "22:00", "23:00"), "")
        rooms.save_blocked_hours({3: [23], 4: [0]})
        self.assertIn("D3 的 23点，D4 的 0点", rooms.blocked_hit(3, "23:00", "01:00"))



    def test_taken_wish_cross_midnight_still_conflicts(self):
        """跨午夜的心愿被摘后，房间存的是 01:00 而不是显示用的「次日01:00」，后面再约同一时段要撞上。"""
        from unittest import mock

        from changri.core import season, wishes
        with mock.patch.object(season, "phase", lambda: "playing"):
            ok, wid = wishes.post(self.a, "2300-0100", "花园", "夜游")
            self.assertTrue(ok, wid)
            ok, info, rid = wishes.take(int(wid), self.b)
            self.assertTrue(ok, info)
            with get_conn() as c:
                r = c.execute("SELECT start_hm, end_hm FROM rooms WHERE id=?", (rid,)).fetchone()
            self.assertEqual((r["start_hm"], r["end_hm"]), ("23:00", "01:00"))
            ok, info, _ = rooms.create("约会", self.b, [self.c], "2330-0030", "湖边")
            self.assertFalse(ok)

    def test_migration_fixes_stored_next_day(self):
        from pathlib import Path

        from changri.storage import migrate
        sql = (Path(migrate.MIGRATIONS_DIR) / "0031_wish_room_next_day.sql").read_text(encoding="utf-8")
        with get_conn() as c:
            c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, created_ts) VALUES('心愿',?,3,'23:00','次日01:00','花园',0)", (self.a,))
            rid = c.execute("SELECT MAX(id) FROM rooms").fetchone()[0]
            c.executescript(sql)
            r = c.execute("SELECT start_hm, end_hm FROM rooms WHERE id=?", (rid,)).fetchone()
        self.assertEqual((r["start_hm"], r["end_hm"]), ("23:00", "01:00"))

    def test_vote_change_ignored_after_room_closed(self):
        ok, info, rid = rooms.create("约会", self.a, [self.b], "1400-1500", "花园")
        self.assertTrue(ok, info)
        self.assertTrue(rooms.propose_change(rid, self.a, 3, "16:00", "17:00")[0])
        with get_conn() as c:                                   # 绕过 rooms.end 直接关掉（申请还留着）
            c.execute("UPDATE rooms SET status='ended' WHERE id=?", (rid,))
        self.assertEqual(rooms.vote_change(rid, self.b, True), ("none", None))
        with get_conn() as c:
            r = c.execute("SELECT start_hm FROM rooms WHERE id=?", (rid,)).fetchone()
        self.assertEqual(r["start_hm"], "14:00")


if __name__ == "__main__":
    unittest.main()
