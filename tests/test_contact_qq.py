"""联系 QQ（选填）：后台「基础」里填了，报错上报才带上；不填、填得不像 QQ 号都不带。主站收的时候再验一遍。"""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "cq.db")
import errors_service  # noqa: E402
from changri.admin import common  # noqa: E402
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db, set_config  # noqa: E402


class Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class ContactQQ(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()
        set_config("contact_qq", "")
        set_config("error_report_url", "http://example.invalid/api/errors")

    def _send(self):
        sent = []

        def fake(req, timeout=0):
            sent.append(json.loads(req.data.decode("utf-8")))
            return Resp()
        errlog.record("error", "x", "boom")
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(errlog.report_pending(), 1)
        return sent[0]

    def test_optional(self):
        self.assertIn("contact_qq", common.SETTINGS)
        self.assertEqual(common.SETTINGS["contact_qq"][1], "")                   # 默认不填
        self.assertNotIn("contact", self._send())                                # 没填：不带
        set_config("contact_qq", "不是号码")
        self.assertNotIn("contact", self._send())                                # 填得不像 QQ 号：不带
        set_config("contact_qq", " 123456789 ")
        p = self._send()
        self.assertEqual(p["contact"], "123456789")
        self.assertNotIn("123456789", json.dumps(p["errors"]))                   # 只在 contact 这一项里

    def test_server_side(self):
        rows = errors_service.clean({"app": "changri_bot", "version": "0.22.1", "install": "a" * 16, "contact": "123456789",
                                     "errors": [{"ts": 1, "kind": "error", "source": "s", "message": "m"}]}, "1.2.3.4")
        self.assertEqual(rows[0]["contact"], "123456789")
        for bad in ("abc", "12", "1" * 20, "<script>", None):
            rows = errors_service.clean({"app": "a", "errors": [{"ts": 1, "message": "m"}], "contact": bad}, "1.2.3.4")
            self.assertNotIn("contact", rows[0])


if __name__ == "__main__":
    unittest.main()
