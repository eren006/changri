"""作者的通知：机器人从主站拉、存本地、格式不对不动本地那份；后台每页带弹窗、总览列着、内容按文字显示不当 HTML。"""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "notices.db")
os.environ["QQ_BOTS"] = "[]"
import changri.admin as admin_mod  # noqa: E402

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import notices, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


def token(client, path="/login"):
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', client.get(path).get_data(as_text=True)).group(1)


class _Srv(BaseHTTPRequestHandler):
    reply = b"{}"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).reply)

    def log_message(self, *a):
        pass


class Notices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "notices.json"
        self._orig = notices._path
        notices._path = lambda: self.file

    def tearDown(self):
        notices._path = self._orig
        self.tmp.cleanup()

    def serve(self, obj):
        _Srv.reply = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}/api/notices"

    def test_clean(self):
        got = notices.clean({"notices": [
            {"id": 3, "ts": 100, "title": "  新版  ", "body": "x" * 2000},
            {"id": "4", "title": "id 不是数字"}, {"id": 5, "title": ""}, {"id": True, "title": "布尔不算"}, "乱的",
            {"id": 6, "ts": "昨天", "title": "t" * 80}, {"id": 7, "title": "a"}, {"id": 8, "title": "b"}]})
        self.assertEqual([n["id"] for n in got], [3, 6, 7])                       # 最多留 3 条
        self.assertEqual((got[0]["title"], len(got[0]["body"]), got[1]["ts"], len(got[1]["title"])), ("新版", 1000, 0, 40))
        self.assertIsNone(notices.clean({"oops": 1}))
        self.assertIsNone(notices.clean([]))

    def test_fetch_save_and_withdraw(self):
        self.assertEqual(notices.current(), [])
        self.assertTrue(notices.fetch(self.serve({"notices": [{"id": 1, "ts": 5, "title": "维护", "body": "今晚 10 点"}]})))
        self.assertEqual(notices.current(), [{"id": 1, "ts": 5, "title": "维护", "body": "今晚 10 点"}])
        self.assertFalse(notices.fetch(self.serve(b"<html>bad gateway")))           # 格式不对：留着上次的
        self.assertFalse(notices.fetch(self.serve({"error": 1})))
        self.assertFalse(notices.fetch("http://127.0.0.1:9/none", timeout=1))        # 连不上
        self.assertEqual(len(notices.current()), 1)
        self.assertTrue(notices.fetch(self.serve({"notices": []})))                 # 作者撤下了 → 本地也没了
        self.assertEqual(notices.current(), [])

    def test_admin_popup_and_dashboard(self):
        init_db()
        with get_conn() as c:
            for t in ("web_users", "config"):
                c.execute(f"DELETE FROM {t}")
        admin_mod._fails.clear()
        client = create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        client.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(client)})
        self.assertNotIn('id="an-dlg"', client.get("/").get_data(as_text=True))       # 没通知：没弹窗
        notices.save([{"id": 9, "ts": 1700000000, "title": "<b>新版</b>", "body": "第一行\n<script>alert(1)</script>"}])
        html = client.get("/").get_data(as_text=True)
        self.assertIn('id="an-dlg"', html)
        self.assertIn('data-id="9"', html)
        self.assertIn('class="callout an"', html)                                     # 总览上也列着
        self.assertIn("&lt;b&gt;新版&lt;/b&gt;", html)                                # 按文字显示
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn('id="an-dlg"', client.get("/no-at").get_data(as_text=True))   # 别的页面也会弹
        self.assertNotIn('class="callout an"', client.get("/no-at").get_data(as_text=True))
        logged_out = create_app().test_client().get("/login").get_data(as_text=True)
        self.assertNotIn('id="an-dlg"', logged_out)                                   # 没登录看不到


if __name__ == "__main__":
    unittest.main()
