"""小手机跟着机器人清单开 / 关：新装时开机那一刻还没有机器人，向导里加上机器人以后，小手机要自己开起来（不用重启长日bot）。"""
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, phoneserver  # noqa: E402

A, B = "333333", "444444"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PhoneFollowsBots(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()                                # 新装：迁移把小手机默认打开
        phoneserver._bots_cache = (0.0, [])
        self.port = free_port()
        phoneserver.set_port(self.port)

    def tearDown(self):
        phoneserver._stop_server()
        phoneserver._apps.clear()
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def health(self) -> int:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/p/health", timeout=5) as r:
            return r.status

    def test_added_bot_opens_phone_without_restart(self):
        self.assertEqual(phoneserver.start(), "小手机已关闭")           # 开机那一刻：还没有机器人
        self.assertFalse(phoneserver.running())
        botconf.add_account(A, "a" * 20)                                 # 向导里加上机器人
        phoneserver.ensure()                                             # 机器人同步那一轮
        self.assertTrue(phoneserver.running())
        self.assertEqual(self.health(), 200)
        phoneserver.ensure()                                             # 已经在跑：不重启
        self.assertTrue(phoneserver.running())

    def test_removed_bot_closes_phone(self):
        botconf.add_account(A, "a" * 20)
        phoneserver.ensure()
        self.assertTrue(phoneserver.running())
        botconf.delete_account(A)
        phoneserver.ensure()
        self.assertFalse(phoneserver.running())

    def test_removing_one_of_two_bots_keeps_phone_running(self):
        botconf.add_account(A, "a" * 20)
        botconf.add_account(B, "b" * 20)
        phoneserver.ensure()
        server = phoneserver._server["server"]
        for remove in (lambda: botconf.delete_account(A), lambda: botconf.set_enabled(B, False)):
            botconf.add_account(A, "a" * 20) if not botconf.get_account(A) else None
            remove()
            phoneserver._bots_cache = (0.0, [])
            phoneserver.ensure()
            self.assertTrue(phoneserver.running())
            self.assertIs(phoneserver._server["server"], server)        # 同一个监听，没有重启，另一个机器人的玩家不掉线
            self.assertEqual(self.health(), 200)
        self.assertEqual(phoneserver.phone_bots(fresh=True), [A])      # 剩下的就是还开着的那个

    def test_phone_switched_off_stays_off(self):
        botconf.add_account(A, "a" * 20)
        with db.use_bot(A):
            db.set_config("phone_enabled", "0")                          # 管理员自己关的
        phoneserver.ensure()
        self.assertFalse(phoneserver.running())


if __name__ == "__main__":
    unittest.main()
