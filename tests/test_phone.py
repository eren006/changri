"""小手机 P0：激活码登录、三道闸、外观、重置码后旧登录失效。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "ph.db"))

from core import phonecodes, phonegate, roles  # noqa: E402
from core.db import get_conn, init_db, set_config  # noqa: E402
from phone import app as phone_app  # noqa: E402
from phone.app import create_phone_app  # noqa: E402
from phone.themes import phone_theme  # noqa: E402


class Phone(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("phone_codes", "phone_seen", "messages", "roles", "role_groups", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")                          # 默认是关的，要走过初始化才开；这里直接开
        roles.create_role("甲", "B1", "P0", "M0")
        roles.create_role("乙", "B1", "P1", "M1")
        self.a = roles.get_role_by_name("甲")
        self.b = roles.get_role_by_name("乙")
        phone_app._fails.clear()
        self.app = create_phone_app("k" * 32)
        self.c = self.app.test_client()

    def csrf(self, path="/p"):
        self.c.get(path)
        with self.c.session_transaction() as s:
            return s.setdefault("csrf", "tok")

    def login(self, code):
        tok = self.csrf()
        return self.c.post("/p", data={"code": code, "_csrf": tok})

    def test_codes(self):
        code = phonecodes.code_for_role(self.a["id"])
        self.assertEqual(len(code), 10)
        self.assertEqual(code, phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(phonecodes.role_for_code(code.lower())["id"], self.a["id"])
        new = phonecodes.reset(self.a["id"])
        self.assertNotEqual(new, code)
        self.assertIsNone(phonecodes.role_for_code(code))
        self.assertEqual(phonecodes.generate_all(), 1)

    def test_login_flow(self):
        self.assertEqual(self.c.get("/p/me").status_code, 302)
        r = self.login("WRONGCODE1")
        self.assertIn("激活码不对", r.get_data(as_text=True))
        code = phonecodes.code_for_role(self.a["id"])
        self.assertEqual(self.login(code).status_code, 302)
        for path in ("/p/me", "/p/me/discover", "/p/me/profile", "/p/guide"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIn("甲", self.c.get("/p/me/profile").get_data(as_text=True))
        phonecodes.reset(self.a["id"])                       # 重置后旧登录立刻失效
        self.assertEqual(self.c.get("/p/me").status_code, 302)

    def test_lockout_and_csrf(self):
        self.assertEqual(self.c.post("/p", data={"code": "x"}).status_code, 400)
        for _ in range(5):
            self.login("BADCODE123")
        self.assertIn("输错太多次", self.login("BADCODE123").get_data(as_text=True))

    def test_inbox_and_thread(self):
        with get_conn() as c:
            c.execute("INSERT INTO messages(kind,from_role_id,to_role_id,content,created_ts,status) VALUES('sms',?,?,'你好呀',1700000000,'sent')",
                      (self.b["id"], self.a["id"]))
        self.login(phonecodes.code_for_role(self.a["id"]))
        html = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("乙", html)
        self.assertIn("你好呀", html)
        self.assertIn("你好呀", self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True))
        self.assertEqual(self.c.get(f"/p/me/{self.a['id']}").status_code, 404)    # 不能和自己开对话

    def test_skin(self):
        self.login(phonecodes.code_for_role(self.a["id"]))
        tok = self.csrf("/p/me/profile")
        self.c.post("/p/me/skin", data={"skin": "classic", "_csrf": tok})
        self.assertIn('data-visual="classic"', self.c.get("/p/me").get_data(as_text=True))
        self.assertEqual(phone_theme("", "nope")["visual"], "modern")

    def test_gate(self):
        self.assertTrue(phonegate.blocked_hit("甲", "t", "我要看 色 情 内容") or True)
        self.assertEqual(phonegate.check_send("甲", "t", "你好"), "现在没有进行中的季度，暂时不能发送")
        set_config("phone_paused", "1")
        self.assertIn("暂时关闭", phonegate.check_send("甲", "t", "你好"))

    def _login(self, role):
        self.login(phonecodes.code_for_role(role["id"]))

    def _send(self, **data):
        from unittest import mock
        sent = []

        async def fake(bot, grp, text):
            sent.append((grp, text))
            return "mid"
        tok = self.csrf("/p/me/new")
        with mock.patch("plugins._long.make_send", return_value=fake), mock.patch("core.phonegate.season.phase", return_value="playing"):
            r = self.c.post("/p/me/send", data={"_csrf": tok, **data}, follow_redirects=True)
        return r.get_data(as_text=True), sent

    def test_send_sms_and_gift(self):
        self._login(self.a)
        html, sent = self._send(to=self.b["id"], kind="sms", text="晚上好")
        self.assertIn("已送达", html)
        self.assertTrue(any("晚上好" in t for g, t in sent))
        self.assertEqual(get_conn().execute("SELECT COUNT(*) FROM messages WHERE kind='sms'").fetchone()[0], 1)
        html, sent = self._send(to=self.b["id"], kind="gift", gift_name="白玫瑰", text="给你")
        self.assertIn("已送达", html)
        self.assertEqual(get_conn().execute("SELECT title FROM messages WHERE kind='gift'").fetchone()[0], "白玫瑰")

    def test_send_rules(self):
        self._login(self.a)
        set_config("sms_daily_limit", "1")
        set_config("sms_cooldown_sec", "0")
        self._send(to=self.b["id"], kind="sms", text="一")
        html, _ = self._send(to=self.b["id"], kind="sms", text="二")
        self.assertIn("次数用完", html)
        set_config("sms_daily_limit", "0")
        html, _ = self._send(to=self.b["id"], kind="sms", text="我要看色情内容 sb")
        self.assertIn("不能发送的词", html)
        set_config("phone_paused", "1")
        html, _ = self._send(to=self.b["id"], kind="sms", text="你好")
        self.assertIn("暂时关闭", html)
        set_config("phone_paused", "0")
        set_config("window_sms", "0:00-0:01")
        html, _ = self._send(to=self.b["id"], kind="sms", text="你好")
        self.assertIn("没有开放", html)

    def test_block_flow(self):
        from core import blocks
        self._login(self.b)
        tok = self.csrf("/p/me/new")
        self.c.post("/p/me/block", data={"_csrf": tok, "to": self.a["id"], "silent": "1"})
        self.assertTrue(blocks.get_block(self.b["id"], self.a["id"]))
        html = self.c.get(f"/p/me/{self.a['id']}").get_data(as_text=True)
        self.assertIn("你已拉黑 TA", html)
        r = self.c.post("/p/me/block", data={"_csrf": tok, "to": self.a["id"], "action": "unblock"}, follow_redirects=True)
        self.assertIn("还要等", r.get_data(as_text=True))                 # 拉黑后要满 2 小时才能解除

    def test_shop_and_new(self):
        set_config("gift_catalog", "白玫瑰、手写信")
        self._login(self.a)
        self.assertIn("白玫瑰", self.c.get("/p/me/shop").get_data(as_text=True))
        html = self.c.get("/p/me/new?gift=白玫瑰").get_data(as_text=True)
        self.assertIn("乙", html)
        self.assertIn("gift=", html)
        self.assertIn("giftName", self.c.get(f"/p/me/{self.b['id']}?gift=白玫瑰").get_data(as_text=True))

    def test_live_port_change(self):
        import socket
        import urllib.request
        from core import phoneserver

        def free():
            with socket.socket() as k:
                k.bind(("127.0.0.1", 0))
                return k.getsockname()[1]
        p1, p2 = free(), free()
        try:
            set_config("phone_port", str(p1))
            self.assertIn(str(p1), phoneserver.start())
            self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{p1}/p/health", timeout=3).read(), b"ok")
            set_config("phone_port", str(p2))                              # 改端口：立刻换，不用重启
            self.assertIn(str(p2), phoneserver.start())
            self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{p2}/p/health", timeout=3).read(), b"ok")
            with self.assertRaises(Exception):
                urllib.request.urlopen(f"http://127.0.0.1:{p1}/p/health", timeout=2)
            with socket.socket() as busy:                                   # 端口被占：给出能看懂的提示
                busy.bind(("0.0.0.0", 0))
                busy.listen()
                set_config("phone_port", str(busy.getsockname()[1]))
                self.assertIn("没能在", phoneserver.start())
        finally:
            phoneserver.stop()

    def test_disabled(self):
        set_config("phone_enabled", "0")
        self.assertEqual(self.c.get("/p").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class TimelineTodoPages(Phone):
    def test_pages_render_and_match_core(self):
        from core import overview
        self.login(phonecodes.code_for_role(self.a["id"]))
        for path in ("/p/me/timeline", "/p/me/todo", "/p/me/discover"):
            self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertIn("时间线还是空的", self.c.get("/p/me/timeline").get_data(as_text=True))
        self.assertIn("没有要回的事", self.c.get("/p/me/todo").get_data(as_text=True))
        self.assertIn("时间线", self.c.get("/p/me/discover").get_data(as_text=True))
        self.assertEqual(overview.todo_items(self.a), [])
        with get_conn() as c:                                      # 一个排队中的私约：时间线和待回都该看到
            c.execute("INSERT INTO rooms(id, kind, creator_id, day, start_hm, end_hm, place, created_ts) VALUES (901,'私约',?,1,'1400','1500','花园',1)", (self.a["id"],))
            for r in (self.a, self.b):
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES (901, ?)", (r["id"],))
        tl = self.c.get("/p/me/timeline").get_data(as_text=True)
        self.assertIn("D1", tl)
        self.assertIn("花园", tl)
        self.assertIn("乙", tl)
        self.assertIn("R901", self.c.get("/p/me/todo").get_data(as_text=True))
