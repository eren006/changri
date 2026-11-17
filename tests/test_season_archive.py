"""历史季度：结束/收尾时存档案，清空后还能回看；改预订、改名、删历史。"""
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "sa.db")
os.environ["QQ_BOTS"] = "[]"
import changri.admin as admin_mod  # noqa: E402,F401

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import roles, season, seasonarchive, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


def token(client, path="/season"):
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', client.get(path).get_data(as_text=True)).group(1)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("season_archives", "seasons", "room_lines", "room_members", "rooms", "messages", "lovemails", "relations", "role_attrs",
                      "role_groups", "roles", "web_users", "config"):
                c.execute(f"DELETE FROM {t}")

    def play_one_season(self, name="甲季"):
        season.book(name, "2026-11-10", "2026-11-20")
        season.start(now="2026-11-12")
        roles.create_role("甲", "B", "G1", "M1")
        roles.create_role("乙", "B", "G2", "M2")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        now = int(time.time())
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, status, created_ts) VALUES('约会',?,1,'ended',?)", (a, now)).lastrowid
            for r in (a, b):
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, r))
            c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,1,5)", (rid, now, a, "甲", "甲：你好呀"))
            c.execute("INSERT INTO messages(kind, from_role_id, to_role_id, status, created_ts, day) VALUES('sms',?,?,'sent',?,'x')", (a, b, now))
        return a, b

    def test_end_snapshots_and_wipe_keeps_it(self):
        self.play_one_season()
        season.end()
        sid = season.latest_ended()["id"]
        arc = seasonarchive.get(sid)
        self.assertEqual(arc["summary"]["counts"]["roles"], 2)
        self.assertEqual(arc["summary"]["counts"]["sms"], 1)
        self.assertEqual(arc["rooms"][0]["lines"][0]["text"], "甲：你好呀")
        self.assertEqual(arc["rooms"][0]["members"], ["甲", "乙"])
        season.wipe()
        self.assertEqual(season.role_count(), 0)                                   # 数据清空了
        arc = seasonarchive.get(sid)                                               # 档案还在，且是收尾前的数据
        self.assertEqual(len(arc["rooms"]), 1)
        self.assertEqual([p["chars"] for p in arc["summary"]["players"] if p["name"] == "甲"], [5])

    def test_edit_booking(self):
        season.book("甲季", "2026-11-10", "2026-11-20")
        sid = season.booked()[0]["id"]
        self.assertTrue(season.update_booking(sid, "新名", "2026-11-11", "2026-11-21", "2026-11-23")[0])
        b = season.booked()[0]
        self.assertEqual((b["name"], b["start_date"], b["supp_end"]), ("新名", "2026-11-11", "2026-11-23"))
        self.assertFalse(season.update_booking(sid, "新名", "2026-11-22", "2026-11-21")[0])
        season.book("乙季", "2026-12-01", "2026-12-02")
        self.assertFalse(season.update_booking(sid, "乙季", "2026-11-11", "2026-11-21")[0])     # 重名

    def test_rename_and_delete_history(self):
        self.play_one_season()
        season.end()
        sid = season.latest_ended()["id"]
        self.assertFalse(season.delete_history(sid)[0])                            # 刚结束还没收尾：不能删
        season.wipe()
        self.assertTrue(season.rename_season(sid, "第一季")[0])
        self.assertEqual(season.get_season(sid)["name"], "第一季")
        self.assertTrue(season.delete_history(sid)[0])
        self.assertIsNone(season.get_season(sid))
        self.assertIsNone(seasonarchive.get(sid))
        self.assertFalse(season.delete_history(999)[0])

    def test_pages(self):
        self.play_one_season()
        season.end()
        season.wipe()
        sid = season.latest_ended()["id"]
        webusers.create_user("boss", "longenough1", "owner")
        app = create_app()
        c = app.test_client()
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(c, "/login")})
        html = c.get("/season").get_data(as_text=True)
        self.assertIn("有复盘存档", html)
        html = c.get(f"/season/{sid}").get_data(as_text=True)
        self.assertIn("每个人的数据", html)
        self.assertIn("约会", html)
        rid = seasonarchive.get(sid)["rooms"][0]["id"]
        self.assertIn("你好呀", c.get(f"/season/{sid}/room/{rid}").get_data(as_text=True))
        self.assertIn("你好呀", c.get(f"/season/{sid}/room/{rid}/export").get_data(as_text=True))
        self.assertEqual(c.get("/season/9999").status_code, 404)
        c.post(f"/season/{sid}/delete-history", data={"confirm": "错的", "_csrf": token(c)})
        self.assertIsNotNone(season.get_season(sid))                               # 名字没输对：不删
        c.post(f"/season/{sid}/delete-history", data={"confirm": "甲季", "_csrf": token(c)})
        self.assertIsNone(season.get_season(sid))


    def test_edit_history_and_list_buttons(self):
        self.play_one_season()
        season.end(); season.wipe()
        sid = season.latest_ended()["id"]
        self.assertFalse(season.edit_history(sid, "甲季", "2026-11-20", "2026-11-10")[0])
        self.assertTrue(season.edit_history(sid, "第一季", "2026-11-09", "2026-11-21", "2026-11-23")[0])
        s = season.get_season(sid)
        self.assertEqual((s["name"], s["start_date"], s["supp_end"]), ("第一季", "2026-11-09", "2026-11-23"))
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(c, "/login")})
        html = c.get("/season").get_data(as_text=True)
        for word in ("查看", "编辑", "删除", f'id="he{sid}"', f'删除历史季度「{season.get_season(sid)["name"]}」'):
            self.assertIn(word, html)
        r = c.post(f"/season/{sid}/rename", data={"name": "改了", "start": "2026-11-09", "end": "2026-11-21", "supp": "", "back": "list", "_csrf": token(c)})
        self.assertTrue(r.headers["Location"].endswith("/season#past"))
        self.assertEqual(season.get_season(sid)["name"], "改了")
        r = c.post(f"/season/{sid}/delete-history", data={"confirm": "改了", "back": "list", "_csrf": token(c)})
        self.assertTrue(r.headers["Location"].endswith("/season#past"))
        self.assertIsNone(season.get_season(sid))

    def test_conflict_and_calendar(self):
        self.assertTrue(season.book("甲季", "2026-11-10", "2026-11-20", "2026-11-22")[0])
        self.assertFalse(season.book("乙季", "2026-11-22", "2026-11-25")[0])              # 撞了甲季的补戏期
        self.assertTrue(season.book("乙季", "2026-11-23", "2026-11-25")[0])
        b = season.booked()[1]
        self.assertFalse(season.update_booking(b["id"], "乙季", "2026-11-15", "2026-11-25")[0])
        self.assertTrue(season.update_booking(b["id"], "乙季", "2026-11-23", "2026-11-28")[0])   # 自己和自己不算撞
        g = season.month_grid("2026-11", now="2026-11-12")
        cells = {d["date"]: d for w in g["weeks"] for d in w}
        self.assertEqual([i["name"] for i in cells["2026-11-12"]["items"]], ["甲季"])
        self.assertEqual(cells["2026-11-21"]["items"][0]["kind"], "supp")
        self.assertTrue(cells["2026-11-12"]["today"])
        self.assertEqual(season.month_grid("乱写")["prev"] is not None, True)
        self.assertIn("甲季", season.upcoming_line("2026-11-05"))

    def test_calendar_on_pages(self):
        season.book("甲季", "2026-11-10", "2026-11-20")
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(c, "/login")})
        html = c.get("/season?cal=2026-11").get_data(as_text=True)
        self.assertIn("2026 年 11 月", html)
        self.assertIn("甲季", html)
        self.assertIn('id="scal"', html)
        self.assertIn("季度日历", c.get("/?cal=2026-11").get_data(as_text=True))


    def test_report_section_and_poster_downloads(self):
        import zipfile, io
        self.play_one_season()
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(c, "/login")})
        html = c.get("/season").get_data(as_text=True)
        self.assertIn("季末报告", html)
        self.assertIn("全部打包下载", html)                                        # 进行中的季度有互动：能下载
        c.post("/season/report", data={"on": "1", "_csrf": token(c)})
        c.post("/season/report", data={"skin": "rose", "_csrf": token(c)})
        from changri.core import seasonreport
        self.assertTrue(seasonreport.enabled())
        self.assertEqual(seasonreport.skin(), "rose")
        a = roles.get_role_by_name("甲")["id"]
        r = c.get(f"/season/poster/live/{a}.jpg")
        self.assertEqual(r.mimetype, "image/jpeg")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        z = zipfile.ZipFile(io.BytesIO(c.get("/season/poster/live.zip").data))
        self.assertEqual(len([n for n in z.namelist() if n.endswith(".jpg")]), 2)
        season.end(); season.wipe()                                               # 收尾后从存档下载
        sid = season.latest_ended()["id"]
        self.assertIn("季末海报全部打包下载", c.get(f"/season/{sid}").get_data(as_text=True))
        self.assertEqual(c.get(f"/season/{sid}/poster/0.jpg").mimetype, "image/jpeg")
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/season/{sid}/posters.zip").data))
        self.assertEqual(len([n for n in z.namelist() if n.endswith(".jpg")]), 2)
        self.assertEqual(c.get(f"/season/{sid}/poster/9.jpg").status_code, 404)


if __name__ == "__main__":
    unittest.main()
