import asyncio
import os
import tempfile
import time
import unittest

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from changri.core import roles, season, seasonreport
from changri.core.db import get_conn, init_db, set_config


class SeasonReportTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("room_members", "rooms", "lovemails", "messages", "seasons", "role_groups", "roles"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B1", "P0", "M0")
        roles.create_role("乙", "B1", "P1", "M1")
        roles.create_role("丙", "B1", "P2", "M2")
        with get_conn() as c:
            self.a, self.b, self.c = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            now = int(time.time())
            for frm, to, kind, st in ((self.a, self.b, "sms", "sent"), (self.a, self.b, "sms", "sent"), (self.b, self.a, "sms", "sent"),
                                      (self.a, self.c, "gift", "sent"), (self.a, self.c, "sms", "failed")):
                c.execute("INSERT INTO messages(kind,from_role_id,to_role_id,created_ts,status) VALUES(?,?,?,?,?)", (kind, frm, to, now, st))
            c.execute("INSERT INTO lovemails(from_role_id,to_role_id,content,day,created_ts,status) VALUES(?,?, 'x','D1',?,'sent')", (self.b, self.a, now))
            c.execute("INSERT INTO rooms(id,kind,status,created_ts) VALUES(1,'约会','ended',?)", (now,))
            c.executemany("INSERT INTO room_members(room_id,role_id) VALUES(1,?)", [(self.a,), (self.b,)])

    def test_build(self):
        t = seasonreport.build(self.a)
        self.assertIn("约会：1 场", t)
        self.assertIn("短信：发出 2 条，收到 1 条", t)          # 发给丙的那条失败了不算
        self.assertIn("礼物：送出 1 份，收到 0 份", t)
        self.assertIn("心动信：写了 0 封，收到 1 封", t)
        self.assertIn("乙（4）", t)                              # 2 发 + 1 收 + 1 场
        self.assertNotIn("甲（", t)

    def test_nothing_no_report(self):
        with get_conn() as c:
            for t in ("room_members", "rooms", "lovemails", "messages"):
                c.execute(f"DELETE FROM {t}")
        self.assertEqual(seasonreport.build(self.a), "")

    def test_end_sends_only_when_enabled(self):
        sent = []

        async def fake_send(*a, **k):
            sent.append(a)
        from changri.core import messaging
        orig = messaging.push_to_role

        async def fake_push(role_id, text, send, at=True):
            sent.append((role_id, text))
            return True, ""
        messaging.push_to_role = seasonreport.push_to_role = fake_push
        try:
            season.book("S", "2000-01-01", "2099-01-01")
            with get_conn() as c:
                c.execute("UPDATE seasons SET status='running'")
            set_config("season_report", "0")
            set_config("season_save_reminder", "0")                   # 收尾前存档提醒另有开关，这里只看季末报告
            ok, msg = asyncio.run(season.end_and_report(fake_send))
            self.assertTrue(ok)
            self.assertEqual(sent, [])
            with get_conn() as c:
                c.execute("UPDATE seasons SET status='running'")
            set_config("season_report", "1")
            ok, msg = asyncio.run(season.end_and_report(fake_send))
            self.assertIn("已发给", msg)
            self.assertEqual({s[0] for s in sent}, {self.a, self.b, self.c})
        finally:
            messaging.push_to_role = seasonreport.push_to_role = orig


if __name__ == "__main__":
    unittest.main()
