"""自定义回复：匹配、范围、冷却、后台页面。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "ar.db")

from changri.core import autoreply as A  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


def rule(**kw):
    d = {"name": "", "mode": "contains", "words": "祝晓是谁", "replies": ["一个很可爱的女孩儿！"], "who": "all", "place": "all", "cooldown": 0}
    d.update(kw)
    return d


class AutoReply(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM autoreplies")
        A._last_hit.clear()

    def pick(self, text, **kw):
        base = dict(bot_id="B", group="G", has_role=True, is_admin=False, in_personal=False)
        base.update(kw)
        return A.pick(text, **base)

    def test_all_off_and_on(self):
        A.save_rule(rule(words="早"))
        A.save_rule(rule(words="晚"))
        self.assertEqual(A.set_all_enabled(False), 2)
        self.assertFalse(any(r["enabled"] for r in A.list_rules()))
        self.assertEqual(A.set_all_enabled(False), 0)
        self.assertEqual(A.set_all_enabled(True), 2)
        self.assertTrue(all(r["enabled"] for r in A.list_rules()))

    def test_modes(self):
        cases = [("contains", "祝晓", "你知道祝晓是谁吗", True), ("exact", "你好", "你好呀", False), ("exact", "你好", "你好", True),
                 ("prefix", "查", "查天气", True), ("prefix", "查", "我查", False), ("suffix", "吗", "好吗", True),
                 ("regex", r"^早+安$", "早早早安", True), ("regex", r"^早+安$", "晚安", False)]
        for mode, word, text, want in cases:
            A.save_rule(rule(mode=mode, words=word))
            self.assertEqual(bool(self.pick(text)), want, (mode, word, text))
            with get_conn() as c:
                c.execute("DELETE FROM autoreplies")

    def test_multi_words_random_reply_and_placeholder(self):
        A.save_rule(rule(words="早\n早安", replies=["早，{角色}", "早上好，{角色}"]))
        self.assertIn(self.pick("早安", role_name="小明")[0], {"早，小明", "早上好，小明"})
        self.assertIn("朋友", self.pick("早", role_name="")[0])

    def test_scope_and_cooldown(self):
        A.save_rule(rule(who="admin"))
        self.assertIsNone(self.pick("祝晓是谁"))
        self.assertIsNotNone(self.pick("祝晓是谁", is_admin=True))
        with get_conn() as c:
            c.execute("DELETE FROM autoreplies")
        A.save_rule(rule(place="personal", cooldown=60))
        self.assertIsNone(self.pick("祝晓是谁", in_personal=False))
        self.assertIsNotNone(self.pick("祝晓是谁", in_personal=True))
        self.assertIsNone(self.pick("祝晓是谁", in_personal=True))                 # 冷却中
        self.assertIsNotNone(self.pick("祝晓是谁", in_personal=True, group="G2"))    # 换个群不受影响

    def test_order_disable_and_hits(self):
        A.save_rule(rule(name="甲", replies=["甲回复"]))
        A.save_rule(rule(name="乙", replies=["乙回复"]))
        a, b = [r["id"] for r in A.list_rules()]
        self.assertEqual(self.pick("祝晓是谁"), ["甲回复"])
        A.move(b, -1)
        self.assertEqual(self.pick("祝晓是谁"), ["乙回复"])
        A.set_enabled(b, False)
        self.assertEqual(self.pick("祝晓是谁"), ["甲回复"])
        self.assertEqual(A.get_rule(a)["hits"], 2)
        self.assertEqual([p["name"] for p in A.preview("祝晓是谁")], ["乙", "甲"])

    def test_send_all_in_order(self):
        A.save_rule(rule(mode="exact", words="小圆", replies=["一个艺术天才和一个永远幸福的人！", "这是一首关于小圆的歌"], send_mode="all"))
        self.assertEqual(self.pick("小圆"), ["一个艺术天才和一个永远幸福的人！", "这是一首关于小圆的歌"])
        with get_conn() as c:
            c.execute("DELETE FROM autoreplies")
        A.save_rule(rule(replies=["甲", "乙"]))                       # 默认随机一条
        self.assertEqual(len(self.pick("祝晓是谁")), 1)

    def test_old_table_gets_send_mode_column(self):
        import sqlite3
        d = Path(tempfile.mkdtemp()) / "old.db"
        c = sqlite3.connect(d)
        c.execute("CREATE TABLE autoreplies (id INTEGER PRIMARY KEY, name TEXT)")
        c.commit(); c.close()
        from changri.core import db
        db._migrate(d)
        c = sqlite3.connect(d)
        self.assertIn("send_mode", [r[1] for r in c.execute("PRAGMA table_info(autoreplies)")])
        c.close()

    def test_validation(self):
        self.assertIn("触发词", A.save_rule(rule(words=""))[1])
        self.assertIn("回复", A.save_rule(rule(replies=["  "]))[1])
        self.assertIn("正则", A.save_rule(rule(mode="regex", words="(未闭合"))[1])
        self.assertFalse(A.matches({"mode": "contains", "word_list": ["a"]}, ""))


class AutoReplyAdmin(unittest.TestCase):
    def test_page_and_crud(self):
        import re

        from changri.admin import create_app
        from changri.core import webusers
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM autoreplies")
            c.execute("DELETE FROM web_users")
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        self.assertEqual(c.get("/autoreply").status_code, 200)
        r = c.post("/autoreply/save", data={"_csrf": tok("/autoreply"), "mode": "contains", "words": "祝晓是谁", "replies_json": '["可爱的女孩"]', "who": "all", "place": "all", "cooldown": "0"})
        self.assertEqual(r.status_code, 302)
        html = c.get("/autoreply").get_data(as_text=True)
        self.assertIn("祝晓是谁", html)
        self.assertIn("可爱的女孩", html)
        r = c.post("/autoreply/test", data={"_csrf": tok("/autoreply"), "text": "你知道祝晓是谁吗"})
        self.assertEqual(r.get_json()[0]["replies"], ["可爱的女孩"])
        bad = c.post("/autoreply/save", data={"_csrf": tok("/autoreply"), "mode": "contains", "words": "", "replies_json": "[]"}, follow_redirects=True)
        self.assertIn("至少写一个触发词", bad.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
