import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.mkdtemp()
os.environ["CHANGRI_DB_PATH"] = str(Path(_tmp) / "t.db")

from core import messaging, roles  # noqa: E402
from core.db import get_conn, init_db, set_config  # noqa: E402


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

    def test_player_codes_backup_and_main(self):
        roles.create_role("甲", "B1", "G1", "M1")
        rid = roles.get_role_by_name("甲")["id"]
        code = roles.make_player_code(rid, "backup")
        self.assertFalse(roles.consume_bind_code(code, "B1", "G2", "M1", "main")[0])   # 类型不符
        self.assertTrue(roles.consume_bind_code(code, "B1", "G2", "M1", "backup")[0])
        self.assertFalse(roles.consume_bind_code(code, "B1", "G3", "M1")[0])           # 码只能用一次
        self.assertEqual([g["kind"] for g in roles.list_groups(rid)], ["main", "backup"])
        code = roles.make_player_code(rid, "main")
        self.assertTrue(roles.consume_bind_code(code, "B1", "G9", "M1", "main")[0])
        self.assertEqual(roles.list_groups(rid)[0]["group_openid"], "G9")
        self.assertEqual(roles.unbind_backup(rid, 1)[0], False)                  # 主群不能解绑
        self.assertTrue(roles.unbind_backup(rid, 2)[0])

    def test_backup_limit_and_taken_group(self):
        set_config("max_backup_groups", "1")
        roles.create_role("甲", "B1", "G1", "M1"); roles.create_role("乙", "B1", "G2", "M2")
        rid = roles.get_role_by_name("甲")["id"]
        c1 = roles.make_player_code(rid, "backup")
        self.assertFalse(roles.consume_bind_code(c1, "B1", "G2", "M1")[0])             # G2 已属于乙
        self.assertTrue(roles.consume_bind_code(c1, "B1", "G3", "M1")[0])
        c2 = roles.make_player_code(rid, "backup")
        self.assertFalse(roles.consume_bind_code(c2, "B1", "G4", "M1")[0])             # 到上限
        set_config("max_backup_groups", "3")

    def test_admin_slot_by_real_gid(self):
        roles.create_role("甲", "B1", "G1", "M1")
        rid = roles.get_role_by_name("甲")["id"]
        self.assertFalse(roles.add_slot(rid, "abc", "backup")[0])
        ok, code = roles.add_slot(rid, "987654321", "backup")
        self.assertTrue(ok)
        slot = roles.list_groups(rid)[1]
        self.assertIsNone(slot["group_openid"]); self.assertEqual(slot["real_gid"], "987654321")
        self.assertTrue(roles.consume_bind_code(code, "B1", "GB", "MX", "backup")[0])
        slot = roles.list_groups(rid)[1]
        self.assertEqual((slot["group_openid"], slot["real_gid"]), ("GB", "987654321"))


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

    def test_main_then_backup_fallback(self):
        code = roles.make_player_code(self.b, "backup"); roles.consume_bind_code(code, "B1", "G2B", "M2")
        mid = messaging.queue_message("sms", self.a, self.b, "", "你好")
        self.assertTrue(run(messaging.deliver(mid, self.sender(fail={"G2"}))))   # 主群失败，备用群成功
        self.assertEqual(self.sent[0][0], "G2B")
        self.assertIn("甲", self.sent[0][1]); self.assertIn("你好", self.sent[0][1])
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT status, delivered_group FROM messages").fetchone()[:], ("sent", "G2B"))

    def test_unclaimed_slot_skipped_and_retry_then_fail(self):
        roles.add_slot(self.b, "111111", "backup")                                # 未认群的群位，不能拿来发
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
