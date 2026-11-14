"""每个机器人一个小手机端口：互不冲突、各用各的数据。"""
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import core.db as db  # noqa: E402
from core import botconf, phoneserver  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PhonePorts(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()
        botconf.add_account("111111", "a" * 20)
        botconf.add_account("222222", "b" * 20)

    def tearDown(self):
        for b in ("111111", "222222"):
            phoneserver.stop(b)
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def test_port_conflict_and_suggestion(self):
        with db.use_bot("111111"):
            db.set_config("phone_enabled", "1")
            db.set_config("phone_port", "5092")
        self.assertIn("已经给机器人", phoneserver.check_port("222222", "5092"))
        self.assertEqual(phoneserver.check_port("111111", "5092"), "")       # 自己的端口不算冲突
        self.assertEqual(phoneserver.check_port("222222", "5093"), "")
        self.assertIn("1024", phoneserver.check_port("222222", "80"))
        self.assertNotEqual(phoneserver.suggest_port("222222"), 5092)

    def test_two_phone_servers_each_in_own_workspace(self):
        p1, p2 = free_port(), free_port()
        for bid, port in (("111111", p1), ("222222", p2)):
            with db.use_bot(bid):
                db.set_config("phone_enabled", "1")
                db.set_config("phone_port", str(port))
        phoneserver.start_all()
        for port in (p1, p2):
            self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/p/health", timeout=5).status, 200)
        with db.use_bot("111111"):
            db.set_config("phone_enabled", "0")
        phoneserver.start("111111")
        with self.assertRaises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{p1}/p/health", timeout=2)
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{p2}/p/health", timeout=5).status, 200)


if __name__ == "__main__":
    unittest.main()
