import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "lb.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from changri.core import loopbridge  # noqa: E402


class LoopBridgeTests(unittest.TestCase):
    """网页线程等机器人主循环：超时不能说成「失败」（玩家会再点一次，发两遍），事情也不能半路掐掉。"""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.t = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.t.start()
        loopbridge.set_loop(self.loop)

    def tearDown(self):
        loopbridge.set_loop(None)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.t.join(2)

    def test_result_comes_back(self):
        async def go():
            return 42
        self.assertEqual(loopbridge.run(go()), 42)

    def test_timeout_raises_still_running_and_keeps_going(self):
        done = threading.Event()

        async def slow():
            await asyncio.sleep(0.3)
            done.set()
        with self.assertRaises(loopbridge.StillRunning) as cm:
            loopbridge.run(slow(), timeout=0.05)
        self.assertIsInstance(cm.exception, TimeoutError)
        self.assertTrue(done.wait(2))                                   # 没被取消，照样做完了
        self.assertNotIn("失败", loopbridge.friendly_error(cm.exception, "发送失败"))
        self.assertEqual(loopbridge.friendly_error(RuntimeError("不在线"), "发送失败"), "发送失败：不在线")

    def test_flask_handler_redirects_with_flash(self):
        from flask import Flask, get_flashed_messages

        app = Flask(__name__)
        app.secret_key = "x"
        loopbridge.install_flask_handler(app, home="/p")

        @app.post("/p/send")
        def send():
            raise loopbridge.StillRunning()

        @app.get("/p")
        def home():
            return "|".join(get_flashed_messages())
        c = app.test_client()
        r = c.post("/p/send")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/p"))
        self.assertIn("还在处理", c.get("/p").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
