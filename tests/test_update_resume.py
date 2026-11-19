"""在线更新的断点续传：下载断在半路时用 Range 从断的地方接着下；服务器不认 Range 就从头下；一直没进展就放弃。"""
import hashlib
import http.server
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from changri.core import updater  # noqa: E402

BODY = os.urandom(300_000)
SHA = hashlib.sha256(BODY).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    srv = None                    # 测试里设：drop_after（每次最多发多少字节就断）、ranges（认不认 Range）、log

    def log_message(self, *a):
        pass

    def do_GET(self):
        cfg = _Handler.srv
        rng = self.headers.get("Range")
        cfg["log"].append(rng)
        if cfg.get("dead"):
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers()
            return
        start = 0
        if rng and cfg["ranges"]:
            start = int(rng.split("=")[1].rstrip("-"))
            if start >= len(BODY):
                self.send_response(416); self.send_header("Content-Length", "0"); self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        else:
            self.send_response(200)
        part = BODY[start:]
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        n = cfg["drop_after"]
        if n and len(part) > n:
            self.wfile.write(part[:n]); self.wfile.flush()
            self.close_connection = True           # 话没说完就挂断：客户端读到的比 Content-Length 少
            return
        self.wfile.write(part)


class ResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/app.zip"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = _Handler.srv = {"drop_after": 0, "ranges": True, "log": []}
        for p in (mock.patch.dict(os.environ, {"CHANGRI_UPDATE_ALLOW_FILE": "1"}),
                  mock.patch.object(updater.time, "sleep"),
                  mock.patch.object(updater, "_direct_url", return_value=None)):
            p.start(); self.addCleanup(p.stop)

    def item(self, sha=SHA):
        return {"url": self.url, "sha256": sha, "signature": "", "size": len(BODY)}

    def test_resumes_from_where_it_broke(self):
        self.cfg["drop_after"] = 100_000           # 每次只给 10 万字节就断：要接着下 3 次才完整
        dest = updater._download(self.item(), self.tmp / "a.zip", updater.MAX_APP, None)
        self.assertEqual(dest.read_bytes(), BODY)
        self.assertEqual(self.cfg["log"], [None, "bytes=100000-", "bytes=200000-"])

    def test_server_without_range_starts_over(self):
        self.cfg.update(ranges=False)
        self.cfg["drop_after"] = 0
        dest = self.tmp / "a.zip"
        dest.write_bytes(b"x" * 1000)               # 上次留下的残片：_download 开头就删掉
        updater._download(self.item(), dest, updater.MAX_APP, None)
        self.assertEqual(dest.read_bytes(), BODY)

    def test_range_ignored_midway_restarts_cleanly(self):
        """断了以后服务器回 200（不认 Range）：从头写，不能把整份接在残片后面。"""
        dest = self.tmp / "a.zip"
        dest.write_bytes(BODY[:5000])
        self.cfg["ranges"] = False
        updater._fetch_to(self.url, dest, updater.MAX_APP)
        self.assertEqual(dest.read_bytes(), BODY)

    def test_partial_longer_than_file_dropped(self):
        dest = self.tmp / "a.zip"
        dest.write_bytes(BODY + b"extra")
        with self.assertRaises(Exception):
            updater._fetch_to(self.url, dest, updater.MAX_APP)   # 416
        self.assertFalse(dest.exists())

    def test_gives_up_when_no_progress(self):
        self.cfg["dead"] = True
        with self.assertRaises(updater.UpdateError) as cm:
            updater._download(self.item(), self.tmp / "a.zip", updater.MAX_APP, None)
        self.assertIn("下载失败", str(cm.exception))
        self.assertEqual(len(self.cfg["log"]), updater.STALL_TRIES)
        self.assertFalse((self.tmp / "a.zip").exists())

    def test_falls_back_to_direct_and_keeps_partial(self):
        """主地址半路一直断：换直连地址，也是接着已经下到的部分下。"""
        calls = []
        real = updater._fetch_to

        def fake(url, dest, limit):
            calls.append(url)
            if url == self.url:
                if not dest.exists():
                    dest.write_bytes(BODY[:1000])
                raise OSError("连不上")
            return real(self.url, dest, limit)
        with mock.patch.object(updater, "_fetch_to", fake), \
                mock.patch.object(updater, "_direct_url", return_value="https://github.example/app.zip"):
            dest = updater._download(self.item(), self.tmp / "a.zip", updater.MAX_APP, None)
        self.assertEqual(dest.read_bytes(), BODY)
        self.assertEqual(calls[-1], "https://github.example/app.zip")
        self.assertEqual(self.cfg["log"], ["bytes=1000-"])

    def test_hash_mismatch_discarded(self):
        with self.assertRaises(updater.UpdateError) as cm:
            updater._download(self.item("0" * 64), self.tmp / "a.zip", updater.MAX_APP, None)
        self.assertIn("对不上", str(cm.exception))
        self.assertFalse((self.tmp / "a.zip").exists())

    def test_size_limit(self):
        with self.assertRaises(updater.UpdateError):
            updater._download(self.item(), self.tmp / "a.zip", 1000, None)

    def test_no_range_and_always_cut_gives_up(self):
        """不认 Range、每次都从头下又断在同一个地方：从头写不算进展，连续 STALL_TRIES 次就放弃。"""
        self.cfg.update(ranges=False, drop_after=100_000)
        with self.assertRaises(updater.UpdateError):
            updater._download(self.item(), self.tmp / "a.zip", updater.MAX_APP, None)
        self.assertEqual(len(self.cfg["log"]), 1 + updater.STALL_TRIES)

    def test_total_tries_capped(self):
        """一直有进展但总要断：总请求次数有上限。"""
        self.cfg["drop_after"] = 100_000           # 要 3 次才下完
        with mock.patch.object(updater, "MAX_TRIES", 2), self.assertRaises(updater.UpdateError):
            updater._download(self.item(), self.tmp / "a.zip", updater.MAX_APP, None)
        self.assertEqual(len(self.cfg["log"]), 2)


if __name__ == "__main__":
    unittest.main()
