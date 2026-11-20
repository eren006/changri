"""Windows 开机自启的任务计划：不能带「运行 3 天就停止」（ExecutionTimeLimit）；旧版本装的要在 status / 体检里提示重装。"""
import os
import tempfile

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "winauto.db")

import sys  # noqa: E402
import types  # noqa: E402
import unittest  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from changri.platform import service  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
OLD_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings><ExecutionTimeLimit>PT72H</ExecutionTimeLimit></Settings>
</Task>"""
# 旧版本 schtasks /SC ONSTART 建的任务真实导出（11-20 一台用户机器）：没有 ExecutionTimeLimit 这一项 = 默认 PT72H
REAL_OLD_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\\ChangriBot</URI></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <IdleSettings><Duration>PT10M</Duration><WaitTimeout>PT1H</WaitTimeout><StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
  </Settings>
  <Triggers><BootTrigger><StartBoundary>2026-11-20T11:33:00</StartBoundary></BootTrigger></Triggers>
  <Actions Context="Author"><Exec><Command>"C:\\changri\\Start-Changri.bat"</Command><Arguments>--service</Arguments></Exec></Actions>
</Task>"""


class Done:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


def _root(xml: str):
    return ET.fromstring(xml.split("?>", 1)[1])        # 去掉声明（写的是 UTF-16，字符串里解析不了）


class TaskXml(unittest.TestCase):
    def test_no_time_limit_and_boot_as_system(self):
        with mock.patch.object(service, "_win_launcher", return_value=r"C:\长日 & bot\Start-Changri.bat"):
            x = service._win_task_xml()
        r = _root(x)
        self.assertEqual(r.find("t:Settings/t:ExecutionTimeLimit", NS).text, "PT0S")
        self.assertEqual(r.find("t:Settings/t:StopIfGoingOnBatteries", NS).text, "false")
        self.assertIsNotNone(r.find("t:Triggers/t:BootTrigger", NS))
        self.assertEqual(r.find("t:Principals/t:Principal/t:UserId", NS).text, "S-1-5-18")
        self.assertEqual(r.find("t:Actions/t:Exec/t:Command", NS).text, r'"C:\长日 & bot\Start-Changri.bat"')   # & 转义过，带引号
        self.assertEqual(r.find("t:Actions/t:Exec/t:Arguments", NS).text, "--service")
        rep = r.find("t:Triggers/t:TimeTrigger/t:Repetition", NS)                      # 每 5 分钟：不在跑就拉起来
        self.assertEqual(rep.find("t:Interval", NS).text, "PT5M")
        self.assertIsNone(rep.find("t:Duration", NS))                                  # 不设持续时间 = 一直重复
        self.assertEqual(r.find("t:Settings/t:MultipleInstancesPolicy", NS).text, "IgnoreNew")
        self.assertFalse(service._win_task_time_limited(x))
        self.assertFalse(service._win_task_needs_repair(x))

    def test_install_uses_xml_utf16(self):
        seen = {}

        def run(cmd, check=True):
            if cmd[:2] == ["schtasks", "/Create"]:
                i = cmd.index("/XML")
                seen["cmd"] = cmd
                seen["xml"] = Path(cmd[i + 1]).read_bytes()
            return Done()

        ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(shell32=types.SimpleNamespace(IsUserAnAdmin=lambda: 1)))
        with mock.patch.dict(sys.modules, {"ctypes": ctypes}), mock.patch.object(service, "_run", side_effect=run), \
                mock.patch.object(service, "open_firewall", return_value=""):
            service._win_install()
        self.assertNotIn("/SC", seen["cmd"])
        self.assertIn("/F", seen["cmd"])
        self.assertTrue(seen["xml"].startswith(b"\xff\xfe"))                     # UTF-16 带 BOM
        self.assertIn("PT0S", seen["xml"].decode("utf-16"))


class Status(unittest.TestCase):
    def test_limit_detection(self):
        self.assertTrue(service._win_task_time_limited(OLD_XML))
        self.assertFalse(service._win_task_time_limited(OLD_XML.replace("PT72H", "PT0S")))
        self.assertTrue(service._win_task_time_limited(REAL_OLD_XML))            # 没写 = Windows 默认 PT72H
        self.assertTrue(service._win_task_time_limited("<Task><Settings/></Task>"))
        self.assertFalse(service._win_task_time_limited(""))                     # 查不到任务：不提示
        self.assertFalse(service._win_task_time_limited("错误: 系统找不到指定的文件。"))

    def _status(self, xml):
        def run(cmd, check=True):
            return Done(out=xml if "/XML" in cmd else "状态: 正在运行")
        with mock.patch.object(service, "_run", side_effect=run):
            return service._win_status()

    def test_old_install_tells_to_reinstall(self):
        s = self._status(OLD_XML)
        self.assertIn("3 天", s)
        self.assertIn("Install-Autostart.bat", s)

    def test_real_old_export_tells_to_reinstall(self):
        self.assertIn("Install-Autostart.bat", self._status(REAL_OLD_XML))

    def test_new_install_quiet(self):
        s = self._status(OLD_XML.replace("PT72H", "PT0S"))
        self.assertEqual(s, "已设开机自启，现在正在运行。")


class Repair(unittest.TestCase):
    """新版启动时自动换掉旧任务：用户不用知道要重装。"""

    def _repair(self, xml, code=0, register=None, windows=True, service_env="1"):
        calls = []

        def run(cmd, check=True):
            return Done(code=code, out=xml)

        def reg():
            calls.append(1)
            if register:
                raise register

        env = {"CHANGRI_SERVICE": service_env} if service_env else {}
        with mock.patch.object(service, "_is_windows", return_value=windows), mock.patch.object(service, "_run", side_effect=run), \
                mock.patch.object(service, "_win_register", side_effect=reg), mock.patch.dict(os.environ, env, clear=False):
            if not service_env:
                os.environ.pop("CHANGRI_SERVICE", None)
            return service.repair_win_task(), len(calls)

    def test_old_task_rebuilt(self):
        msg, n = self._repair(REAL_OLD_XML)
        self.assertEqual(n, 1)
        self.assertIn("已自动换成新设置", msg)

    def test_new_task_left_alone(self):
        with mock.patch.object(service, "_win_launcher", return_value=r"C:\a\Start-Changri.bat"):
            new = service._win_task_xml()
        self.assertEqual(self._repair(new), (None, 0))

    def test_limit_removed_but_no_restart_trigger_rebuilt(self):
        self.assertEqual(self._repair(OLD_XML.replace("PT72H", "PT0S"))[1], 1)

    def test_no_task_or_not_service(self):
        self.assertEqual(self._repair("错误: 系统找不到指定的文件。", code=1), (None, 0))
        self.assertEqual(self._repair(REAL_OLD_XML, service_env=None), (None, 0))   # 双击窗口：没权限改 SYSTEM 的任务
        self.assertEqual(self._repair(REAL_OLD_XML, windows=False), (None, 0))

    def test_failure_does_not_raise(self):
        msg, n = self._repair(REAL_OLD_XML, register=RuntimeError("拒绝访问"))
        self.assertEqual(n, 1)
        self.assertIn("Install-Autostart.bat", msg)


if __name__ == "__main__":
    unittest.main()
