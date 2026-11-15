"""大的媒体文件（音频、字体、大图）安装后在运行环境的 changri-media/ 里：包里找不到就去那里找。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "m.db"))
from changri.platform import layout  # noqa: E402


class MediaTests(unittest.TestCase):
    def test_static_falls_back_to_runtime_media(self):
        media = Path(tempfile.mkdtemp())
        (media / "phone" / "static" / "masquerade").mkdir(parents=True)
        (media / "phone" / "static" / "masquerade" / "only_in_runtime.m4a").write_bytes(b"AUDIO")
        with mock.patch.object(layout, "MEDIA_DIR", media):
            from changri.core.db import init_db
            from changri.phone.app import create_phone_app
            init_db()
            c = create_phone_app("k").test_client()
            r = c.get("/p/static/masquerade/only_in_runtime.m4a")
            self.assertEqual((r.status_code, r.data), (200, b"AUDIO"))
            self.assertEqual(c.get("/p/static/masquerade/nope.m4a").status_code, 404)
            self.assertEqual(c.get("/p/static/../../app.py").status_code, 404)

    def test_asset_prefers_package(self):
        self.assertEqual(layout.asset("VERSION"), layout.PKG_DIR / "VERSION")
        self.assertTrue((layout.fonts_dir() / "SansSC-Regular.otf").exists())
        with mock.patch.object(layout, "MEDIA_DIR", Path("/x")):
            self.assertEqual(layout.asset("assets/fonts/none.otf"), Path("/x/assets/fonts/none.otf"))


class RemoteMediaTests(unittest.TestCase):
    """背景音乐：安装包里没有，第一次用到时从主页下载一次，校验后缓存在 data/media/。"""

    def setUp(self):
        import hashlib
        import json
        from changri.platform import remote_media
        self.rm = remote_media
        self.site = Path(tempfile.mkdtemp())
        self.data = Path(tempfile.mkdtemp())
        self.pkg = Path(tempfile.mkdtemp())            # 假装安装后的包里没有这个文件
        rel = "phone/static/masquerade/song.m4a"
        (self.site / "phone/static/masquerade").mkdir(parents=True)
        (self.site / rel).write_bytes(b"MUSIC")
        man = self.pkg / "remote_media.json"
        man.write_text(json.dumps({rel: {"sha256": hashlib.sha256(b"MUSIC").hexdigest(), "size": 5}}))
        self.patches = [mock.patch.object(remote_media, "MANIFEST", man), mock.patch.object(remote_media, "DATA_DIR", self.data),
                        mock.patch.dict(os.environ, {"CHANGRI_MEDIA_URL": self.site.as_uri() + "/"}),
                        mock.patch.object(layout, "MEDIA_DIR", Path(tempfile.mkdtemp()))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def client(self):
        from changri.core.db import init_db
        from changri.phone import app as phone_app
        init_db()
        return phone_app.create_phone_app("k").test_client()

    def test_downloads_once_and_serves(self):
        with mock.patch("changri.phone.app.HERE", self.pkg):
            c = self.client()
            r = c.get("/p/static/masquerade/song.m4a")
            self.assertEqual((r.status_code, r.data), (200, b"MUSIC"))
            self.assertEqual((self.data / "media/phone/static/masquerade/song.m4a").read_bytes(), b"MUSIC")
            (self.site / "phone/static/masquerade/song.m4a").unlink()             # 主页上删了也没关系：已经缓存
            self.assertEqual(c.get("/p/static/masquerade/song.m4a").data, b"MUSIC")

    def test_bad_checksum_rejected(self):
        (self.site / "phone/static/masquerade/song.m4a").write_bytes(b"EVIL!")
        with mock.patch("changri.phone.app.HERE", self.pkg):
            r = self.client().get("/p/static/masquerade/song.m4a")
        self.assertEqual(r.status_code, 503)
        self.assertFalse((self.data / "media/phone/static/masquerade/song.m4a").exists())

    def test_manifest_matches_files_in_repo(self):
        import hashlib
        import json
        real = json.loads((layout.PKG_DIR / "remote_media.json").read_text())
        for rel, item in real.items():
            f = layout.PKG_DIR / rel
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), item["sha256"], f"{rel} 换了：运行 build.py --refresh-remote-media")


if __name__ == "__main__":
    unittest.main()
