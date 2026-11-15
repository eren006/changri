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

    def test_failing_new_version_rolls_back(self):
        calls = []

        def fake_rollback(*a):
            calls.append(1)
            return "已自动回到 0.1.0" if len(calls) == 3 else None
        with self.child("raise SystemExit(1)"), mock.patch.object(updater, "rollback_if_failing", fake_rollback):
            self.assertEqual(supervisor.run(), supervisor.RELAUNCH)
        self.assertEqual(self.runs(), 3)


if __name__ == "__main__":
    unittest.main()
