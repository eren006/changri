"""在线更新：装到新版本目录、切 current、连续起不来自动回滚；签名不对、路径不安全一律拒绝。"""
import base64
import hashlib
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from changri.core import update_pubkey, updater  # noqa: E402
from changri.platform import layout  # noqa: E402

RT_OLD, RT_NEW = "r0000aaaa", "r1111bbbb"


def keypair():
    k = Ed25519PrivateKey.generate()
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return k, pub


def db_value(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT v FROM t").fetchone()[0]
    finally:
        c.close()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root, self.pub, self.data = self.tmp / "app", self.tmp / "pub", self.tmp / "app" / "data"
        (self.root / "versions" / "0.1.0" / "changri").mkdir(parents=True)
        (self.root / "versions" / "0.1.0" / "RUNTIME").write_text(RT_OLD)
        (self.root / "runtime" / RT_OLD).mkdir(parents=True)
        (self.root / "current").write_text("0.1.0\n")
        self.data.mkdir()
        (self.data / "config.toml").write_text("port = 5091\n")
        for db in (self.data / "changri.db", self.data / "bots" / "123456" / "changri.db"):
            db.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(db); c.execute("create table t(v)"); c.execute("insert into t values('before')"); c.commit(); c.close()
        self.pub.mkdir()
        self.key, self.pubkey = keypair()
        self.patches = [
            mock.patch.object(update_pubkey, "PUBLIC_KEY_B64", self.pubkey),
            mock.patch.dict(os.environ, {"CHANGRI_UPDATE_ALLOW_FILE": "1", "CHANGRI_UPDATE_URL": (self.pub / "latest.json").as_uri()}),
            mock.patch.object(layout, "APP_ROOT", self.root), mock.patch.object(layout, "VERSIONS", self.root / "versions"),
            mock.patch.object(layout, "RUNTIMES", self.root / "runtime"), mock.patch.object(layout, "CURRENT", self.root / "current"),
            mock.patch.object(layout, "INSTALLED", True), mock.patch.object(layout, "platform_tag", return_value="linux-x64"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def sign(self, msg: bytes) -> str:
        return base64.b64encode(self.key.sign(msg)).decode()

    def release(self, version="0.2.0", runtime=RT_OLD, with_runtime=True, files=None, tamper=False, bad_sig=False, runtime_plats=("linux-x64",)):
        files = files if files is not None else {"changri/__init__.py": "", "changri/VERSION": version, "changri_api.py": "", "RUNTIME": runtime}
        app = self.pub / f"app-{version}.zip"
        with zipfile.ZipFile(app, "w") as z:
            for n, c in files.items():
                z.writestr(n, c)
        data = app.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        sig = self.sign(updater.sign_message(version if not bad_sig else "9.9.9", sha))
        if tamper:
            app.write_bytes(data + b"x")
        manifest = {"version": version, "notes": "n", "runtime": runtime,
                    "app": {"url": app.name, "sha256": sha, "signature": sig, "size": len(data)}, "runtimes": {}}
        if with_runtime:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as t:
                info = tarfile.TarInfo("bin/python3"); body = b"#!/bin/sh\n"; info.size = len(body); info.mode = 0o755
                t.addfile(info, io.BytesIO(body))
            rt = self.pub / f"runtime-{runtime}.tar.gz"
            rt.write_bytes(buf.getvalue())
            rsha = hashlib.sha256(buf.getvalue()).hexdigest()
            for plat in runtime_plats:
                manifest["runtimes"][plat] = {"url": rt.name, "sha256": rsha, "signature": self.sign(updater.runtime_sign_message(runtime, plat, rsha)),
                                              "size": len(buf.getvalue())}
        (self.pub / "latest.json").write_text(json.dumps(manifest))

    def assert_untouched(self):
        self.assertEqual((self.root / "current").read_text().strip(), "0.1.0")
        self.assertFalse((self.data / "update_pending.json").exists())
        self.assertEqual([p.name for p in (self.root / "versions").iterdir()], ["0.1.0"])

    def test_version_compare(self):
        self.assertTrue(updater.is_newer("0.10.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.3.0", "0.3.0"))
        self.assertFalse(updater.is_newer("abc", "0.1.0"))

    def test_https_required_in_production(self):
        with mock.patch.dict(os.environ, {"CHANGRI_UPDATE_ALLOW_FILE": "0"}):
            with self.assertRaises(updater.UpdateError):
                updater.fetch_manifest("http://example.com/latest.json")

    def test_dev_checkout_cannot_update(self):
        with mock.patch.object(layout, "INSTALLED", False):
            with self.assertRaisesRegex(updater.UpdateError, "开发环境"):
                updater.update_now(self.data)

    def test_full_update_same_runtime(self):
        self.release()
        info = updater.check()
        self.assertTrue(info["newer"])
        self.assertFalse(info["runtime_needed"])
        res = updater.update_now(self.data)
        self.assertEqual((res["from"], res["to"]), ("0.1.0", "0.2.0"))
        self.assertEqual((self.root / "current").read_text().strip(), "0.2.0")
        self.assertEqual((self.root / "versions" / "0.2.0" / "changri" / "VERSION").read_text(), "0.2.0")
        self.assertTrue((self.root / "versions" / "0.1.0").is_dir())                # 旧版本留着，回滚用
        self.assertEqual(json.loads((self.data / "update_pending.json").read_text())["to"], "0.2.0")
        bdir = Path(res["backup"])
        self.assertEqual(db_value(bdir / "changri.db"), "before")
        self.assertEqual(db_value(bdir / "bots" / "123456" / "changri.db"), "before")   # 每个机器人的库都备份了
        self.assertEqual(list(self.root.glob(".download_*")), [])                    # 下载的包装完就删
        self.assertEqual((self.data / "config.toml").read_text(), "port = 5091\n")  # data 不碰

    def test_new_runtime_downloaded_and_old_kept_while_in_use(self):
        self.release(runtime=RT_NEW)
        self.assertTrue(updater.check()["runtime_needed"])
        updater.update_now(self.data)
        self.assertTrue((self.root / "runtime" / RT_NEW / "bin" / "python3").exists())
        self.assertTrue(os.access(self.root / "runtime" / RT_NEW / "bin" / "python3", os.X_OK))
        self.assertTrue((self.root / "runtime" / RT_OLD).is_dir())                  # 旧版本还在用它，不删

    def test_no_runtime_for_this_platform(self):
        self.release(runtime=RT_NEW, runtime_plats=("windows-x64",))
        with self.assertRaisesRegex(updater.UpdateError, "linux-x64"):
            updater.update_now(self.data)
        self.assert_untouched()

    def test_tampered_or_wrong_signature_rejected(self):
        for kw in ({"tamper": True}, {"bad_sig": True}):
            self.release(**kw)
            with self.assertRaises(updater.UpdateError):
                updater.update_now(self.data)
            self.assert_untouched()

    def test_path_traversal_rejected(self):
        self.release(files={"changri/__init__.py": "", "changri/VERSION": "0.2.0", "RUNTIME": RT_OLD, "../evil.py": "x"})
        with self.assertRaisesRegex(updater.UpdateError, "不安全"):
            updater.update_now(self.data)
        self.assertFalse((self.root / "evil.py").exists())

    def test_version_mismatch_rejected(self):
        self.release(files={"changri/__init__.py": "", "changri/VERSION": "0.3.0", "RUNTIME": RT_OLD})
        with self.assertRaisesRegex(updater.UpdateError, "版本号"):
            updater.update_now(self.data)
        self.assert_untouched()

    def test_rollback_after_repeated_crashes(self):
        self.release()
        updater.update_now(self.data)
        for db in (self.data / "changri.db", self.data / "bots" / "123456" / "changri.db"):
            c = sqlite3.connect(db); c.execute("update t set v='migrated'"); c.commit(); c.close()     # 新版本改过库
        self.assertIsNone(updater.rollback_if_failing(self.data))
        self.assertIsNone(updater.rollback_if_failing(self.data))
        msg = updater.rollback_if_failing(self.data)                                      # 第 3 次：回滚
        self.assertIn("0.1.0", msg)
        self.assertEqual((self.root / "current").read_text().strip(), "0.1.0")
        self.assertEqual(db_value(self.data / "changri.db"), "before")
        self.assertEqual(db_value(self.data / "bots" / "123456" / "changri.db"), "before")
        self.assertTrue((self.data / "update_failed.txt").exists())
        self.assertFalse((self.data / "update_pending.json").exists())

    def test_confirm_healthy_clears_pending(self):
        self.release()
        updater.update_now(self.data)
        updater.confirm_healthy(self.data)
        self.assertIsNone(updater.rollback_if_failing(self.data))
        self.assertEqual((self.root / "current").read_text().strip(), "0.2.0")

    def test_prune_keeps_recent_versions(self):
        for v in ("0.0.1", "0.0.2", "0.0.3"):
            (self.root / "versions" / v).mkdir()
            (self.root / "versions" / v / "RUNTIME").write_text(RT_OLD)
        (self.root / "runtime" / "r2222cccc").mkdir()                                   # 没有版本在用
        removed = layout.prune()
        self.assertEqual(sorted(p.name for p in (self.root / "versions").iterdir()), ["0.0.2", "0.0.3", "0.1.0"])
        self.assertIn("runtime/r2222cccc", removed)
        self.assertTrue((self.root / "runtime" / RT_OLD).is_dir())


if __name__ == "__main__":
    unittest.main()
