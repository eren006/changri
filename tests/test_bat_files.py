"""Windows .bat：打包时存成 GBK + CRLF，不切 65001（UTF-8 下 cmd 会把含中文的行读错位）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import build  # noqa: E402

BATS = sorted([*(ROOT / "packaging").rglob("*.bat"), *(ROOT / "src").rglob("*.bat")])


class BatFiles(unittest.TestCase):
    def test_found(self):
        names = {p.name for p in BATS}
        self.assertTrue({"_env.bat", "Install-HTTPS.bat", "install_caddy.bat", "Fix-Changri.bat"} <= names)

    def test_every_bat_packs_as_gbk_crlf(self):
        for p in BATS:
            with self.subTest(p.name):
                text = p.read_text(encoding="utf-8")
                data = build.bat_bytes(text)                     # 有 chcp 65001 或 GBK 存不下的字符会 sys.exit
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
                self.assertEqual(data.decode("gbk").replace("\r\n", "\n"), text.replace("\r\n", "\n"))

    def test_rejects_chcp_65001(self):
        with self.assertRaises(SystemExit):
            build.bat_bytes("@echo off\nchcp 65001 >nul\n")

    def test_app_files_converts_bat(self):
        files = build.app_files("rt-test", None)
        bat = files["changri/scripts/install_caddy.bat"]
        self.assertIn("管理员", bat.decode("gbk"))

    def test_env_bat_names_missing_file(self):
        text = (ROOT / "packaging" / "windows" / "_env.bat").read_text(encoding="utf-8")
        for label in (":no_current", ":no_runtime_id", ":no_python"):
            self.assertIn(label, text)
            self.assertIn("goto " + label[1:], text)


if __name__ == "__main__":
    unittest.main()
