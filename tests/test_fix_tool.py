"""长日bot 修复工具（packaging/fix + packaging/build_fix.py）：打包、签名、Windows 压缩包的样子；
Linux 的 fix.sh 用假的 id / curl / ss / systemctl 跑几种情况（真 Linux、真 Windows 上没跑过，发布前要实际试）。"""
import base64
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
os.environ.setdefault("CHANGRI_BUILD_CACHE", tempfile.mkdtemp())

import build_fix  # noqa: E402


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp())
        cls.key = build_fix.load_key(Path("unused"), test=True)
        cls.files = build_fix.build_site(cls.key, "https://example.test", cls.out)

    def test_outputs(self):
        self.assertEqual(set(self.files), {"fix.sh", "fix/fix.ps1", "fix/fix.ps1.sig", "fix/changri-fix-windows.zip", "fix/index.html"})
        page = self.files["fix/index.html"].read_text(encoding="utf-8")
        self.assertIn("curl -fsSL https://example.test/fix.sh | sudo bash", page)
        self.assertIn("https://example.test/fix/changri-fix-windows.zip", page)
        self.assertNotIn("@", "".join(x for x in page.split() if x.startswith("@") and x.endswith("@")))   # 占位符都填上了

    def test_signature_matches_hosted_script(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        data = self.files["fix/fix.ps1"].read_bytes()
        sig = base64.b64decode(self.files["fix/fix.ps1.sig"].read_text())
        self.key.public_key().verify(sig, data, padding.PKCS1v15(), hashes.SHA256())       # 不抛错 = 对

    def test_windows_zip(self):
        z = zipfile.ZipFile(io.BytesIO(self.files["fix/changri-fix-windows.zip"].read_bytes()))
        names = sorted(z.namelist())
        self.assertEqual(names, ["changri-fix/Fix-Changri.bat", "changri-fix/README.txt", "changri-fix/fix.ps1", "changri-fix/launcher.ps1"])
        self.assertTrue(all(n.isascii() for n in names))                                     # Windows 自带解压认不准 UTF-8 文件名
        bat = z.read("changri-fix/Fix-Changri.bat")
        self.assertTrue(bat.startswith(b"@echo off\r\n"))                                     # 不能带 BOM，要 CRLF
        for n in ("changri-fix/fix.ps1", "changri-fix/launcher.ps1"):
            self.assertTrue(z.read(n).startswith(b"\xef\xbb\xbf"), n)                         # PowerShell 5.1 要 BOM 才认 UTF-8
        self.assertEqual(z.read("changri-fix/fix.ps1"), self.files["fix/fix.ps1"].read_bytes())   # 自带的和官网上的同一份
        launcher = z.read("changri-fix/launcher.ps1").decode("utf-8-sig")
        self.assertIn(build_fix.pubkey_xml(self.key), launcher)
        self.assertIn("$Base = 'https://example.test'", launcher)
        self.assertNotIn("PRIVATE KEY", launcher)

    def test_powershell_parses(self):
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("没有 PowerShell")
        d = Path(tempfile.mkdtemp())
        zipfile.ZipFile(self.files["fix/changri-fix-windows.zip"]).extractall(d)
        for n in ("fix.ps1", "launcher.ps1"):
            p = d / "changri-fix" / n
            r = subprocess.run([pwsh, "-NoProfile", "-Command",
                                f"$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('{p}', [ref]$null, [ref]$e) | Out-Null; $e.Count"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.stdout.strip(), "0", n + r.stderr)


@unittest.skipIf(sys.platform.startswith("win"), "fix.sh 用 bash 跑")
class LinuxScriptTests(unittest.TestCase):
    """fix.sh：用 PATH 里的假命令模拟 root、设置平台的状态、谁占着端口、systemd。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.root = self.tmp / "changri-bot"
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "config.toml").write_text('host = "0.0.0.0"\nport = 6091\n')
        self.make(self.root / "changri", 'echo "changri $*" >> "$STATE/calls"; [ "$1 $2" = "service install" ] && echo up > "$STATE/health"; exit 0')
        self.state = self.tmp / "state"
        self.state.mkdir()
        (self.state / "health").write_text("down")
        (self.state / "listener").write_text("")
        (self.state / "calls").write_text("")
        self.make(self.bin / "id", 'echo 0')
        self.make(self.bin / "sleep", 'exit 0')
        self.make(self.bin / "curl", '[ "$(cat "$STATE/health")" = up ] && { printf ok; exit 0; }; exit 7')
        self.make(self.bin / "ss", 'l="$(cat "$STATE/listener")"; [ -n "$l" ] && echo "LISTEN 0 128 0.0.0.0:6091 0.0.0.0:* users:((\\"x\\",pid=$l,fd=3))"; exit 0')
        self.make(self.bin / "systemctl", 'echo "systemctl $*" >> "$STATE/calls"; case "$1" in restart|start) echo up > "$STATE/health";; esac; exit 0')
        self.make(self.bin / "journalctl", 'exit 0')
        self.make(self.bin / "kill", 'echo "kill $*" >> "$STATE/calls"; : > "$STATE/listener"; exit 0')
        self.script = build_fix.build_site(build_fix.load_key(Path("unused"), test=True), "https://example.test", self.tmp / "out")["fix.sh"]

    def make(self, p: Path, body: str):
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def run_fix(self, unit: bool = False):
        unit_file = self.tmp / "unit.service"
        if unit:
            unit_file.write_text(f"[Service]\nWorkingDirectory={self.root}\nExecStart={self.root}/changri run\n")
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "STATE": str(self.state),
               "CHANGRI_DIR": "" if unit else str(self.root), "CHANGRI_UNIT_FILE": str(unit_file)}
        r = subprocess.run(["bash", str(self.script)], capture_output=True, text=True, errors="replace", env=env, stdin=subprocess.DEVNULL, timeout=60,
                           start_new_session=True)               # 没有终端：问话一律当「不」
        return r.returncode, r.stdout + r.stderr, (self.state / "calls").read_text()

    def test_healthy_changes_nothing(self):
        (self.state / "health").write_text("up")
        code, out, calls = self.run_fix(unit=True)
        self.assertEqual(code, 0, out)
        self.assertIn("正常运行", out)
        self.assertIn("6091", out)                                   # 端口读的是 config.toml
        self.assertEqual(calls, "")

    def test_not_running_with_unit_restarts(self):
        code, out, calls = self.run_fix(unit=True)
        self.assertEqual(code, 0, out)
        self.assertIn("systemctl restart changri-bot", calls)
        self.assertIn("修好了", out)

    def test_not_running_without_unit_and_no_answer(self):
        code, out, calls = self.run_fix(unit=False)
        self.assertEqual(code, 1, out)
        self.assertIn("没设开机自启", out)
        self.assertNotIn("service install", calls)                    # 没人回答：不擅自装开机自启

    def test_other_program_not_killed_without_consent(self):
        (self.state / "listener").write_text("4321")
        code, out, calls = self.run_fix(unit=True)
        self.assertEqual(code, 1, out)
        self.assertIn("被「PID 4321」", out.replace("（PID 4321）", ""))
        self.assertNotIn("kill", calls)

    def test_firewall_off_says_so(self):
        (self.state / "health").write_text("up")
        code, out, calls = self.run_fix(unit=True)
        self.assertIn("没开本机防火墙", out)

    def test_ufw_active_opens_ports_from_file(self):
        (self.state / "health").write_text("up")
        (self.root / "data" / "firewall_ports").write_text("6091 7092\n")
        self.make(self.bin / "ufw", 'echo "ufw $*" >> "$STATE/calls"; [ "$1" = status ] && echo "Status: active"; exit 0')
        code, out, calls = self.run_fix(unit=True)
        self.assertEqual(code, 0, out)
        self.assertIn("ufw allow 6091/tcp", calls)
        self.assertIn("ufw allow 7092/tcp", calls)
        self.assertIn("已放行端口：6091 7092", out)

    def test_ufw_old_version_falls_back_to_default_phone_port(self):
        (self.state / "health").write_text("up")
        self.make(self.bin / "ufw", 'echo "ufw $*" >> "$STATE/calls"; [ "$1" = status ] && echo "Status: active"; exit 0')
        code, out, calls = self.run_fix(unit=True)
        self.assertIn("ufw allow 6091/tcp", calls)                   # 设置平台端口读的是 config.toml
        self.assertIn("ufw allow 5092/tcp", calls)

    def test_installs_sudo_helper_for_service_user(self):
        (self.state / "health").write_text("up")
        self.make(self.bin / "visudo", 'echo "visudo $*" >> "$STATE/calls"; exit 0')
        (self.tmp / "unit.service").write_text(f"[Service]\nUser=changri\nWorkingDirectory={self.root}\n")
        helper, sudoers = self.tmp / "changri-firewall", self.tmp / "sudoers"
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "STATE": str(self.state), "CHANGRI_DIR": "",
               "CHANGRI_UNIT_FILE": str(self.tmp / "unit.service"), "CHANGRI_FW_HELPER": str(helper), "CHANGRI_SUDOERS": str(sudoers)}
        subprocess.run(["bash", str(self.script)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60, start_new_session=True)
        self.assertEqual(helper.read_text(), (Path(__file__).resolve().parent.parent / "src/changri/platform/firewall_helper.sh").read_text())
        self.assertIn(f"changri ALL=(root) NOPASSWD: {helper}", sudoers.read_text())

    def test_not_root(self):
        self.make(self.bin / "id", 'echo 1000')
        code, out, _ = self.run_fix(unit=True)
        self.assertEqual(code, 1)
        self.assertIn("sudo", out)


if __name__ == "__main__":
    unittest.main()
