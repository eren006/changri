"""匿名 / 自定义署名 / 化名的短信礼物：收件人那边任何地方都不能按真实发件人显示（好友列表卡片、选人排序、我的数量、季末报告）。"""
import os
import tempfile
import unittest

os.environ["CHANGRI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

from changri.core import contacts, datepick, messaging, mystats, roles, seasonreport
from changri.core.db import get_conn, init_db


class AnonSenderTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("messages", "role_groups", "roles"):
                c.execute(f"DELETE FROM {t}")
        for n in "甲乙丙":
            roles.create_role(n, "B1", "P" + n, "M" + n)
        self.a, self.b, self.c = (roles.get_role_by_name(n)["id"] for n in "甲乙丙")

    def _send(self, frm, to, sign="", kind="sms"):
        mid = messaging.queue_message(kind, frm, to, "玫瑰" if kind == "gift" else "", "你好", sign)
        with get_conn() as c:
            c.execute("UPDATE messages SET status='sent' WHERE id=?", (mid,))

    def test_anonymous_sender_not_revealed(self):
        self._send(self.a, self.b, sign="神秘人")
        self._send(self.a, self.b, sign="神秘人", kind="gift")
        self.assertEqual(contacts._last_contact(self.b, self.a), "")              # 乙看甲的卡片：看不出甲给自己发过
        self.assertNotIn(self.a, datepick._contacts(self.b))                       # 甲不会因此排到乙选人卡片前面
        stats = mystats.build(self.b)
        self.assertIn("神秘人", stats)
        self.assertNotIn("甲（", stats)
        self.assertNotIn("甲", seasonreport.build(self.b).split("互动最多的人")[-1] if "互动最多的人" in seasonreport.build(self.b) else "")
        # 发件人自己那边照常：甲知道自己发给了乙
        self.assertIn("你给 TA 发了", contacts._last_contact(self.a, self.b))
        self.assertIn(self.b, datepick._contacts(self.a))

    def test_signed_sender_still_shown(self):
        self._send(self.c, self.b)
        self.assertIn("TA 给你发了", contacts._last_contact(self.b, self.c))
        self.assertIn(self.c, datepick._contacts(self.b))
        self.assertIn("丙（", mystats.build(self.b))
        self.assertIn("丙", seasonreport.build(self.b))

    def test_reply_buttons_hidden_for_signed(self):
        with get_conn() as c:
            mid = messaging.queue_message("sms", self.a, self.b, "", "嗨", "神秘人")
            m = c.execute("SELECT m.*, f.name AS from_name FROM messages m JOIN roles f ON f.id=m.from_role_id WHERE m.id=?", (mid,)).fetchone()
        self.assertEqual(messaging.reply_buttons(m), [])


if __name__ == "__main__":
    unittest.main()
