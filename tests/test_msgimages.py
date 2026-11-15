"""短信图片：存储与校验、发送入库、小手机上传和只有收发双方能看。"""
import asyncio
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "mi.db"))

from changri.core import msgimages, phonecodes, roles, sendmsg  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.phone import app as phone_app  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class MsgImages(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("seasons", "message_images", "phone_codes", "phone_seen", "messages", "roles", "role_groups", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")
        with get_conn() as c:
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        set_config("game_day", "1")
        for n, i in (("甲", 0), ("乙", 1), ("丙", 2)):
            roles.create_role(n, f"B{i}", f"P{i}", f"M{i}")
        self.a, self.b, self.c3 = (roles.get_role_by_name(n) for n in ("甲", "乙", "丙"))
        phone_app._fails.clear()
        self.app = create_phone_app("k" * 32)

    def test_check_and_store(self):
        self.assertEqual(msgimages.sniff(PNG), "image/png")
        self.assertTrue(msgimages.check(b"not an image"))
        self.assertTrue(msgimages.check(b""))
        set_config("sms_image_enabled", "0")
        self.assertTrue(msgimages.check(PNG))
        set_config("sms_image_enabled", "1")
        self.assertEqual(msgimages.check(PNG), "")

    def test_send_and_delete(self):
        sent = []

        async def fn(*a, **k):
            sent.append(a)
            return True
        ok, _ = asyncio.run(sendmsg.send(self.a, self.b, "sms", "", "", "", fn, images=[PNG]))
        self.assertTrue(ok)
        self.assertEqual(msgimages.stats()["n"], 1)
        bad, why = asyncio.run(sendmsg.send(self.a, self.b, "sms", "", "hi", "", fn, images=[b"xx"]))
        self.assertFalse(bad)
        row = msgimages.listing()[0]
        self.assertEqual(row["from_name"], "甲")
        msgimages.delete(row["id"])
        self.assertEqual(msgimages.stats()["n"], 0)
        asyncio.run(sendmsg.send(self.a, self.b, "sms", "", "x", "", fn, images=[PNG]))
        with get_conn() as c:
            c.execute("DELETE FROM messages")
        self.assertEqual(msgimages.stats()["n"], 0)          # 随短信一起删

    def _login(self, role):
        c = self.app.test_client()
        c.get("/p")
        with c.session_transaction() as s:
            tok = s.setdefault("csrf", "tok")
        c.post("/p", data={"code": phonecodes.code_for_role(role["id"]), "_csrf": tok})
        return c, tok

    def test_phone_upload_and_access(self):
        phone_app.loopbridge.run = lambda coro: asyncio.run(coro)
        phone_app._send_fn = lambda: (lambda *a, **k: asyncio.sleep(0, True))
        ca, tok = self._login(self.a)
        r = ca.post(f"/p/me/send", data={"to": str(self.b["id"]), "kind": "sms", "text": "", "_csrf": tok,
                                          "image": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
        rows = msgimages.listing()
        self.assertEqual(len(rows), 1)
        iid = rows[0]["id"]
        self.assertEqual(ca.get(f"/p/img/{iid}").status_code, 200)
        cb, _ = self._login(self.b)
        self.assertEqual(cb.get(f"/p/img/{iid}").status_code, 200)
        cc, _ = self._login(self.c3)
        self.assertEqual(cc.get(f"/p/img/{iid}").status_code, 404)

    def tearDown(self):
        with get_conn() as c:
            c.execute("DELETE FROM seasons")
        set_config("game_day", "")


if __name__ == "__main__":
    unittest.main()
