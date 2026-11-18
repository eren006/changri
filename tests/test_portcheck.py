"""启动前查端口：被占时说清楚是谁、怎么办，不自动换端口；看门进程见到 PORT_BUSY 不再重启；自检认得出占用者不是长日bot。"""
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "p.db")

from changri.platform import portcheck  # noqa: E402


def listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


class PortCheckTests(unittest.TestCase):
    def test_can_bind(self):
        s, port = listener()
        try:
            self.assertFalse(portcheck.can_bind("127.0.0.1", port))
        finally:
            s.close()
        self.assertTrue(portcheck.can_bind("127.0.0.1", port))

    def test_changri_on_needs_healthz_ok(self):
        s, port = listener()                    # 只 listen 不回 HTTP：不是长日bot
        try:
            with mock.patch("urllib.request.urlopen", side_effect=OSError):
                self.assertFalse(portcheck.changri_on(port))
        finally:
            s.close()

    def test_windows_parser(self):
        netstat = ("\n  Proto  Local Address          Foreign Address        State           PID\n"
                   "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       900\n"
                   "  TCP    0.0.0.0:5091           0.0.0.0:0              LISTENING       4321\n"
                   "  TCP    127.0.0.1:50910        0.0.0.0:0              LISTENING       77\n")
        tasklist = '"python.exe","4321","Console","1","52,120 K"\n'
        with mock.patch.object(portcheck, "_out", side_effect=[netstat, tasklist]):
            self.assertEqual(portcheck._windows(5091), "python.exe（PID 4321）")
        with mock.patch.object(portcheck, "_out", return_value=netstat):
            self.assertEqual(portcheck._windows(8080), "")

    def test_linux_parser(self):
        ss = 'LISTEN 0 2048 0.0.0.0:5091 0.0.0.0:* users:(("nginx",pid=812,fd=6),("nginx",pid=813,fd=6))\n'
        with mock.patch.object(portcheck, "_out", return_value=ss):
            self.assertEqual(portcheck._linux(5091), "nginx（PID 812）")
        with mock.patch.object(portcheck, "_out", return_value="LISTEN 0 128 0.0.0.0:5091 0.0.0.0:*\n"):   # 没权限看进程
            self.assertEqual(portcheck._linux(5091), "")

    def test_mac_parser(self):
        with mock.patch.object(portcheck, "_out", return_value="p555\ncPython\nf3\n"):
            self.assertEqual(portcheck._mac(5091), "Python（PID 555）")

    def test_who_listens_never_raises(self):
        with mock.patch.object(portcheck, "_out", side_effect=RuntimeError("boom")):
            self.assertEqual(portcheck.who_listens(5091), "")

    def test_busy_message(self):
        m = portcheck.busy_message(5091, "nginx（PID 812）")
        for s in ("5091", "nginx（PID 812）", "data/config.toml", "防火墙"):
            self.assertIn(s, m)
        self.assertIn("被别的程序占用了", portcheck.busy_message(5091))
        self.assertIn("已经在运行", portcheck.busy_message(5091, changri=True))


class FixHintTests(unittest.TestCase):
    def test_busy_message_points_to_fix_tool(self):
        with mock.patch.object(portcheck, "_win", lambda: True):
            m = portcheck.busy_message(5091, "foo.exe（PID 5）")
            self.assertIn(portcheck.FIX_PAGE, m)
            self.assertIn("Fix-Changri.bat", m)
        with mock.patch.object(portcheck, "_win", lambda: False):
            self.assertIn(portcheck.FIX_LINUX, portcheck.busy_message(5091))


class RunStartupTests(unittest.TestCase):
    """changri run 启动前：端口被别的程序占 → 打印原因、记一条「配置」报错、退出码 PORT_BUSY；是长日bot 自己 → 退出码 0。"""

    def setUp(self):
        import changri.__main__ as m
        self.m = m
        from changri import config
        self.patches = [mock.patch.object(config, "ensure", lambda: None),
                        mock.patch.object(config, "get", lambda k: "0.0.0.0"),
                        mock.patch.object(config, "admin_port", lambda: 5091)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_free_port_starts(self):
        with mock.patch.object(portcheck, "can_bind", return_value=True):
            self.assertIsNone(self.m._port_busy())

    def test_other_program(self):
        from changri.core import errlog
        with mock.patch.object(portcheck, "can_bind", return_value=False), \
             mock.patch.object(portcheck, "changri_on", return_value=False), \
             mock.patch.object(portcheck, "who_listens", return_value="nginx（PID 812）"), \
             mock.patch.object(errlog, "record") as rec, mock.patch("builtins.print"):
            self.assertEqual(self.m._port_busy(), portcheck.PORT_BUSY)
        kind, source, msg = rec.call_args[0]
        self.assertEqual(kind, "config")
        self.assertIn("nginx（PID 812）", msg)

    def test_changri_already_running(self):
        from changri.core import errlog
        with mock.patch.object(portcheck, "can_bind", return_value=False), \
             mock.patch.object(portcheck, "changri_on", return_value=True), \
             mock.patch.object(errlog, "record") as rec, mock.patch("builtins.print"):
            self.assertEqual(self.m._port_busy(), 0)
        rec.assert_not_called()


class DoctorTests(unittest.TestCase):
    def _admin_row(self, listening, changri, who=""):
        from changri import config
        from changri.platform import doctor
        with mock.patch.object(config, "admin_port", lambda: 5091), \
             mock.patch.object(doctor, "_listening", side_effect=lambda p: listening if p == 5091 else False), \
             mock.patch.object(doctor, "_reach", return_value=True), \
             mock.patch.object(portcheck, "changri_on", return_value=changri), \
             mock.patch.object(portcheck, "who_listens", return_value=who), \
             mock.patch("changri.core.netcheck.public_ip", return_value=""):
            rows = doctor.checks()
        return next(r for r in rows if r[1].startswith("设置平台"))

    def test_running(self):
        self.assertEqual(self._admin_row(True, True)[:2], (True, "设置平台（5091 端口）"))

    def test_taken_by_other(self):
        ok, _, msg = self._admin_row(True, False, "nginx（PID 812）")
        self.assertFalse(ok)
        self.assertIn("nginx（PID 812）", msg)

    def test_not_running(self):
        ok, _, msg = self._admin_row(False, False)
        self.assertFalse(ok)
        self.assertIn("没在运行", msg)


if __name__ == "__main__":
    unittest.main()
