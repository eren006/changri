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

    @unittest.skipIf(sys.platform.startswith("win"), "Linux 那条路")
    def test_linux_no_firewall_is_not_marked_done(self):
        """防火墙没开时不记「放行过」：以后有人开了 ufw，下次启动还会再放行。"""
        with mock.patch.object(service.os, "geteuid", return_value=1000), mock.patch.object(service.os.path, "exists", return_value=True), \
                mock.patch.object(service.shutil, "which", return_value="/usr/bin/sudo"), \
                mock.patch.object(service, "_run", return_value=Done(0, "\n")) as run:
            self.assertEqual(service.open_firewall([5091], only_if_changed=True), "")
            self.assertFalse((self.tmp / "firewall_done").exists())
            service.open_firewall([5091], only_if_changed=True)
        self.assertEqual(run.call_count, 2)

    def _linux_hint(self, run):
        with mock.patch.object(service.os, "geteuid", return_value=1000), mock.patch.object(service.os.path, "exists", return_value=True), \
                mock.patch.object(service.shutil, "which", return_value="/usr/bin/sudo"), mock.patch.object(service, "_ports", return_value=[5091, 5092]), \
                mock.patch.object(service, "_linux_fw_active", return_value="ufw"), mock.patch.object(service, "_run", side_effect=run):
            return service.firewall_hint()

    @unittest.skipIf(sys.platform.startswith("win"), "Linux 那条路")
    def test_linux_hint_when_helper_installed_but_sudo_broken(self):
        """放行脚本在、sudoers 没装上：以前自检说没问题，现在要提示。"""
        self.assertIn("fix.sh", self._linux_hint(lambda c, check=True: Done(1)))

    @unittest.skipIf(sys.platform.startswith("win"), "Linux 那条路")
    def test_linux_hint_checks_and_reopens(self):
        calls = []
        state = {"open": False}

        def run(c, check=True):
            calls.append(c)
            if "--check" in c:
                return Done(0, "\n" if state["open"] else "5092\n")
            state["open"] = True
            return Done(0, "ufw\n")
        self.assertEqual(self._linux_hint(run), "")                 # 拦着 → 自己再放行一次 → 通了，不用提示
        self.assertIn(["sudo", "-n", service.FW_HELPER, "5092"], calls)

        self.assertIn("5092", self._linux_hint(lambda c, check=True: Done(0, "5092\n")))   # 放行了还拦着：提示

    @unittest.skipIf(sys.platform.startswith("win"), "Linux 那条路")
    def test_linux_hint_old_helper_without_check(self):
        """旧版本装的放行脚本不认 --check（退出码 2）：再放行一遍成功就当通了。"""
        self.assertEqual(self._linux_hint(lambda c, check=True: Done(2) if "--check" in c else Done(0, "ufw\n")), "")

    def test_windows_rule_rebuilt_with_current_ports(self):
        calls = []
        with mock.patch.object(service, "_is_windows", return_value=True), \
                mock.patch.object(service, "_run", side_effect=lambda c, check=True: calls.append(c) or Done(0)):
            msg = service.open_firewall([5091, 5092])
        self.assertIn("Windows 防火墙已放行 5091、5092", msg)
        self.assertIn("localport=5091,5092", calls[-1])

    def test_startup_skips_when_ports_unchanged(self):
        with mock.patch.object(service, "_is_windows", return_value=True), \
                mock.patch.object(service, "_run", return_value=Done(0, "本地端口: 5091,5092")) as run:
            service.open_firewall([5091, 5092], only_if_changed=True)
            n = run.call_count
            self.assertEqual(service.open_firewall([5091, 5092], only_if_changed=True), "")
            self.assertEqual([c.args[0][3] for c in run.call_args_list[n:]], ["show"])   # 端口没变、规则还在：只看一眼，不动防火墙
            run.return_value = Done(0)
            n = run.call_count
            service.open_firewall([5091, 6000], only_if_changed=True)
            self.assertGreater(run.call_count, n)                   # 端口变了：重新放行
            n = run.call_count
            service.open_firewall([5091, 6000])                     # 不带 only_if_changed（改端口、开 HTTPS 时）：照样放行
            self.assertGreater(run.call_count, n)

    def test_startup_puts_back_deleted_windows_rule(self):
        """规则被人删了 / 被安全软件清掉了（以前记过「已放行」）：启动时照样补回来。"""
        rule = {"on": True}

        def run(c, check=True):
            if c[3] == "show":
                return Done(0, "本地端口: 5091,5092") if rule["on"] else Done(1, "没有与指定标准相匹配的规则。")
            if c[3] == "add":
                rule["on"] = True
            return Done(0)
        with mock.patch.object(service, "_is_windows", return_value=True), mock.patch.object(service, "_run", side_effect=run):
            service.open_firewall([5091, 5092], only_if_changed=True)
            self.assertTrue((self.tmp / "firewall_done").exists())
            rule["on"] = False
            self.assertIn("已放行", service.open_firewall([5091, 5092], only_if_changed=True))
        self.assertTrue(rule["on"])

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

    def run_helper(self, *args, env=None):
        env = {**os.environ, "PATH": f"{self.bin}:/usr/bin:/bin", "LOG": str(self.log), **(env or {})}
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

    IPT = ('echo "ipt $*" >> "$LOG"\n'
           'case "$1" in -S) cat "$RULES" ;; -C) grep -q "ipt -I INPUT -p tcp --dport $6 " "$LOG" ;; esac')

    def test_iptables_strict_gets_accept_rule(self):
        """ufw、firewalld 都没开，INPUT 里有全拦的 REJECT（Oracle Cloud 的样子）：插放行规则。"""
        rules = self.tmp / "rules"
        rules.write_text("-P INPUT ACCEPT\n-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT\n-A INPUT -j REJECT --reject-with icmp-host-prohibited\n")
        self.make("iptables", self.IPT)
        env = {"RULES": str(rules)}
        self.assertEqual(self.run_helper("--check", "5091", "5092", env=env).stdout.split(), ["5091", "5092"])
        r = self.run_helper("5091", "5092", env=env)
        self.assertEqual(r.stdout.split(), ["iptables"])
        self.assertIn("ipt -I INPUT -p tcp --dport 5091 -j ACCEPT", self.log.read_text())
        self.assertEqual(self.run_helper("--check", "5091", "5092", env=env).stdout.strip(), "")
        self.run_helper("5091", env=env)
        self.assertEqual(self.log.read_text().count("-I INPUT -p tcp --dport 5091 "), 1)      # 已经有了不重复插

    def test_iptables_open_policy_untouched(self):
        rules = self.tmp / "rules"
        rules.write_text("-P INPUT ACCEPT\n-A INPUT -s 1.2.3.4/32 -j DROP\n")
        self.make("iptables", self.IPT)
        env = {"RULES": str(rules)}
        self.assertEqual(self.run_helper("5091", env=env).stdout.strip(), "")
        self.assertEqual(self.run_helper("--check", "5091", env=env).stdout.strip(), "")
        self.assertNotIn("-I INPUT", self.log.read_text())

    def test_check_ufw(self):
        self.make("ufw", '[ "$1" = status ] || exit 0; echo "Status: active"; [ "$2" = verbose ] && echo "Default: deny (incoming)";'
                         ' echo "5091/tcp                   ALLOW       Anywhere"')
        self.assertEqual(self.run_helper("--check", "5091", "5092").stdout.split(), ["5092"])

    def test_nothing_active(self):
        self.make("ufw", 'echo "Status: inactive"')
        r = self.run_helper("5091")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))


if __name__ == "__main__":
    unittest.main()
