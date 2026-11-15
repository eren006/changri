import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "m.db"))

import changri.core.db as db  # noqa: E402
from changri.core import admins, messaging, roles  # noqa: E402
from changri.core.announce import get_announce, set_announce  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class MultiBotTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("messages", "bind_codes", "role_groups", "roles", "admins", "admin_codes", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("sms_cooldown_sec", "0")

    def test_openid_is_per_bot(self):
        roles.create_role("甲", "B1", "G1", "M1")
        self.assertEqual(roles.find_role_for_sender("B1", "G1", "M1")["name"], "甲")
        self.assertIsNone(roles.find_role_for_sender("B2", "G1", "M1"))          # 同样的 openid 在另一个机器人下不是同一个人
        self.assertTrue(roles.create_role("乙", "B2", "G1", "M1")[0])              # 另一个机器人下撞了 openid 也互不影响
        self.assertEqual(roles.find_role_for_sender("B2", "G1", "M1")["name"], "乙")

    def test_same_player_binds_group_of_second_bot(self):
        roles.create_role("甲", "B1", "G1", "M1")
        rid = roles.get_role_by_name("甲")["id"]
        code = roles.make_player_code(rid, "backup")
        self.assertTrue(roles.consume_bind_code(code, "B2", "H9", "N7", "backup")[0])   # 在第二个机器人的群里认群
        self.assertEqual(roles.find_role_for_sender("B2", "H9", "N7")["name"], "甲")    # 之后用第二个机器人也认得他
        self.assertEqual([(g["bot_id"], g["group_openid"]) for g in roles.list_groups(rid)], [("B1", "G1"), ("B2", "H9")])

    def test_delivery_uses_the_groups_own_bot_and_falls_over(self):
        roles.create_role("甲", "B1", "G1", "M1"); roles.create_role("乙", "B1", "G2", "M2")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        roles.consume_bind_code(roles.make_player_code(b, "backup"), "B2", "H2", "N2", "backup")
        calls = []

        async def send(bot, group, text):
            calls.append((bot, group))
            if bot == "B1":
                raise RuntimeError("B1 离线")

        mid = messaging.queue_message("sms", a, b, "", "hi")
        loop = asyncio.new_event_loop()
        try:
            self.assertTrue(loop.run_until_complete(messaging.deliver(mid, send)))
        finally:
            loop.close()
        self.assertEqual(calls, [("B1", "G2"), ("B2", "H2")])                         # 先试主群所属的机器人，失败换备用群所属的机器人

    def test_admin_per_bot_and_announce(self):
        code = admins.make_code()
        admins.claim(code, "B1", "MX")
        self.assertTrue(admins.is_admin("B1", "MX")); self.assertFalse(admins.is_admin("B2", "MX"))
        set_announce("B2", "AG")
        self.assertEqual(get_announce(), ("B2", "AG"))
        set_announce("", "")
        self.assertIsNone(get_announce())


class MigrationTests(unittest.TestCase):
    def test_old_schema_is_upgraded_without_losing_data(self):
        old = Path(tempfile.mkdtemp()) / "old.db"
        c = sqlite3.connect(old)
        c.executescript("""
            CREATE TABLE roles (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, member_openid TEXT NOT NULL, created_ts INTEGER NOT NULL);
            CREATE TABLE role_groups (id INTEGER PRIMARY KEY AUTOINCREMENT, role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                group_openid TEXT UNIQUE, real_gid TEXT NOT NULL DEFAULT '', member_openid TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL CHECK (kind IN ('main','backup')), created_ts INTEGER NOT NULL);
            CREATE TABLE bind_codes (code TEXT PRIMARY KEY, role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, slot_id INTEGER REFERENCES role_groups(id) ON DELETE CASCADE, expires_ts INTEGER NOT NULL);
            CREATE TABLE admins (member_openid TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', created_ts INTEGER NOT NULL);
            INSERT INTO roles VALUES (1, '甲', 'M1', 1), (2, '乙', 'M2', 1);
            INSERT INTO role_groups VALUES (1, 1, 'G1', '', 'M1', 'main', 1), (2, 2, 'G2', '123456', 'M2', 'main', 1), (3, 2, NULL, '999999', '', 'backup', 1);
            INSERT INTO bind_codes VALUES ('111111', 2, 'backup', 3, 9999999999);
            INSERT INTO admins VALUES ('MA', '站长', 1);
            CREATE TABLE web_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, created_ts INTEGER NOT NULL, last_login_ts INTEGER);
            INSERT INTO web_users VALUES (5, 'boss', 'h1', 1, NULL), (9, 'second', 'h2', 2, NULL);
        """)
        c.commit(); c.close()
        saved_path, saved_env = db.DB_PATH, os.environ.get("QQ_BOTS")
        db.DB_PATH = old
        os.environ["QQ_BOTS"] = '[{"id": "B9", "secret": "x"}]'
        try:
            db.init_db(); db.init_db()                                              # 重复执行也安全
            conn = db.get_conn()
            rows = conn.execute("SELECT id, bot_id, group_openid FROM role_groups ORDER BY id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [(1, "B9", "G1"), (2, "B9", "G2"), (3, "", None)])   # 有群的归到第一个机器人，未认群的群位保持为空
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bind_codes").fetchone()[0], 1)             # 重建表没有把认群码级联删掉
            self.assertEqual(tuple(conn.execute("SELECT bot_id, member_openid, note FROM admins").fetchone()), ("B9", "MA", "站长"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0], 2)
            self.assertEqual([tuple(r) for r in conn.execute("SELECT username, role, disabled FROM web_users ORDER BY id")], [("boss", "owner", 0), ("second", "admin", 0)])   # 最早的账号升为主账号
            conn.close()
        finally:
            db.DB_PATH = saved_path
            if saved_env is None:
                os.environ.pop("QQ_BOTS", None)
            else:
                os.environ["QQ_BOTS"] = saved_env


if __name__ == "__main__":
    unittest.main()
