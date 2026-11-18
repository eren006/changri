"""看门进程：重启、换版本、崩溃重拉、新版本起不来回滚。子进程用几行假的 python 代替。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from changri.core import updater  # noqa: E402
from changri.platform import layout, supervisor  # noqa: E402


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.counter = self.tmp / "n"
        self.counter.write_text("0")
        (self.tmp / "versions" / "0.1.0").mkdir(parents=True)
        (self.tmp / "versions" / "0.2.0").mkdir(parents=True)
        (self.tmp / "current").write_text("0.1.0")
        self.patches = [mock.patch.object(supervisor, "CRASH_DELAY", 0), mock.patch.object(supervisor, "_open_browser_once", lambda: None),
                        mock.patch.object(supervisor, "_wait_for_user", lambda: None),
                        mock.patch.object(supervisor, "_already_running", lambda: None),
                        mock.patch.object(layout, "INSTALLED", True), mock.patch.object(layout, "CURRENT", self.tmp / "current"),
                        mock.patch.object(layout, "VERSIONS", self.tmp / "versions"),
                        mock.patch.object(updater, "confirm_healthy", lambda *a: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def child(self, script: str):
        """script 里能用 n（第几次被拉起，从 1 开始）。"""
        code = f"from pathlib import Path; p=Path({str(self.counter)!r}); n=int(p.read_text())+1; p.write_text(str(n))\n{script}"
        return mock.patch.object(supervisor, "_child_cmd", lambda: [sys.executable, "-c", code])

    def runs(self) -> int:
        return int(self.counter.read_text())

    def test_clean_exit_stops(self):
        with self.child("raise SystemExit(0)"):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), 1)

    def test_restart_code_relaunches_child(self):
        with self.child("raise SystemExit(3 if n < 3 else 0)"):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), 3)

    def test_version_switch_asks_launcher_to_relaunch(self):
        cur = str(self.tmp / "current")
        with self.child(f"Path({cur!r}).write_text('0.2.0'); raise SystemExit(3)"):
            self.assertEqual(supervisor.run(), supervisor.RELAUNCH)
        self.assertEqual(self.runs(), 1)

    def test_crash_is_retried(self):
        with self.child("raise SystemExit(1 if n < 3 else 0)"):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), 3)

    def test_crash_loop_gives_up(self):
        """一启动就崩（比如端口被占）：连续 MAX_QUICK_CRASHES 次后不再拉起，退出码 0，老的 .bat 见到 0 也会停。"""
        with self.child("raise SystemExit(1)"):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), supervisor.MAX_QUICK_CRASHES)

    def test_long_run_resets_crash_count(self):
        """跑满 HEALTHY_AFTER 秒后才崩的，不算连续快速崩溃，计数清零接着拉起。"""
        with self.child("raise SystemExit(1 if n < 8 else 0)"), mock.patch.object(supervisor, "HEALTHY_AFTER", 0):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), 8)

    def test_restart_resets_crash_count(self):
        """中间夹着一次人为重启（退出码 3），计数清零。"""
        with self.child("raise SystemExit(3 if n == 4 else (0 if n >= 9 else 1))"):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), 9)

    def test_port_busy_stops_at_once(self):
        """子进程说端口被别的程序占了（PORT_BUSY）：不再拉起，退出码 0。"""
        with self.child(f"raise SystemExit({supervisor.PORT_BUSY})"):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(self.runs(), 1)

    def test_already_running_does_not_launch(self):
        """端口上已经有一个长日bot：一次都不拉起、帮他打开设置平台、不停住等回车，退出码 0。"""
        opened = []
        with self.child("raise SystemExit(1)"), mock.patch.object(supervisor, "_already_running", lambda: 5091), \
                mock.patch.object(supervisor, "_open_running", opened.append), \
                mock.patch.object(supervisor, "_wait_for_user", lambda: self.fail("不该停住等回车")):
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual((self.runs(), opened), (0, [5091]))

    def test_failing_new_version_rolls_back(self):
        calls = []

        def fake_rollback(*a):
            calls.append(1)
            return "已自动回到 0.1.0" if len(calls) == 3 else None
        with self.child("raise SystemExit(1)"), mock.patch.object(updater, "rollback_if_failing", fake_rollback):
            self.assertEqual(supervisor.run(), supervisor.RELAUNCH)
        self.assertEqual(self.runs(), 3)


class AlreadyRunningTests(unittest.TestCase):
    """_already_running 真去连一个本地 HTTP 服务：/healthz 回 ok 才算长日bot。"""

    def serve(self, body: bytes, status: int = 200):
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status if self.path == "/healthz" else 404)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def check(self, port: int):
        from changri import config
        with mock.patch.object(config, "admin_port", lambda: port):
            return supervisor._already_running()

    def test_changri_on_port(self):
        port = self.serve(b"ok")
        self.assertEqual(self.check(port), port)

    def test_other_program_on_port(self):
        self.assertIsNone(self.check(self.serve(b"<html>hello</html>")))
        self.assertIsNone(self.check(self.serve(b"ok", status=500)))

    def test_nothing_on_port(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertIsNone(self.check(port))


if __name__ == "__main__":
    unittest.main()
