"""多机器人：每个机器人一套独立数据库（工作区）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["QQ_BOTS"] = "[]"                      # 不要把本机 .env 里的机器人导进来
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "x.db")      # 先让别的模块能 import；下面再切到多工作区模式

import core.db as db  # noqa: E402
from core import botconf, roles  # noqa: E402


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, db._default_bot, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.d / "changri.db", self.d / "bots", False
        db._inited.clear()
        db.forget_bots()

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, db._default_bot = self.saved[:4]
        db._inited.clear()
        db._inited.update(self.saved[4])

    def _two_bots(self):
        db.init_db()
        self.assertTrue(botconf.add_account("111111", "a" * 20)[0])
        self.assertTrue(botconf.add_account("222222", "b" * 20)[0])

    def test_data_isolated_between_bots(self):
        self._two_bots()
        with db.use_bot("111111"):
            roles.create_role("甲", "111111", "G1", "M1")
            db.set_config("sms_cooldown_sec", "7")
        with db.use_bot("222222"):
            self.assertIsNone(roles.get_role_by_name("甲"))
            self.assertEqual(db.get_config("sms_cooldown_sec", "x"), "x")
            roles.create_role("甲", "222222", "G1", "M1")           # 同名角色在另一个机器人里互不影响
        with db.use_bot("111111"):
            self.assertEqual(db.get_config("sms_cooldown_sec"), "7")
        self.assertTrue(db.workspace_path("111111").exists() and db.workspace_path("222222").exists())

    def test_global_config_and_accounts_shared(self):
        self._two_bots()
        with db.use_bot("111111"):
            db.set_config("remote_admin_ips", "1.2.3.4")
            db.set_config("update_latest", "9.9")
        with db.use_bot("222222"):
            self.assertEqual(db.get_config("remote_admin_ips"), "1.2.3.4")
            self.assertEqual(len(botconf.list_accounts()), 2)

    def test_default_bot_is_first(self):
        self._two_bots()
        self.assertEqual(db.current_bot(), "111111")
        with db.use_bot("222222"):
            self.assertEqual(db.current_bot(), "222222")
        self.assertEqual(db.current_bot(), "111111")

    def test_migrate_old_single_db_into_first_bot(self):
        db.init_db()                                           # 只有全局库，没有任何机器人
        with db.get_global_conn() as c:
            c.execute("INSERT INTO roles(name, member_openid, created_ts) VALUES ('老角色', 'M', 1)")
            c.execute("INSERT INTO config(key, value) VALUES ('sms_cooldown_sec', '9')")
        botconf.add_account("111111", "a" * 20)                # 升级后接入机器人
        msg = db.migrate_to_workspaces()
        self.assertIn("111111", msg)
        self.assertTrue((self.d / "changri.db.premulti").exists())
        self.assertEqual(db.migrate_to_workspaces(), "")        # 幂等
        db.init_db()
        with db.use_bot("111111"):
            self.assertIsNotNone(roles.get_role_by_name("老角色"))
            self.assertEqual(db.get_config("sms_cooldown_sec"), "9")

    def test_for_each_bot_and_per_bot(self):
        self._two_bots()
        seen = []
        for app_id in db.for_each_bot():
            seen.append((app_id, db.current_bot()))
        self.assertEqual(seen, [("111111", "111111"), ("222222", "222222")])
        botconf.set_enabled("222222", False)
        self.assertEqual(db.list_bot_ids(), ["111111"])


if __name__ == "__main__":
    unittest.main()
