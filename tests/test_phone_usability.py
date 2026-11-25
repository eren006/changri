"""小手机在手机上好用：草稿不丢、中文出错页、图片在本页看大图、激活码链接、输入框字号。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "pu.db")

from changri.core import phonecodes, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.phone import app as phone_app  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402

TPL = Path(__file__).resolve().parent.parent / "src" / "changri" / "phone" / "templates"


class Usability(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("phone_codes", "phone_seen", "messages", "roles", "role_groups", "config", "phone_favs"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")
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

    def login(self, role=None):
        tok = self.csrf()
        self.c.post("/p", data={"code": phonecodes.code_for_role((role or self.a)["id"]), "_csrf": tok})

    def send(self, **data):
        async def fake(bot, grp, text):
            return "mid"
        tok = data.pop("_csrf", None) or self.csrf("/p/me/new")
        with mock.patch("changri.core.qqsend.make_send", return_value=fake), mock.patch("changri.core.phonegate.season.phase", return_value="playing"):
            return self.c.post("/p/me/send", data={"_csrf": tok, **data})

    # ---------- B. 出错页 ----------
    def test_disabled_phone_page_is_chinese(self):
        set_config("phone_enabled", "0")
        r = self.c.get("/p")
        self.assertEqual(r.status_code, 404)
        html = r.get_data(as_text=True)
        self.assertIn("小手机暂时关着，可以先在 QQ 里玩", html)
        self.assertNotIn("Not Found", html)
        self.assertIn('id="oopsBack"', html)

    def test_404_and_405(self):
        r = self.c.get("/p/no-such-page")
        self.assertEqual(r.status_code, 404)
        self.assertIn("没有这个页面", r.get_data(as_text=True))
        self.assertIn('href="/p/"', r.get_data(as_text=True))
        r = self.c.get("/p/me/send")
        self.assertEqual(r.status_code, 405)
        self.assertIn("返回", r.get_data(as_text=True))

    def test_csrf_failure_keeps_text(self):
        self.login()
        r = self.c.post("/p/me/send", data={"_csrf": "stale", "to": self.b["id"], "kind": "sms", "text": "写了很久的一段话"})
        self.assertEqual(r.status_code, 400)
        html = r.get_data(as_text=True)
        self.assertIn("页面放得太久", html)
        self.assertNotIn("Bad Request", html)
        html = self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True)
        self.assertIn(">写了很久的一段话</textarea>", html)
        html = self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True)   # 填回一次就清掉
        self.assertNotIn("写了很久的一段话", html)

    def test_json_errors_for_fetch(self):
        self.login()
        r = self.c.post("/p/me/fav", data={"_csrf": "stale", "id": 1}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_413(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 1000
        self.login()
        r = self.c.post("/p/me/send", data={"_csrf": "x", "text": "字" * 2000})
        self.assertEqual(r.status_code, 413)
        self.assertIn("图片太大了", r.get_data(as_text=True))

    def test_500(self):
        app = create_phone_app("k" * 32)
        app.logger.disabled = True

        @app.route("/p/boom")
        def boom():
            raise RuntimeError("x")
        r = app.test_client().get("/p/boom")
        self.assertEqual(r.status_code, 500)
        self.assertIn("出了点问题，稍后再试，或者告诉管理员", r.get_data(as_text=True))

    def test_csrf_session_is_permanent(self):
        r = self.c.get("/p")
        cookie = "; ".join(r.headers.getlist("Set-Cookie"))
        self.assertIn("session=", cookie)
        self.assertIn("Expires=", cookie)

    # ---------- A. 草稿 ----------
    def test_failed_send_prefills_and_success_clears(self):
        self.login()
        r = self.send(to=self.b["id"], kind="sms", text="我要看色情内容 sb")
        html = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("不能发送的词", html)
        self.assertIn(">我要看色情内容 sb</textarea>", html)
        self.assertIn(f'data-draft="msg:{self.b["id"]}"', html)
        r = self.send(to=self.b["id"], kind="sms", text="晚上好")
        html = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn(f'["msg:{self.b["id"]}"].forEach', html)       # 发成功：下一页清掉本机草稿
        self.assertIn("></textarea>", html)
        html = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("[].forEach", html)

    def test_fav_toggle_without_reload(self):
        self.login()
        self.send(to=self.b["id"], kind="sms", text="收藏我")
        mid = get_conn().execute("SELECT id FROM messages").fetchone()[0]
        tok = self.csrf(f"/p/me/{self.b['id']}")
        h = {"X-Requested-With": "fetch"}
        self.assertTrue(self.c.post("/p/me/fav", data={"_csrf": tok, "id": mid}, headers=h).get_json()["fav"])
        self.assertFalse(self.c.post("/p/me/fav", data={"_csrf": tok, "id": mid}, headers=h).get_json()["fav"])
        self.assertEqual(self.c.post("/p/me/fav", data={"_csrf": tok, "id": mid}).status_code, 302)   # 没有脚本时照旧

    def test_draft_fields_marked(self):
        for name, key in (("lovemail.html", 'data-draft="lovemail"'), ("room.html", 'data-draft="room:'), ("special_thread.html", 'data-draft="alias:'),
                          ("bottles.html", 'data-draft="bottle"'), ("bottle_thread.html", 'data-draft="bottle:'), ("wishes.html", 'data-draft="wish"')):
            self.assertIn(key, (TPL / name).read_text(encoding="utf-8"), name)

    # ---------- C. 图片 ----------
    def test_images_open_in_page(self):
        for p in TPL.glob("*.html"):
            s = p.read_text(encoding="utf-8")
            for line in s.splitlines():
                if "target=\"_blank\"" in line:
                    self.assertNotIn("_image', img_id=i) }}\" target", line, p.name)
        self.login()
        html = self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True)
        self.assertIn('id="phoneViewer"', html)

    # ---------- D. 输入框字号 ----------
    def test_inputs_not_below_16px(self):
        html = self.c.get("/p").get_data(as_text=True)
        self.assertIn("font-size: max(16px, 1em) !important", html)
        self.assertNotIn("font-size:14px\"><button type=\"submit\" class=\"quiet-button\"", (TPL / "thread.html").read_text(encoding="utf-8"))
        self.assertIn("min-height: 44px; min-width: 44px", (TPL / "_phone_base_css.html").read_text(encoding="utf-8"))

    # ---------- E. 激活 ----------
    def test_entry_hint_and_hash_code(self):
        html = self.c.get("/p").get_data(as_text=True)
        self.assertIn("个人群里发「手机码」", html)
        self.assertNotIn("管理员发给你的激活码", html)
        self.assertIn("code=([A-Za-z0-9]{4,20})", html)
        self.login()
        r = self.c.get("/p")                         # 已登录再点带码的链接：直接进自己的手机
        self.assertEqual(r.status_code, 302)
        self.assertIn("/p/me", r.headers["Location"])

    def test_reply_carries_code_in_fragment(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "changri" / "plugins" / "phone.py").read_text(encoding="utf-8")
        self.assertIn("{url}#code={code}", src)
        self.assertIn("激活码：{code}", src)


if __name__ == "__main__":
    unittest.main()
