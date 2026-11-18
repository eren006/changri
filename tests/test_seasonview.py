"""复盘网页：结束季度自动打开、链接只发后台群；按天 / 场次 / 互动 / 角色的页面和 TXT；收尾以后照样能看；关闭、换链接；心动信只露署名。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "sv.db")

from changri.core import announce, roles, season, seasonview  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402


class SeasonView(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("season_archives", "seasons", "room_lines", "room_members", "rooms", "messages", "lovemails", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")
        set_config("ext:phone", "1")
        set_config("phone_base_url", "https://phone.example")
        season.book("春季", "2026-11-10", "2026-11-20")
        season.start(now="2026-11-12")
        roles.create_role("甲", "B", "G1", "M1")
        roles.create_role("乙", "B", "G2", "M2")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        now = int(time.time())
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, status, created_ts, ended_ts) "
                            "VALUES('约会',?,2,'14:00','15:00','咖啡厅','ended',?,?)", (a, now, now + 3600)).lastrowid
            c.execute("INSERT INTO rooms(kind, creator_id, day, status, created_ts) VALUES('约会',?,2,'open',?)", (a, now))   # 进行中的不放
            for r in (a, b):
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, r))
            c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,1,3)", (rid, now, a, "甲", "甲：你好呀"))
            c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,0,0)", (rid, now + 1, b, "乙", "哈哈哈"))
            c.execute("INSERT INTO messages(kind, from_role_id, to_role_id, status, created_ts, day, content) VALUES('sms',?,?,'sent',?,'S1D2.0','今晚见')", (a, b, now))
            c.execute("INSERT INTO lovemails(from_role_id, to_role_id, signature, content, day, created_ts, status, sent_ts) "
                      "VALUES(?,?,'雨伞','谢谢你的伞','S1D2.0',?,'sent',?)", (a, b, now, now))
        self.rid = rid
        announce.set_backstage("B", "STAFF")
        self.sent = []

        async def send(bot_id, gid, text, buttons=None):
            self.sent.append((gid, text))
            return "x"
        ok, self.msg = asyncio.run(season.end_and_report(send))
        self.assertTrue(ok)
        self.sid = season.latest_ended()["id"]
        self.c = create_phone_app("k" * 32).test_client()

    def test_end_publishes_and_sends_to_backstage_only(self):
        self.assertTrue(seasonview.is_published(self.sid))
        self.assertIn("已发到后台群", self.msg)
        self.assertEqual([g for g, _ in self.sent], ["STAFF"])                          # 只发后台群
        url = seasonview.url(self.sid)
        self.assertIn(url, self.sent[0][1])
        self.assertTrue(url.startswith("https://phone.example/v/"))

    def test_pages_and_txt(self):
        root = "/v/" + seasonview.token_of(self.sid)
        home = self.c.get(root).get_data(as_text=True)
        self.assertIn("春季 · 本季总结", home)
        self.assertIn("D2", home)
        day = self.c.get(root + "/day/D2").get_data(as_text=True)
        self.assertIn("咖啡厅", day); self.assertIn(f"R{self.rid}", day)
        self.assertIn("今晚见", day)
        room = self.c.get(f"{root}/room/{self.rid}").get_data(as_text=True)
        self.assertIn('<div class="rp-content">你好呀</div>', room)                       # 名字单独写在上面，正文不再带「甲：」
        self.assertNotIn("哈哈哈", room)                                                # 闲聊不上网页
        txt = self.c.get(f"{root}/room/{self.rid}.txt")
        self.assertEqual(txt.status_code, 200)
        self.assertIn("哈哈哈（闲聊）", txt.get_data(as_text=True))                      # TXT 里带着闲聊
        self.assertIn("甲：你好呀", txt.get_data(as_text=True))
        self.assertNotIn("甲：甲：", txt.get_data(as_text=True))                           # 不会「甲：甲：」
        ev = self.c.get(root + "/events").get_data(as_text=True)
        self.assertIn("雨伞", ev)
        self.assertNotIn("<span class=\"frm\">甲</span><span class=\"arr\">→</span><span class=\"to\">乙</span></span>", ev)
        self.assertIn('<span class="frm">雨伞</span>', ev)                              # 心动信只露署名
        self.assertIn("咖啡厅", self.c.get(root + "/role/乙").get_data(as_text=True))
        self.assertEqual(self.c.get("/v/" + "0" * 32).status_code, 404)

    def test_wipe_keeps_it_and_toggles(self):
        root = "/v/" + seasonview.token_of(self.sid)
        season.wipe()
        self.assertIn("你好呀", self.c.get(f"{root}/room/{self.rid}").get_data(as_text=True))       # 收尾以后照样能看
        set_config("season_view_lovemail", "0")
        self.assertNotIn("雨伞", self.c.get(root + "/events").get_data(as_text=True))
        seasonview.unpublish(self.sid)
        self.assertEqual(self.c.get(root).status_code, 404)
        self.assertEqual(seasonview.url(self.sid), "")
        seasonview.publish(self.sid)
        self.assertEqual(self.c.get(root).status_code, 200)                              # 再打开还是原来的链接
        new = "/v/" + seasonview.publish(self.sid, new_link=True)
        self.assertEqual(self.c.get(root).status_code, 404)                              # 换链接：旧的失效
        self.assertEqual(self.c.get(new).status_code, 200)

    def test_zip_all_txt(self):
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(seasonview.all_text_zip(self.sid)))
        self.assertEqual(len(z.namelist()), 1)
        self.assertIn("咖啡厅", z.namelist()[0])


if __name__ == "__main__":
    unittest.main()
