import base64
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from core import update_pubkey, updater  # noqa: E402


def keypair():
    k = Ed25519PrivateKey.generate()
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return k, pub


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "app"; self.pub = self.tmp / "pub"
        for d in ("core", "plugins", "data", "assets/fonts"):
            (self.root / d).mkdir(parents=True)
        self.pub.mkdir()
        (self.root / "VERSION").write_text("0.1.0")
        (self.root / "bot.py").write_text("print('old')")
        (self.root / "core/a.py").write_text("A=1")
        (self.root / "plugins/old.py").write_text("OLD=1")
        (self.root / "requirements.txt").write_text("x==1\n")
        (self.root / ".env").write_text("SECRET=1")
        (self.root / "assets/fonts/f.otf").write_bytes(b"font")
        db = sqlite3.connect(self.root / "data/changri.db"); db.execute("create table t(v)"); db.execute("insert into t values('before')"); db.commit(); db.close()
        self.key, self.pubkey = keypair()
        self.patches = [mock.patch.object(update_pubkey, "PUBLIC_KEY_B64", self.pubkey),
                        mock.patch.dict(os.environ, {"CHANGRI_UPDATE_ALLOW_FILE": "1", "CHANGRI_UPDATE_URL": (self.pub / "latest.json").as_uri()})]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def release(self, version="0.2.0", extra=None, key=None, tamper=False, req="x==1\n"):
        files = {"VERSION": version, "bot.py": "print('new')", "core/a.py": "A=2", "plugins/new.py": "NEW=1", "requirements.txt": req}
        files.update(extra or {})
        zp = self.pub / "update.zip"
        with zipfile.ZipFile(zp, "w") as z:
            for n, c in files.items():
                z.writestr(n, c)
        data = zp.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        sig = base64.b64encode((key or self.key).sign(updater.sign_message(version, sha))).decode()
        if tamper:
            zp.write_bytes(data + b"x")
        (self.pub / "latest.json").write_text(json.dumps({"version": version, "url": "update.zip", "sha256": sha, "signature": sig, "notes": "n", "size": len(data)}))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and "backups" not in p.parts and p.name != "update_pending.json"}

    def test_version_compare(self):
        self.assertTrue(updater.is_newer("0.10.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.3.0", "0.3.0"))
        self.assertFalse(updater.is_newer("abc", "0.1.0"))

    def test_https_required_in_production(self):
        with mock.patch.dict(os.environ, {"CHANGRI_UPDATE_ALLOW_FILE": "0"}):
            with self.assertRaises(updater.UpdateError):
                updater.fetch_manifest("http://example.com/latest.json")

    def test_full_update(self):
        self.release()
        info = updater.check(root=self.root)
        self.assertTrue(info["newer"])
        res = updater.update_now(self.root)
        self.assertEqual((res["from"], res["to"]), ("0.1.0", "0.2.0"))
        self.assertEqual(updater.current_version(self.root), "0.2.0")
        self.assertEqual((self.root / "core/a.py").read_text(), "A=2")
        self.assertTrue((self.root / "plugins/new.py").exists())
        self.assertFalse((self.root / "plugins/old.py").exists())            # 新版本里没有的插件被清掉
        self.assertEqual((self.root / ".env").read_text(), "SECRET=1")       # 不碰 .env 和字体
        self.assertEqual((self.root / "assets/fonts/f.otf").read_bytes(), b"font")
        self.assertTrue((self.root / "data/update_pending.json").exists())
        b = Path(res["backup"])
        self.assertTrue((b / "code.zip").exists() and (b / "changri.db").exists())
        with self.assertRaises(updater.UpdateError):                          # 再查一次：已是最新
            updater.update_now(self.root)

    def test_tampered_file_rejected_and_nothing_changed(self):
        self.release(tamper=True)
        before = self.snapshot()
        with self.assertRaises(updater.UpdateError):
            updater.update_now(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_wrong_signature_rejected(self):
        other, _ = keypair()
        self.release(key=other)
        before = self.snapshot()
        with self.assertRaises(updater.UpdateError) as cm:
            updater.update_now(self.root)
        self.assertIn("签名", str(cm.exception))
        self.assertEqual(self.snapshot(), before)

    def test_path_traversal_rejected(self):
        self.release(extra={"../evil.txt": "x"})
        before = self.snapshot()
        with self.assertRaises(updater.UpdateError):
            updater.update_now(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_ignored_paths_are_not_written(self):
        self.release(extra={"data/changri.db": "hacked", ".env": "hacked", "venv/x.py": "x", "start.bat": "x"})
        updater.update_now(self.root)
        self.assertEqual((self.root / ".env").read_text(), "SECRET=1")
        self.assertNotEqual((self.root / "data/changri.db").read_bytes(), b"hacked")
        self.assertFalse((self.root / "start.bat").exists())

    def test_failed_dependency_install_rolls_back(self):
        self.release(req="x==2\n")
        before = self.snapshot()
        with mock.patch.object(updater, "_pip_install", side_effect=updater.UpdateError("装不上")):
            with self.assertRaises(updater.UpdateError):
                updater.update_now(self.root)
        self.assertEqual(self.snapshot(), before)                             # 代码原样恢复，新插件也被清掉
        self.assertFalse((self.root / "data/update_pending.json").exists())

    def test_startup_guard_rolls_back_after_repeated_crashes(self):
        self.release()
        updater.update_now(self.root)
        db = sqlite3.connect(self.root / "data/changri.db"); db.execute("insert into t values('after')"); db.commit(); db.close()
        for _ in range(updater.MAX_ATTEMPTS):
            self.assertIsNone(updater.startup_guard(self.root))
        msg = updater.startup_guard(self.root)                                # 第 4 次起不来：回滚
        self.assertIn("已自动回滚", msg)
        self.assertEqual(updater.current_version(self.root), "0.1.0")
        self.assertTrue((self.root / "plugins/old.py").exists() and not (self.root / "plugins/new.py").exists())
        rows = [r[0] for r in sqlite3.connect(self.root / "data/changri.db").execute("select v from t")]
        self.assertEqual(rows, ["before"])
        self.assertTrue((self.root / "data/update_failed.txt").exists())
        self.assertFalse((self.root / "data/update_pending.json").exists())

    def test_confirm_healthy_clears_pending(self):
        self.release()
        updater.update_now(self.root)
        updater.startup_guard(self.root)
        updater.confirm_healthy(self.root / "data")
        for _ in range(10):
            self.assertIsNone(updater.startup_guard(self.root))
        self.assertEqual(updater.current_version(self.root), "0.2.0")


if __name__ == "__main__":
    unittest.main()
