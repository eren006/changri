import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "b.db")
os.environ["QQ_BOTS"] = json.dumps([{"id": "111111111", "secret": "oldsecretoldsecret1", "token": ""}])
import changri.admin as admin_mod  # noqa: E402

os.environ.pop("ADMIN_PASSWORD", None)
from changri.core import botconf, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402
from tests.test_admin import token  # noqa: E402

GOOD = "A" * 32


class BotConfTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM bot_accounts")
            c.execute("DELETE FROM web_users")

    def test_env_import_once(self):
        os.environ["QQ_BOTS"] = json.dumps([{"id": "111111111", "secret": "oldsecretoldsecret1", "token": ""}])
        botconf.import_env_bots()
        self.assertEqual([a["app_id"] for a in botconf.list_accounts()], ["111111111"])
        botconf.delete_account("111111111")
        os.environ["QQ_BOTS"] = "[]"
        botconf.import_env_bots()
        self.assertEqual(botconf.list_accounts(), [])

    def test_add_validate_and_config(self):
        os.environ["QQ_BOTS"] = "[]"
        self.assertFalse(botconf.add_account("abc", GOOD)[0])
        self.assertFalse(botconf.add_account("123456789", "short")[0])
        self.assertTrue(botconf.add_account(" 123456789 ", GOOD, "主号")[0])
        self.assertFalse(botconf.add_account("123456789", GOOD)[0])           # 重复
        cfg = botconf.qq_bots_config()
        self.assertEqual(cfg[0]["id"], "123456789")
        self.assertEqual(cfg[0]["secret"], GOOD)
        botconf.set_enabled("123456789", False)
        self.assertEqual(botconf.qq_bots_config(), [])
        self.assertEqual(botconf.mask(GOOD), "••••AAAA")

    def test_credentials_steps(self):
        calls = {
            "bad": [(200, "{}")],
            "ip": [(200, '{"access_token":"t"}'), (401, '{"code":11298,"message":"call api from ip 1.2.3.4 not in whitelist"}')],
            "ok": [(200, '{"access_token":"t"}'), (200, '{"url":"wss://x"}')],
        }
        with mock.patch.object(botconf, "_http", side_effect=calls["bad"]):
            self.assertEqual(botconf.test_credentials("1", "2")["step"], "secret")
        with mock.patch.object(botconf, "_http", side_effect=calls["ip"]):
            r = botconf.test_credentials("1", "2")
            self.assertEqual((r["step"], r["ip"]), ("whitelist", "1.2.3.4"))
        with mock.patch.object(botconf, "_http", side_effect=calls["ok"]):
            self.assertTrue(botconf.test_credentials("1", "2")["ok"])
        with mock.patch.object(botconf, "_http", side_effect=OSError("x")):
            self.assertEqual(botconf.test_credentials("1", "2")["step"], "network")


class BotPageTests(unittest.TestCase):
    def setUp(self):
        init_db()
        os.environ["QQ_BOTS"] = "[]"
        with get_conn() as c:
            c.execute("DELETE FROM bot_accounts")
            c.execute("DELETE FROM web_users")
        webusers.create_user("boss", "longenough1", "owner")
        self.admin_name, self.admin_pw = webusers.generate_admin("")
        self.app = admin_mod.create_app()

    def client(self, name, pw):
        c = self.app.test_client()
        c.post("/login", data={"username": name, "password": pw, "_csrf": token(c)})
        return c

    def test_owner_adds_and_admin_blocked(self):
        c = self.client("boss", "longenough1")
        with mock.patch.object(botconf, "public_ip", return_value="9.9.9.9"), \
             mock.patch.object(botconf, "_http", side_effect=[(200, '{"access_token":"t"}'), (200, '{"url":"wss://x"}')]):
            html = c.post("/bots/add", data={"app_id": "222222222", "secret": GOOD, "note": "二号", "_csrf": token(c, "/bots")}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("222222222", html)
        self.assertIn("白名单也通过了", html)
        self.assertNotIn(GOOD, html)                       # 密钥不回显
        self.assertIn("••••AAAA", html)
        a = self.client(self.admin_name, self.admin_pw)
        self.assertEqual(a.post("/bots/add", data={"app_id": "333333333", "secret": GOOD, "_csrf": token(a, "/bots")}).status_code, 403)
        page = a.get("/bots").get_data(as_text=True)
        self.assertIn("222222222", page)
        self.assertNotIn("••••AAAA", page)                 # 管理员看不到密钥，连掩码也没有
        self.assertEqual(len(botconf.list_accounts()), 1)

    def test_owner_secret_toggle_delete(self):
        botconf.add_account("222222222", GOOD)
        c = self.client("boss", "longenough1")
        t = lambda: token(c, "/bots")
        with mock.patch.object(botconf, "_http", side_effect=[(200, "{}")]):
            c.post("/bots/222222222/secret", data={"secret": "B" * 32, "_csrf": t()})
        self.assertEqual(botconf.get_account("222222222")["secret"], "B" * 32)
        c.post("/bots/222222222/toggle", data={"on": "0", "_csrf": t()})
        self.assertEqual(botconf.get_account("222222222")["enabled"], 0)
        c.post("/bots/222222222/delete", data={"_csrf": t()})
        self.assertIsNone(botconf.get_account("222222222"))

    def test_update_pages_owner_only(self):
        from changri.core import updater
        c = self.client("boss", "longenough1")
        a = self.client(self.admin_name, self.admin_pw)
        self.assertEqual(a.get("/update").status_code, 403)
        self.assertEqual(a.post("/update/apply", data={"_csrf": token(a, "/bots")}).status_code, 403)
        self.assertNotIn("/update", a.get("/bots").get_data(as_text=True))          # 管理员看不到入口
        self.assertIn("/update", c.get("/bots").get_data(as_text=True))
        info = {"version": "9.9.9", "current": "0.3.0", "newer": True, "notes": "新东西", "size": 2097152, "sha256": "", "signature": "", "url": ""}
        with mock.patch.object(updater, "check", return_value=info):
            c.post("/update/check", data={"_csrf": token(c, "/update")})
            html = c.get("/update").get_data(as_text=True)
        self.assertIn("9.9.9", html); self.assertIn("立即更新", html)
        with mock.patch.object(updater, "update_now", return_value={"from": "0.3.0", "to": "9.9.9", "backup": "x"}), \
             mock.patch.object(updater, "restart") as rs:
            r = c.post("/update/apply", data={"_csrf": token(c, "/update")})
        self.assertEqual(r.status_code, 302); rs.assert_called_once()
        with mock.patch.object(updater, "update_now", side_effect=updater.UpdateError("签名校验没通过")), \
             mock.patch.object(updater, "restart") as rs:
            html = c.post("/update/apply", data={"_csrf": token(c, "/update")}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("签名校验没通过", html); rs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
