"""后台多机器人：登录后选机器人、/b/<AppID>/ 下数据隔离、老网址自动补前缀。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import core.db as db  # noqa: E402
from core import botconf, roles, webusers  # noqa: E402


class AdminMultiBot(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        from admin import create_app
        self.app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        botconf.add_account("111111", "a" * 20, "甲号")
        self.c = self.app.test_client()
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self._csrf("/login")})

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def _csrf(self, path):
        import re
        html = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

    def test_single_bot_goes_straight_in_and_old_urls_redirect(self):
        r = self.c.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/b/111111/"))
        r = self.c.get("/roles")
        self.assertEqual(r.headers["Location"], "/b/111111/roles")
        self.assertEqual(self.c.get("/b/111111/roles").status_code, 200)
        self.assertEqual(self.c.get("/b/999999/roles").status_code, 404)

    def test_picker_with_two_bots_and_isolation(self):
        botconf.add_account("222222", "b" * 20, "乙号")
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn("选择机器人", html)
        self.assertIn("甲号", html)
        self.assertIn("乙号", html)
        with db.use_bot("111111"):
            roles.create_role("只在甲号", "111111", "G1", "M1")
        self.assertIn("只在甲号", self.c.get("/b/111111/roles").get_data(as_text=True))
        self.assertNotIn("只在甲号", self.c.get("/b/222222/roles").get_data(as_text=True))
        self.assertIn('href="/b/222222/roles"', self.c.get("/b/222222/").get_data(as_text=True))   # 页面链接自带前缀
        self.assertIn("/b/111111/", self.c.get("/b/222222/").get_data(as_text=True))                # 切换下拉
        self.assertEqual(self.c.get("/accounts").status_code, 200)                                   # 全局页面不带前缀

    def test_remote_block_regex_with_prefix(self):
        from admin.remote import is_blocked_path
        self.assertTrue(is_blocked_path("/b/111111/extensions/upload"))
        self.assertTrue(is_blocked_path("/b/111111/accounts"))
        self.assertFalse(is_blocked_path("/b/111111/roles"))


if __name__ == "__main__":
    unittest.main()


class Erase(AdminMultiBot):
    def test_erase_needs_exact_appid_and_removes_everything(self):
        botconf.add_account("222222", "b" * 20, "乙号")
        with db.use_bot("222222"):
            roles.create_role("乙的角色", "222222", "G", "M")
        t = self._csrf("/bots")
        self.c.post("/bots/222222/erase", data={"confirm": "wrong", "_csrf": t})
        self.assertTrue(db.workspace_path("222222").exists())                      # 没对上：什么都不删
        self.assertIsNotNone(botconf.get_account("222222"))
        self.c.post("/bots/222222/erase", data={"confirm": "222222", "_csrf": t})
        self.assertFalse(db.workspace_path("222222").parent.exists())
        self.assertIsNone(botconf.get_account("222222"))
        self.assertTrue(db.workspace_path("111111").exists() or botconf.get_account("111111"))   # 别的机器人不受影响


class NoGuess(AdminMultiBot):
    def test_write_without_bot_prefix_is_refused(self):
        botconf.add_account("222222", "b" * 20, "乙号")
        t = self._csrf("/b/111111/roles")
        r = self.c.post("/roles/1/delete", data={"_csrf": t})              # 没前缀的写操作
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/?pick=1"))
        r = self.c.post("/b/111111/roles/1/delete", data={"_csrf": t})     # 带前缀照常
        self.assertNotEqual(r.headers.get("Location"), "/?pick=1")


class Wizard(AdminMultiBot):
    def test_wizard_progress_and_redirect(self):
        self.assertEqual(self.c.get("/wizard").status_code, 200)
        html = self.c.get("/wizard").get_data(as_text=True)
        self.assertIn("已接入：甲号", html)
        self.assertIn("让机器人连上", html)                      # 机器人没在线：停在第 2 步，第 3 步提示先连上
        self.assertIn("先让机器人连上", html)

    def test_fresh_install_goes_to_wizard(self):
        botconf.delete_account("111111")
        r = self.c.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/wizard"))
        html = self.c.get("/wizard").get_data(as_text=True)
        self.assertIn("接入你的 QQ 机器人", html)
        self.assertIn('name="next" value="wizard"', html)
