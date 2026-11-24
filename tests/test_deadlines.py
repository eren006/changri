"""公知、关系线的截止时间（core/deadlines.py）：不填就和以前一样；到点后公知自动展示、关系线不能新发起，已有的线还能写。"""
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "deadlines.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

from changri.admin import create_app  # noqa: E402
from changri.core import deadlines, relations, sheets, webusers  # noqa: E402
from changri.core.db import get_config, get_conn, init_db  # noqa: E402

PAST, FUTURE = "2000-01-01 00:00", "2099-01-01 00:00"


def setup_data():
    init_db()
    with get_conn() as c:
        for t in ("sheet_images", "sheets", "rel_details", "relations", "roles", "config", "web_users"):
            c.execute(f"DELETE FROM {t}")
        a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','M1',1)").lastrowid
        b = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('乙','M2',1)").lastrowid
        d = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('丙','M3',1)").lastrowid
    return a, b, d


class Parse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(deadlines.parse(""), "")
        self.assertEqual(deadlines.parse("2026-11-25T20:00"), "2026-11-25 20:00")      # 网页 datetime-local
        self.assertEqual(deadlines.parse("2026-11-25 20:00"), "2026-11-25 20:00")
        self.assertEqual(deadlines.parse("2026-11-25"), "2026-11-25 00:00")
        self.assertIsNone(deadlines.parse("明天晚上"))

    def test_passed(self):
        setup_data()
        self.assertFalse(deadlines.passed("gongzhi"))                                   # 没设 = 不截止
        deadlines.set("gongzhi", "2026-11-25 20:00")
        at = time.mktime(time.strptime("2026-11-25 20:00", "%Y-%m-%d %H:%M"))
        self.assertFalse(deadlines.passed("gongzhi", now=at - 1))
        self.assertTrue(deadlines.passed("gongzhi", now=at))
        self.assertEqual(deadlines.label("gongzhi"), "11-25 20:00")


class Gongzhi(unittest.TestCase):
    def test_public_and_closed(self):
        a, _b, _d = setup_data()
        self.assertFalse(sheets.public())
        self.assertFalse(sheets.closed("gongzhi"))
        deadlines.set("gongzhi", FUTURE)
        self.assertFalse(sheets.public())
        self.assertFalse(sheets.closed("gongzhi"))
        deadlines.set("gongzhi", PAST)
        self.assertTrue(sheets.public())                     # 到点自动展示
        self.assertTrue(sheets.closed("gongzhi"))
        self.assertFalse(sheets.closed("erbiao"))            # 二表不受影响
        deadlines.set("gongzhi", "")
        self.assertFalse(sheets.public())


class Relations(unittest.TestCase):
    def test_everything_locked_after_deadline(self):
        a, b, d = setup_data()
        self.assertTrue(relations.add_detail(a, b, "乙", "第一段")[0])
        self.assertTrue(relations.add_item(a, b, "乙", "chat", "闲聊一句")[0])
        did = relations.details(relations.get_rel(a, b)["id"])[0]["id"]
        iid = relations.items(relations.get_rel(a, b)["id"], "chat")[0]["id"]
        deadlines.set("rel", PAST)
        self.assertIn("截止", relations.closed_note())
        for ok, msg in (relations.add_detail(a, d, "丙", "想新拉一条"), relations.add_detail(b, a, "甲", "已有的也不能接着写"),
                        relations.add_item(a, b, "乙", "chat", "再聊一句"), relations.edit_detail(did, a, "改一下")):
            self.assertFalse(ok)
            self.assertIn("截止", msg)
        self.assertFalse(relations.delete_detail(did, a)[0])
        self.assertFalse(relations.edit_item(iid, a, "改")[0])
        self.assertFalse(relations.delete_item(iid, a)[0])
        self.assertIsNone(relations.start(a, b, "乙")[0])
        self.assertEqual(len(relations.details(relations.get_rel(a, b)["id"])), 1)       # 原来的都还在，能看
        deadlines.set("rel", FUTURE)
        self.assertEqual(relations.closed_note(), "")
        self.assertTrue(relations.add_detail(a, d, "丙", "没到点可以拉")[0])


