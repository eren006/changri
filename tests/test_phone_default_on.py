"""0.22.4 小手机默认打开：迁移把老库也打开；扩展卡片和「小手机」页是同一个开关；总览在「开着却没跑起来 / 还没确认外面能打开」时提醒。"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "pdo.db")

from changri.core.db import get_config, init_db, set_config  # noqa: E402
from changri.storage import migrate  # noqa: E402


class Migration(unittest.TestCase):
    def _db_at(self, version: int) -> Path:
        """建一个停在某个版本的库（只跑到 version 为止的迁移）。"""
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path, isolation_level=None)
        for num, p in migrate.available():
            if num > version:
                break
            migrate._apply(conn, num, p)
        conn.close()
        return path

    def test_upgrade_turns_phone_on(self):
        path = self._db_at(22)
        conn = sqlite3.connect(path)
        conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES('phone_enabled', '0'), ('ext:phone', '0')")   # 以前是关的
        conn.commit()
        conn.close()
        migrate.migrate(path)
        conn = sqlite3.connect(path)
        got = dict(conn.execute("SELECT key, value FROM config WHERE key IN ('phone_enabled', 'ext:phone')").fetchall())
        conn.close()
        self.assertEqual(got, {"phone_enabled": "1", "ext:phone": "1"})

    def test_new_install_has_phone_on(self):
        path = Path(tempfile.mkdtemp()) / "new.db"
        migrate.migrate(path)
        conn = sqlite3.connect(path)
        got = dict(conn.execute("SELECT key, value FROM config WHERE key IN ('phone_enabled', 'ext:phone')").fetchall())
        conn.close()
        self.assertEqual(got, {"phone_enabled": "1", "ext:phone": "1"})


class Switch(unittest.TestCase):
    def setUp(self):
        init_db()
        os.environ["QQ_BOTS"] = "[]"
        import re
        import changri.admin as admin_mod
        from changri.core import webusers
        from changri.core.db import get_conn
        os.environ.pop("ADMIN_PASSWORD", None)
        os.environ.pop("FLASK_SECRET_KEY", None)
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
            c.execute("DELETE FROM config")
        admin_mod._fails.clear()
        self.c = admin_mod.create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        self.tok = lambda path="/login": re.search(r'name="_csrf" value="([0-9a-f]+)"', self.c.get(path).get_data(as_text=True)).group(1)
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self.tok()})

    def test_extension_card_drives_phone_switch(self):
        from changri.core import phoneserver
        with mock.patch.object(phoneserver, "start", return_value="小手机已关闭"):
            self.c.post("/extensions/toggle", data={"id": "phone", "on": "0", "_csrf": self.tok("/extensions")})
        self.assertEqual(get_config("phone_enabled"), "0")
        with mock.patch.object(phoneserver, "start", return_value="小手机已在 5092 端口运行"):
            self.c.post("/extensions/toggle", data={"id": "phone", "on": "1", "_csrf": self.tok("/extensions")})
        self.assertEqual(get_config("phone_enabled"), "1")

    def test_dashboard_hints(self):
        from changri.admin.common import _phone_todo
        from changri.core import phoneserver
        from changri.platform import portcheck
        with self.c.application.test_request_context():
            set_config("phone_enabled", "0")
            self.assertEqual(_phone_todo(), [])                                # 管理员自己关的：不提醒
            set_config("phone_enabled", "1")
            set_config("ext:phone", "1")
            with mock.patch.object(phoneserver, "running", return_value=False), \
                 mock.patch.object(portcheck, "can_bind", return_value=False), \
                 mock.patch.object(portcheck, "who_listens", return_value="python.exe（PID 1234）"):
                t = _phone_todo()
            self.assertEqual(t[0][0], "bad")
            self.assertIn("python.exe（PID 1234）", t[0][3])
            with mock.patch.object(phoneserver, "running", return_value=True):
                t = _phone_todo()
                self.assertEqual((t[0][0], t[0][1]), ("warn", "还没确认玩家能打开小手机"))
                set_config("phone_net_last", json.dumps({"state": "ok", "msg": ""}))
                self.assertEqual(_phone_todo(), [])


class PhoneLink(unittest.TestCase):
    def test_link_filled_from_public_ip(self):
        init_db()
        set_config("phone_base_url", "")
        from changri.core import netcheck, phoneserver
        with mock.patch.object(netcheck, "default_base_url", return_value="http://1.2.3.4:5092"):
            self.assertEqual(phoneserver.player_url(), "http://1.2.3.4:5092/p")
        self.assertEqual(get_config("phone_base_url"), "http://1.2.3.4:5092")       # 存下来了，以后不再查
        with mock.patch.object(netcheck, "default_base_url", side_effect=AssertionError("不该再查")):
            self.assertEqual(phoneserver.player_url(), "http://1.2.3.4:5092/p")


if __name__ == "__main__":
    unittest.main()
