import json, os, sys, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import errors_service as s  # noqa: E402


class ErrorsService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CHANGRI_ERRORS_DIR"] = self.tmp.name
        os.environ["CHANGRI_ERRORS_TOKEN"] = "tok-for-test"
        s.HITS.clear()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}/api/errors"

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()
        os.environ.pop("CHANGRI_ERRORS_TOKEN", None)

    def post(self, data, raw=False):
        body = data if raw else json.dumps(data).encode()
        req = urllib.request.Request(self.base, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def get(self, token):
        req = urllib.request.Request(self.base, headers={"Authorization": "Bearer " + token} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_store_and_scrub(self):
        ok = {"app": "changri_bot", "version": "0.18.9", "errors": [
            {"ts": 1, "kind": "error", "source": "x.py:1", "message": "boom 123456789 AbCdEfGhIjKlMnOpQrStUvWxYz", "trace": "t", "version": "0.18.9"}, "垃圾"]}
        self.assertEqual(self.post(ok), 200)
        code, rows = self.get("tok-for-test")
        self.assertEqual(code, 200); self.assertEqual(len(rows), 1)
        self.assertNotIn("123456789", rows[0]["message"]); self.assertNotIn("AbCdEf", rows[0]["message"])

    def test_bad_input(self):
        self.assertEqual(self.post(b"not json", raw=True), 400)
        self.assertEqual(self.post({"errors": "x"}), 400)
        self.assertEqual(self.post({"errors": []}), 400)
        self.assertEqual(self.post(b"x" * (s.MAX_BODY + 1), raw=True), 413)

    def test_view_needs_token(self):
        self.assertEqual(self.get("")[0], 404)
        self.assertEqual(self.get("wrong")[0], 404)

    def test_rate_limit(self):
        for i in range(10):
            self.assertTrue(s.rate_ok("8.8.8.8", 100 + i))
        self.assertFalse(s.rate_ok("8.8.8.8", 110))

    def test_real_sender(self):
        """长日bot 自己的发送函数 → 本服务，发完标记为已发。"""
        from changri.core import errlog, db
        old = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "x.db"      # 本机错误文件写到临时目录，不污染 data/
        self.addCleanup(setattr, db, "DB_PATH", old)
        errlog.record("error", "t.py:1", "实际报错 777777777", "trace")
        orig = errlog.report_url
        errlog.report_url = lambda: self.base
        try:
            n = errlog.report_pending()
        finally:
            errlog.report_url = orig
        self.assertGreaterEqual(n, 1)
        self.assertGreaterEqual(len(self.get("tok-for-test")[1]), 1)


if __name__ == "__main__":
    unittest.main()
