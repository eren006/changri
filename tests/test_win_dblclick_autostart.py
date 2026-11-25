"""Windows 双击 Start-Changri.bat：没装开机自启就自动装（UAC）、交给后台任务，不会出现两个长日bot（platform/winstart.py）。"""
import os
import tempfile

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "windbl.db")

import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from changri.platform import layout, service, supervisor, winstart  # noqa: E402


class Done:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


class Base(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(winstart, "OFF_MARK", d / "autostart_off"),
            mock.patch.object(winstart, "EXIT_MARK", d / "supervise_exit"),
            mock.patch.object(winstart.sys, "platform", "win32"),
            mock.patch.object(layout, "INSTALLED", True),
            mock.patch.dict(os.environ, {"CHANGRI_SERVICE": ""}),
            mock.patch.object(winstart, "_update_pending", return_value=False),
            mock.patch.object(winstart, "_admin_port", return_value=5091),
            mock.patch.object(winstart, "_open"),
            mock.patch.object(winstart.time, "sleep"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def on(self, *values):
        """changri_on 依次返回的值（最后一个一直重复）。"""
        seq = list(values)
        return mock.patch("changri.platform.portcheck.changri_on", side_effect=lambda _p: seq.pop(0) if len(seq) > 1 else seq[0])


class Skip(Base):
    def test_not_windows_or_service_or_dev(self):
        with mock.patch.object(winstart.sys, "platform", "linux"):
            self.assertFalse(winstart.should_try())
        with mock.patch.dict(os.environ, {"CHANGRI_SERVICE": "1"}):
            self.assertFalse(winstart.should_try())          # 开机自启自己起来的
        with mock.patch.object(layout, "INSTALLED", False):
            self.assertFalse(winstart.should_try())          # 源码运行
        self.assertTrue(winstart.should_try())

    def test_removed_by_user(self):
        winstart.OFF_MARK.write_text("x")
        self.assertFalse(winstart.should_try())

    def test_relaunch_by_bat_after_update_or_crash(self):
        winstart.mark_exit()                                 # 看门进程刚退出，bat 马上又拉起：没人看着，不弹 UAC
        self.assertFalse(winstart.should_try())
        old = time.time() - winstart.RECENT_EXIT - 5
        os.utime(winstart.EXIT_MARK, (old, old))
        self.assertTrue(winstart.should_try())
        with mock.patch.object(winstart, "_update_pending", return_value=True):
            self.assertFalse(winstart.should_try())          # 刚装完更新（老版本的看门进程不写 supervise_exit）

    def test_already_running_does_nothing(self):
        with self.on(True), mock.patch.object(winstart, "_task_exists") as te, mock.patch.object(winstart, "_install") as ins:
            self.assertFalse(winstart.handoff())             # 交给看门进程原来的「已经在运行」处理
        te.assert_not_called()
        ins.assert_not_called()


class TaskAlreadyInstalled(Base):
    def test_starts_task_and_closes_window(self):
        with self.on(False, False, True), mock.patch.object(winstart, "_task_exists", return_value=True), \
                mock.patch.object(winstart, "_run_task", return_value=True), mock.patch.object(winstart, "_install") as ins:
            self.assertTrue(winstart.handoff())
        ins.assert_not_called()
        winstart._open.assert_called_once_with(5091)

    def test_cannot_start_task_runs_in_window(self):
        with self.on(False), mock.patch.object(winstart, "_task_exists", return_value=True), \
                mock.patch.object(winstart, "_run_task", return_value=False):
            self.assertFalse(winstart.handoff())

    def test_task_never_comes_up_runs_in_window(self):
        with self.on(False), mock.patch.object(winstart, "_task_exists", return_value=True), \
                mock.patch.object(winstart, "_run_task", return_value=True), mock.patch.object(winstart, "WAIT_UP", 0), \
                mock.patch.object(winstart, "_wait_up", return_value=False):
            self.assertFalse(winstart.handoff())
        winstart._open.assert_not_called()


class Install(Base):
    def test_uac_yes_installs_and_hands_off(self):
        exists = iter([False, True])
        with self.on(False, True), mock.patch.object(winstart, "_task_exists", side_effect=lambda: next(exists)), \
                mock.patch.object(winstart, "_is_admin", return_value=False), \
                mock.patch.object(winstart, "_elevate_install", return_value=0) as el:
            self.assertTrue(winstart.handoff())
        el.assert_called_once()
        winstart._open.assert_called_once_with(5091)

    def test_uac_no_runs_in_window(self):
        with self.on(False), mock.patch.object(winstart, "_task_exists", return_value=False), \
                mock.patch.object(winstart, "_is_admin", return_value=False), \
                mock.patch.object(winstart, "_elevate_install", return_value=None):
            self.assertFalse(winstart.handoff())
        winstart._open.assert_not_called()

    def test_install_failed_runs_in_window(self):
        with self.on(False), mock.patch.object(winstart, "_task_exists", return_value=False), \
                mock.patch.object(winstart, "_is_admin", return_value=False), \
                mock.patch.object(winstart, "_elevate_install", return_value=1):
            self.assertFalse(winstart.handoff())

    def test_nobody_clicks_uac_does_not_block_forever(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        with self.on(False), mock.patch.object(winstart, "_task_exists", return_value=False), \
                mock.patch.object(winstart, "_is_admin", return_value=False), mock.patch.object(winstart, "UAC_WAIT", 0.2), \
                mock.patch.object(winstart, "_elevate_install", side_effect=lambda: gate.wait(5) and 0):
            t0 = time.monotonic()
            self.assertFalse(winstart.handoff())
            self.assertLess(time.monotonic() - t0, 3)

    def test_already_admin_installs_without_uac(self):
        exists = iter([False, True])
        with self.on(False, True), mock.patch.object(winstart, "_task_exists", side_effect=lambda: next(exists)), \
                mock.patch.object(winstart, "_is_admin", return_value=True), \
                mock.patch.object(service, "install", return_value="已设为开机自动运行") as inst, \
                mock.patch.object(winstart, "_elevate_install") as el:
            self.assertTrue(winstart.handoff())
        inst.assert_called_once()
        el.assert_not_called()


class ServiceMarks(Base):
    def test_remove_sets_off_mark_install_clears(self):
        with mock.patch.object(service, "_run", return_value=Done()):
            service._win_uninstall()
        self.assertTrue(winstart.OFF_MARK.exists())
        ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(shell32=types.SimpleNamespace(IsUserAnAdmin=lambda: 1)))
        with mock.patch.dict(sys.modules, {"ctypes": ctypes}), mock.patch.object(service, "_run", return_value=Done()), \
                mock.patch.object(service, "open_firewall", return_value=""):
            service._win_install()
        self.assertFalse(winstart.OFF_MARK.exists())

    def test_status_says_double_click(self):
        with mock.patch.object(service, "_run", return_value=Done(1)):
            self.assertIn("双击 Start-Changri.bat", service._win_status())
            winstart.OFF_MARK.write_text("x")
            self.assertIn("Install-Autostart.bat", service._win_status())


class Supervisor(Base):
    def test_handoff_skips_child_and_marks_exit(self):
        with mock.patch.object(winstart, "handoff", return_value=True), mock.patch.object(supervisor.subprocess, "Popen") as po, \
                mock.patch.object(supervisor.signal, "signal"):
            self.assertEqual(supervisor.run(), 0)
        po.assert_not_called()
        self.assertTrue(winstart.EXIT_MARK.exists())

    def test_handoff_error_falls_back_to_window(self):
        with mock.patch.object(winstart, "handoff", side_effect=OSError("x")):
            self.assertFalse(supervisor._handoff_to_task())


class Scripts(unittest.TestCase):
    def test_elevated_bat_shipped_and_gbk_safe(self):
        p = Path(winstart.PKG_DIR) / "scripts" / "autostart_elevated.bat"
        text = p.read_text(encoding="utf-8")
        text.encode("gbk")                                   # 打包时转 GBK
        self.assertIn("_env.bat", text)
        self.assertIn("service install", text)
        self.assertNotIn("pause", text)                      # 看门进程在等它结束，不能停住


if __name__ == "__main__":
    unittest.main()
