import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "relalert.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

from admin import create_app  # noqa: E402
from core import relations, webusers  # noqa: E402
from core.db import get_config, get_conn, init_db, set_config  # noqa: E402


def setup_data():
    init_db()
    with get_conn() as c:
        for t in ("rel_details", "relations", "roles", "config", "web_users"):
            c.execute(f"DELETE FROM {t}")
        a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','M1',1)").lastrowid
        b = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('乙','M2',1)").lastrowid
        d = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('丙','M3',1)").lastrowid
    return a, b, d


def detail(rel_id, frm, hours_ago, now):
    with get_conn() as c:
        c.execute("INSERT INTO rel_details(rel_id,from_role_id,text,created_ts) VALUES(?,?,?,?)", (rel_id, frm, "字", int(now - hours_ago * 3600)))


class Alert(unittest.TestCase):
    def test_waiting_and_overdue(self):
        a, b, d = setup_data()
        now = time.time()
        relations.add_detail(a, b, "乙", "第一段")
        with get_conn() as c:
            c.execute("DELETE FROM rel_details")
            rid = c.execute("SELECT id FROM relations").fetchone()["id"]
        detail(rid, a, 30, now)          # 甲 30 小时前发
        detail(rid, b, 20, now)          # 乙 20 小时前回（间隔 10 小时）
        detail(rid, a, 5, now)           # 甲 5 小时前又发，等乙回
        w = relations.waiting_lines(now)[0]
        self.assertEqual((w["waiting_on"], w["last_from"]), ("乙", "甲"))
        self.assertAlmostEqual(w["hours"], 5, delta=0.01)
        self.assertAlmostEqual(w["avg_reply_hours"], (10 + 15) / 2, delta=0.01)   # 乙隔 10 小时回，甲隔 15 小时回
        set_config("rel_alert_hours", "6")
        self.assertEqual(relations.overdue(now), [])                              # 才 5 小时
        set_config("rel_alert_hours", "4")
        self.assertEqual(len(relations.overdue(now)), 1)
        set_config("rel_alert_hours", "0")
        self.assertEqual(relations.overdue(now), [])                              # 0 = 不提醒
        set_config("rel_alert_hours", "4")
        relations.confirm(a, b)
        self.assertEqual(relations.overdue(now), [])                              # 确认完成的不算

    def test_default_and_admin_page(self):
        a, b, d = setup_data()
        self.assertEqual(relations.alert_hours(), 24.0)
        now = time.time()
        relations.add_detail(a, b, "乙", "x")
        with get_conn() as c:
            c.execute("UPDATE rel_details SET created_ts=?", (int(now - 50 * 3600),))
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        html = c.get("/relations").get_data(as_text=True)
        self.assertIn("1 条超时没回复", html)
        self.assertIn("乙 · 50 小时", html)
        self.assertIn("超过 24 小时没人回复", c.get("/").get_data(as_text=True))       # 总览待办
        c.post("/relations/alert", data={"_csrf": tok("/relations"), "hours": "100"})
        self.assertEqual(get_config("rel_alert_hours"), "100")
        self.assertNotIn("条超时没回复", c.get("/relations").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
