"""本机防火墙放行（platform/service.open_firewall / firewall_hint）和放行脚本 firewall_helper.sh。"""
import os
import tempfile

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "fw.db")

import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from changri.platform import service  # noqa: E402


class Done:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


class OpenFirewallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(service.layout, "INSTALLED", True),
                        mock.patch.object(service, "PORTS_FILE", self.tmp / "firewall_ports"),
                        mock.patch.object(service, "DONE_FILE", self.tmp / "firewall_done")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_dev_does_nothing(self):
        with mock.patch.object(service.layout, "INSTALLED", False), mock.patch.object(service, "_run") as run:
            self.assertEqual(service.open_firewall([5091]), "")
        run.assert_not_called()

    def test_ports_include_https_when_on(self):
        from changri.core import httpsproxy
        with mock.patch.object(httpsproxy, "enabled", return_value=True):
            ports = service._ports()
        self.assertEqual(ports[2:], [80, 443, httpsproxy.ADMIN_HTTPS_PORT, httpsproxy.phone_https_port(ports[1])])

    @unittest.skipIf(sys.platform.startswith("win"), "Linux 那条路")
    def test_linux_non_root_uses_sudo_helper_and_writes_ports_file(self):
        calls = []
        with mock.patch.object(service.os, "geteuid", return_value=1000), mock.patch.object(service.os.path, "exists", return_value=True), \
                mock.patch.object(service.shutil, "which", return_value="/usr/bin/sudo"), \
                mock.patch.object(service, "_run", side_effect=lambda c, check=True: calls.append(c) or Done(0, "ufw\n")):
            msg = service.open_firewall([5091, 5092])
        self.assertEqual(calls, [["sudo", "-n", service.FW_HELPER, "5091", "5092"]])
        self.assertIn("ufw", msg)
        self.assertEqual((self.tmp / "firewall_ports").read_text().split(), ["5091", "5092"])

    @unittest.skipIf(sys.platform.startswith("win"), "Linux 那条路")
    def test_linux_non_root_without_helper_gives_hint_when_firewall_on(self):
        with mock.patch.object(service.os, "geteuid", return_value=1000), mock.patch.object(service.os.path, "exists", return_value=False), \
                mock.patch.object(service, "_linux_fw_active", return_value="ufw"), mock.patch.object(service, "_ports", return_value=[5091, 5092]):
            self.assertEqual(service.open_firewall([5091]), "")
            hint = service.firewall_hint()
        self.assertIn("fix.sh", hint)
        self.assertIn("5091,5092", hint)
        with mock.patch.object(service.os, "geteuid", return_value=1000), mock.patch.object(service.os.path, "exists", return_value=False), \
                mock.patch.object(service, "_linux_fw_active", return_value=""):
            self.assertEqual(service.firewall_hint(), "")

    def test_windows_rule_rebuilt_with_current_ports(self):
        calls = []
        with mock.patch.object(service, "_is_windows", return_value=True), \
                mock.patch.object(service, "_run", side_effect=lambda c, check=True: calls.append(c) or Done(0)):
            msg = service.open_firewall([5091, 5092])
        self.assertIn("Windows 防火墙已放行 5091、5092", msg)
        self.assertIn("localport=5091,5092", calls[-1])

    def test_startup_skips_when_ports_unchanged(self):
        with mock.patch.object(service, "_is_windows", return_value=True), \
                mock.patch.object(service, "_run", return_value=Done(0)) as run:
            service.open_firewall([5091, 5092], only_if_changed=True)
            n = run.call_count
            self.assertEqual(service.open_firewall([5091, 5092], only_if_changed=True), "")
            self.assertEqual(run.call_count, n)                     # 端口没变：不再动防火墙
            service.open_firewall([5091, 6000], only_if_changed=True)
            self.assertGreater(run.call_count, n)                   # 端口变了：重新放行
            n = run.call_count
            service.open_firewall([5091, 6000])                     # 不带 only_if_changed（改端口、开 HTTPS 时）：照样放行
            self.assertGreater(run.call_count, n)

    def test_failure_is_retried_next_start(self):
        def run(c, check=True):
            return Done(1) if c[3] == "delete" else Done(0, "Rule Name: ChangriBot")
        with mock.patch.object(service, "_is_windows", return_value=True), mock.patch.object(service, "_run", side_effect=run):
            service.open_firewall([5091], only_if_changed=True)
        self.assertFalse((self.tmp / "firewall_done").exists())

    def test_windows_not_admin_keeps_old_rule(self):
        def run(c, check=True):
            return Done(1) if c[3] == "delete" else Done(0, "Rule Name: ChangriBot")
        with mock.patch.object(service, "_is_windows", return_value=True), mock.patch.object(service, "_run", side_effect=run) as r:
            self.assertEqual(service.open_firewall([5091]), "")
        self.assertFalse(any(c[3] == "add" for c in (a.args[0] for a in r.call_args_list)))

    def test_windows_hint_when_rule_missing_ports(self):
        with mock.patch.object(service, "_is_windows", return_value=True), mock.patch.object(service, "_ports", return_value=[5091, 5092]), \
                mock.patch.object(service, "_run", return_value=Done(0, "LocalPort: 5091")):
            self.assertIn("localport=5091,5092", service.firewall_hint())
        with mock.patch.object(service, "_is_windows", return_value=True), mock.patch.object(service, "_ports", return_value=[5091, 5092]), \
                mock.patch.object(service, "_run", return_value=Done(0, "LocalPort: 5091,5092")):
            self.assertEqual(service.firewall_hint(), "")


@unittest.skipIf(sys.platform.startswith("win") or not shutil.which("sh"), "要 sh")
class HelperScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "log"
        self.log.write_text("")

    def make(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def run_helper(self, *args):
        env = {**os.environ, "PATH": f"{self.bin}:/usr/bin:/bin", "LOG": str(self.log)}
        return subprocess.run(["sh", str(service.FW_HELPER_SRC), *args], capture_output=True, text=True, env=env)

    def test_rejects_bad_ports(self):
        self.make("ufw", 'echo "ufw $*" >> "$LOG"; [ "$1" = status ] && echo "Status: active"; exit 0')
        for bad in (["22"], ["abc"], ["5091", "1;rm"], ["70000"], []):
            self.assertEqual(self.run_helper(*bad).returncode, 2, bad)
        self.assertEqual(self.log.read_text(), "")

    def test_ufw_and_firewalld(self):
        self.make("ufw", 'echo "ufw $*" >> "$LOG"; [ "$1" = status ] && echo "Status: active"; exit 0')
        self.make("firewall-cmd", 'echo "fw $*" >> "$LOG"; exit 0')
        r = self.run_helper("5091", "443")
        self.assertEqual(r.stdout.split(), ["ufw", "firewalld"])
        log = self.log.read_text()
        for s in ("ufw allow 5091/tcp", "ufw allow 443/tcp", "fw --permanent --add-port=5091/tcp", "fw --reload"):
            self.assertIn(s, log)

    def test_nothing_active(self):
        self.make("ufw", 'echo "Status: inactive"')
        r = self.run_helper("5091")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))


if __name__ == "__main__":
    unittest.main()
