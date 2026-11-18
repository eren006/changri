"""小手机「每日一句」：默认关；按天缓存，拉不到用最近一次；发现页有卡片和脚本。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "dq.db")

from changri.core import dailyquote, phonecodes, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.phone import app as phone_app  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402

DAY1, DAY2 = 1_700_000_000, 1_700_000_000 + 86400


class Core(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")

    def test_off_by_default(self):
        self.assertFalse(dailyquote.enabled())

    def test_parse(self):
        self.assertEqual(dailyquote._hitokoto({"hitokoto": "句", "from": "书", "from_who": "某"}), ("句", "——某《书》"))
        self.assertEqual(dailyquote._hitokoto({"hitokoto": "句", "from": "", "from_who": None}), ("句", ""))
        self.assertEqual(dailyquote._shici({"data": {"content": "诗", "origin": {"author": "李白", "title": "静夜思"}}}), ("诗", "——李白《静夜思》"))

    def test_cache_per_day_and_fallback(self):
        calls = []

        def ok(kind, timeout=3):
            calls.append(kind)
            return (f"{kind}-{len(calls)}", "——x")
        with mock.patch.object(dailyquote, "fetch", ok):
            a = dailyquote.today(DAY1)
            b = dailyquote.today(DAY1)                     # 同一天不再去拉
        self.assertEqual(len(calls), 2)
        self.assertEqual(a, b)
        self.assertEqual([q["kind"] for q in a], ["hitokoto", "shici"])

        fails = []
        with mock.patch.object(dailyquote, "fetch", lambda kind, timeout=3: fails.append(kind)):
            c = dailyquote.today(DAY2)                     # 第二天拉不到：用昨天的
            dailyquote.today(DAY2)                         # 当天不反复去试
        self.assertEqual(c, a)
        self.assertEqual(len(fails), 2)

    def test_nothing_ever(self):
        with mock.patch.object(dailyquote, "fetch", lambda kind, timeout=3: None):
            self.assertEqual(dailyquote.today(DAY1), [])


class Page(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("phone_codes", "phone_seen", "roles", "role_groups", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")
        roles.create_role("甲", "B1", "P0", "M0")
        self.a = roles.get_role_by_name("甲")
        phone_app._fails.clear()
        self.c = create_phone_app("k" * 32).test_client()

    def _login(self):
        self.c.get("/p")
        with self.c.session_transaction() as s:
            tok = s.setdefault("csrf", "tok")
        self.c.post("/p", data={"code": phonecodes.code_for_role(self.a["id"]), "_csrf": tok})

    def test_discover_card_follows_switch(self):
        self._login()
        self.assertNotIn("dailyQuote", self.c.get("/p/me/discover").get_data(as_text=True))
        self.assertEqual(self.c.get("/p/me/quote").get_json(), {"quotes": []})
        set_config("ext:dailyquote", "1")
        self.assertIn("dailyQuote", self.c.get("/p/me/discover").get_data(as_text=True))
        with mock.patch.object(dailyquote, "fetch", lambda kind, timeout=3: ("一句", "——某")):
            qs = self.c.get("/p/me/quote").get_json()["quotes"]
        self.assertEqual(len(qs), 2)
        self.assertEqual(qs[0]["text"], "一句")

    def test_quote_needs_login(self):
        set_config("ext:dailyquote", "1")
        self.assertEqual(self.c.get("/p/me/quote").status_code, 401)


if __name__ == "__main__":
    unittest.main()
