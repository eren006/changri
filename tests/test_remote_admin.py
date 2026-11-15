import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "r.db"))
from changri.admin.remote import RemoteGuard, allowed, is_blocked_path, parse_allow  # noqa: E402
from changri.core.db import init_db, set_config  # noqa: E402


def call(guard, ip, path):
    out = {}
    body = guard({"REMOTE_ADDR": ip, "PATH_INFO": path}, lambda st, h: out.update(st=st))
    return out["st"], b"".join(body)


class Remote(unittest.TestCase):
    def test_all(self):
        init_db()
        ok_app = lambda env, sr: (sr("200 OK", []), [b"hi"])[1]
        g = RemoteGuard(ok_app)
        set_config("remote_admin_ips", "")
        self.assertTrue(call(g, "1.2.3.4", "/")[0].startswith("404"))            # 名单空：谁都不行
        set_config("remote_admin_ips", "1.2.3.4, 9.9.9.0/24")
        self.assertTrue(call(g, "1.2.3.4", "/login")[0].startswith("200"))
        self.assertTrue(call(g, "9.9.9.77", "/roles")[0].startswith("200"))
        self.assertTrue(call(g, "8.8.8.8", "/login")[0].startswith("404"))       # 名单外
        for path in ("/bots", "/bots/x/secret", "/accounts/password", "/update/apply", "/remote/save", "/extensions/upload", "/extensions/abc/delete", "/extensions/restart"):
            self.assertTrue(call(g, "1.2.3.4", path)[0].startswith("403"), path)
        for path in ("/extensions", "/extensions/abc/settings", "/pool", "/settings/基础"):
            self.assertFalse(is_blocked_path(path), path)
        self.assertEqual(len(parse_allow("bad, 1.1.1.1  2.2.2.0/24")), 2)
        self.assertFalse(allowed("nonsense", parse_allow("1.1.1.1")))


if __name__ == "__main__":
    unittest.main()