class Reopen(unittest.TestCase):
    def test_toggle(self):
        a, b, _d = setup_data()
        deadlines.set("gongzhi", PAST)
        deadlines.set("rel", PAST)
        self.assertTrue(sheets.closed("gongzhi"))
        self.assertFalse(relations.add_detail(a, b, "乙", "x")[0])
        deadlines.set_reopened("gongzhi", True)                  # 重新开放
        deadlines.set_reopened("rel", True)
        self.assertFalse(sheets.closed("gongzhi"))
        self.assertTrue(sheets.public())                         # 照样展示
        self.assertTrue(relations.add_detail(a, b, "乙", "x")[0])
        deadlines.set_reopened("rel", False)                     # 重新关闭
        self.assertFalse(relations.add_detail(b, a, "甲", "y")[0])
        deadlines.set("gongzhi", PAST)                           # 改截止时间：重新开放清掉
        self.assertTrue(sheets.closed("gongzhi"))

    def test_admin_button(self):
        setup_data()
        deadlines.set("rel", PAST)
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        self.assertIn("重新开放关系线", c.get("/relations?view=relations").get_data(as_text=True))
        self.assertNotIn("重新开放公知", c.get("/relations?view=sheets").get_data(as_text=True))   # 没截止不出按钮
        c.post("/deadlines/rel/reopen", data={"_csrf": tok("/relations?view=relations"), "on": "1"})
        self.assertTrue(deadlines.reopened("rel"))
        self.assertIn("重新关闭关系线", c.get("/relations?view=relations").get_data(as_text=True))
        c.post("/deadlines/rel/reopen", data={"_csrf": tok("/relations?view=relations"), "on": "0"})
        self.assertFalse(deadlines.reopened("rel"))


class Announce(unittest.TestCase):
    def test_gongzhi_announced_once(self):
        import asyncio
        setup_data()
        sent = []

        async def send(bot_id, gid, text):
            sent.append((bot_id, gid, text))

        run = lambda target=("B1", "G1"): asyncio.run(deadlines.tick(send, target))
        self.assertFalse(run())                                   # 没设截止
        deadlines.set("gongzhi", FUTURE)
        self.assertFalse(run())                                   # 没到点
        deadlines.set("gongzhi", PAST)
        self.assertFalse(run(None))                               # 没设公告群：先不播、也不记
        self.assertTrue(run())
        self.assertFalse(run())                                   # 只播一次
        self.assertEqual(len(sent), 1)
        self.assertIn("公知已截止，可以互相查看了", sent[0][2])
        deadlines.set("gongzhi", "2001-01-01 00:00")              # 改了截止时间：再播
        self.assertTrue(run())


class AdminPage(unittest.TestCase):
    def test_set_and_clear(self):
        setup_data()
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        self.assertIn("公知截止：未设", c.get("/relations?view=sheets").get_data(as_text=True))
        self.assertIn("关系线截止：未设", c.get("/relations?view=relations").get_data(as_text=True))
        c.post("/deadlines/gongzhi", data={"_csrf": tok("/relations?view=sheets"), "at": "2026-11-25T20:00"})
        self.assertEqual(get_config("gongzhi_deadline"), "2026-11-25 20:00")
        self.assertIn("公知截止：11-25 20:00", c.get("/relations?view=sheets").get_data(as_text=True))
        c.post("/deadlines/rel", data={"_csrf": tok("/relations?view=relations"), "at": "乱写"})
        self.assertEqual(get_config("rel_deadline", ""), "")                             # 看不懂的不存
        c.post("/deadlines/gongzhi", data={"_csrf": tok("/relations?view=sheets"), "at": "2026-11-25T20:00", "clear": "1"})
        self.assertEqual(get_config("gongzhi_deadline"), "")
        self.assertEqual(c.post("/deadlines/nope", data={"_csrf": tok("/relations")}).status_code, 404)


    def test_gongzhi_status_callout(self):
        """公知没展示时，页面上直接说明玩家看不到、怎么打开，并给一个「现在展示公知」按钮；打开后换成一句说明。"""
        setup_data()
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        html = c.get("/relations?view=sheets").get_data(as_text=True)
        self.assertIn("公知现在没有对玩家展示", html)
        self.assertIn("现在展示公知", html)
        c.post("/sheets/public", data={"_csrf": tok("/relations?view=sheets"), "on": "1"})
        self.assertTrue(sheets.public())
        html = c.get("/relations?view=sheets").get_data(as_text=True)
        self.assertNotIn("公知现在没有对玩家展示", html)
        self.assertIn("公知正在展示", html)
        c.post("/sheets/public", data={"_csrf": tok("/relations?view=sheets"), "on": "0"})
        deadlines.set("gongzhi", PAST)                            # 截止了：自动展示，不再提示去打开
        html = c.get("/relations?view=sheets").get_data(as_text=True)
        self.assertNotIn("公知现在没有对玩家展示", html)
        self.assertIn("已经自动对所有人展示", html)

if __name__ == "__main__":
    unittest.main()
