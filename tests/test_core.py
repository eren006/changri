import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = tempfile.mkdtemp()
os.environ["CHANGRI_DB_PATH"] = str(Path(_tmp) / "t.db")

from changri.core import messaging, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("messages", "bind_codes", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("sms_cooldown_sec", "0")
        set_config("gift_cooldown_sec", "0")


class RoleTests(Base):
    def test_create_and_rules(self):
        self.assertTrue(roles.create_role("甲", "B1", "G1", "M1", "123456")[0])
        self.assertFalse(roles.create_role("乙", "B1", "G1", "M2")[0])      # 群已被占用
        self.assertFalse(roles.create_role("乙", "B1", "G2", "M1")[0])      # 这个人已有角色
        self.assertFalse(roles.create_role("甲", "B1", "G3", "M3")[0])      # 重名
        self.assertFalse(roles.create_role("有 空格", "B1", "G4", "M4")[0])
        self.assertFalse(roles.create_role("丙", "B1", "G5", "M5", "12ab")[0])  # 群号格式
        self.assertEqual(roles.find_role_for_sender("B1", "G1", "M1")["name"], "甲")
        self.assertEqual(roles.find_role_for_sender("B1", "OTHER", "M1")["name"], "甲")  # 跨群按人认
        self.assertIsNone(roles.find_role_for_sender("B1", "G1", "NOBODY"))

    def test_player_change_code(self):
        roles.create_role("甲", "B1", "G1", "M1"); roles.create_role("乙", "B1", "G2", "M2")
        rid = roles.get_role_by_name("甲")["id"]
        code = roles.make_player_code(rid)
        self.assertFalse(roles.consume_bind_code(code, "B1", "G2", "M1")[0])             # G2 已属于乙
        self.assertTrue(roles.consume_bind_code(code, "B1", "G3", "M1")[0])              # 换绑：个人群换成 G3
        groups = roles.list_groups(rid)
        self.assertEqual([(g["kind"], g["group_openid"]) for g in groups], [("main", "G3")])

    def test_admin_slot_by_real_gid(self):
        roles.create_role("甲", "B1", "G1", "M1")
        rid = roles.get_role_by_name("甲")["id"]
        self.assertFalse(roles.add_slot(rid, "abc")[0])
        ok, code = roles.add_slot(rid, "987654321")
        self.assertTrue(ok)
        self.assertEqual(len(roles.list_groups(rid)), 1)
        g = roles.list_groups(rid)[0]                       # 还没认群：旧群照旧，新群号不能先显示成「已认群」
        self.assertEqual((g["group_openid"], g["real_gid"]), ("G1", ""))
        self.assertEqual((roles.pending_code(rid)["code"], roles.pending_code(rid)["real_gid"]), (code, "987654321"))
        self.assertTrue(roles.consume_bind_code(code, "B1", "GB", "MX")[0])
        self.assertIsNone(roles.pending_code(rid))
        g = roles.list_groups(rid)[0]
        self.assertEqual((g["group_openid"], g["real_gid"]), ("GB", "987654321"))


class MessagingTests(Base):
    def setUp(self):
        super().setUp()
        roles.create_role("甲", "B1", "G1", "M1"); roles.create_role("乙", "B1", "G2", "M2")
        self.a = roles.get_role_by_name("甲")["id"]; self.b = roles.get_role_by_name("乙")["id"]
        self.sent = []

    def sender(self, fail=()):
        async def send(bot, group, text):
            if group in fail:
                raise RuntimeError("blocked")
            self.sent.append((group, text))
        return send

    def test_retry_then_fail(self):
        mid = messaging.queue_message("gift", self.a, self.b, "玫瑰", "给你")
        self.assertFalse(run(messaging.deliver(mid, self.sender(fail={"G2"}))))
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT status, attempts FROM messages").fetchone()[:], ("pending", 1))
        run(messaging.retry_pending(self.sender(fail={"G2"}))); run(messaging.retry_pending(self.sender(fail={"G2"})))
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT status, attempts FROM messages").fetchone()[:], ("failed", 3))
        self.assertEqual(self.sent, [])

    def test_retry_succeeds_and_gift_text(self):
        mid = messaging.queue_message("gift", self.a, self.b, "玫瑰", "给你")
        run(messaging.deliver(mid, self.sender(fail={"G2"})))
        self.assertEqual(run(messaging.retry_pending(self.sender())), 1)
        self.assertEqual(self.sent[0], ("G2", "{{AT:M2:乙}}🎁 甲 送了你「玫瑰」\n留言：给你"))      # 发给本人的通知开头 @ 本人

    def test_cooldown(self):
        set_config("sms_cooldown_sec", "60")
        self.assertEqual(messaging.cooldown_left(self.a, "sms"), 0)
        messaging.queue_message("sms", self.a, self.b, "", "x")
        self.assertGreater(messaging.cooldown_left(self.a, "sms"), 0)
        self.assertEqual(messaging.cooldown_left(self.b, "sms"), 0)


if __name__ == "__main__":
    unittest.main()
