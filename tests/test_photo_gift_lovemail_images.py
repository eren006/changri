"""礼物和心动信附图：照片礼物不走礼品店、不进热度；心动信附图的投稿、撤回、派送、曝光、小手机权限、按天清理；存图前去掉 EXIF。"""
import asyncio
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "pg.db"))

from changri.core import gifts, lovemail, messaging, msgimages, phonecodes, roles, sendmsg  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.phone import app as phone_app  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def run(coro):
    return asyncio.run(coro)


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("seasons", "message_images", "lovemail_images", "lovemails", "gift_shop", "gift_unlocks", "phone_codes", "phone_seen",
                      "messages", "blocks", "roles", "role_groups", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")
        set_config("gift_shop_seeded", "1")
        set_config("gift_cooldown_sec", "0")
        for n, i in (("甲", 1), ("乙", 2), ("丙", 3)):
            roles.create_role(n, "B1", f"G{i}", f"M{i}")
        self.a, self.b, self.c = (roles.get_role_by_name(n) for n in ("甲", "乙", "丙"))
        self.sent, self.pics = [], []

    async def send(self, bot, group, text, **k):
        self.sent.append((group, text))
        return "mid"

    async def send_image(self, bot, group, data):
        self.pics.append((group, data))
        return "img"


class PhotoGift(Base):
    def gift(self, name, images=(PNG,), by_name=False):
        return run(sendmsg.send(self.a, self.b, "gift", name, "", "", self.send, list(images), gift_by_name_ok=by_name))

    def test_free_name_and_default(self):
        ok, _ = self.gift("枣泥泥")
        self.assertTrue(ok)
        ok, _ = self.gift("")
        self.assertTrue(ok)
        with get_conn() as c:
            rows = c.execute("SELECT title, photo FROM messages ORDER BY id").fetchall()
        self.assertEqual([(r["title"], r["photo"]) for r in rows], [("枣泥泥", 1), (sendmsg.PHOTO_GIFT, 1)])
        self.assertEqual(msgimages.stats()["n"], 2)
        self.assertTrue(any("送了你「枣泥泥」" in t for _, t in self.sent))
        self.assertEqual(gifts.heat(), {})                                      # 照片礼物不算热度
        got = {(r["title"], r["photo"]) for r in gifts.received(self.b["id"])}
        self.assertIn(("枣泥泥", 1), got)

    def test_shop_gift_cannot_have_image(self):
        from changri.core import extensions
        extensions.set_enabled("giftshop", True)
        gifts.add("玫瑰")
        gid = gifts.items()[0]["id"]
        gifts.unlock(self.a["id"], gid)
        for name, by_name in (("#001", False), (str(gid), False), ("玫瑰", True), ("玫瑰", False)):
            ok, why = self.gift(name, by_name=by_name)
            self.assertFalse(ok, name)
            self.assertIn("礼品店的礼物不能附图片", why)
        ok, _ = self.gift(f"#{gid:03d}", images=())                              # 不带图照常送
        self.assertTrue(ok)
        set_config("gift_only_preset", "1")                                     # 只能送礼品店的：照片礼物不受限
        ok, why = self.gift("自己拍的猫")
        self.assertTrue(ok, why)
        self.assertEqual(gifts.heat(), {"玫瑰": 1})

    def test_switches(self):
        set_config("gift_image_enabled", "0")
        ok, why = self.gift("猫")
        self.assertFalse(ok)
        self.assertIn("礼物不能附图片", why)
        set_config("gift_image_enabled", "1")
        set_config("sms_image_enabled", "0")                                    # 总开关关了也不行
        self.assertFalse(self.gift("猫")[0])
        self.assertEqual(msgimages.stats()["n"], 0)


class StripMeta(unittest.TestCase):
    def test_exif_removed(self):
        from PIL import Image
        im = Image.new("RGB", (40, 20), "red")
        exif = Image.Exif()
        exif[0x010F] = "SomePhone"          # Make
        exif[0x0112] = 6                    # 方向：要转 90 度
        buf = io.BytesIO()
        im.save(buf, "JPEG", exif=exif.tobytes())
        out = msgimages.strip_meta(buf.getvalue())
        got = Image.open(io.BytesIO(out))
        self.assertFalse(dict(got.getexif()))
        self.assertEqual(got.size, (20, 40))                                    # 方向转到像素上了
        self.assertEqual(msgimages.strip_meta(PNG), PNG)                         # 不是真图片：原样
        plain = io.BytesIO()
        Image.new("RGB", (4, 4)).save(plain, "PNG")
        self.assertEqual(msgimages.strip_meta(plain.getvalue()), plain.getvalue())   # 没 EXIF：不重存


class LovemailImages(Base):
    def setUp(self):
        super().setUp()
        set_config("lovemail_default_limit", "5")

    def test_off_by_default(self):
        ok, msg, _ = lovemail.add_mail(self.a["id"], self.b["id"], "匿名", "hi", [PNG])
        self.assertFalse(ok)
        self.assertIn("不能附图片", msg)
        self.assertEqual(lovemail.my_pool(self.a["id"]), [])
        ok, msg, _ = lovemail.add_mail(self.a["id"], self.b["id"], "匿名", "")        # 没图也没字：照旧不行
        self.assertFalse(ok)

    def test_post_revoke_deliver_expose(self):
        set_config("lovemail_image_enabled", "1")
        set_config("lovemail_expose", "1")
        set_config("lovemail_expose_chance", "100")
        self.assertTrue(lovemail.add_mail(self.a["id"], self.b["id"], "小风", "", [PNG])[0])      # 只附图
        self.assertTrue(lovemail.add_mail(self.c["id"], self.b["id"], "匿名", "第二封")[0])
        self.assertTrue(lovemail.add_mail(self.a["id"], self.c["id"], "匿名", "撤掉的", [PNG])[0])
        self.assertEqual(msgimages.mail_stats()["n"], 2)
        lovemail.revoke(self.a["id"], 2)                                         # 撤回：图一起删
        self.assertEqual(msgimages.mail_stats()["n"], 1)
        res = run(lovemail.deliver_all(self.send, ("B1", "ANN"), send_image=self.send_image))
        self.assertEqual(res["success"], 2)
        pkg = [t for g, t in self.sent if g == "G2"]
        self.assertIn(lovemail.IMAGE_ONLY, pkg[0])
        self.assertIn("📎 附了一张图", pkg[0])
        self.assertTrue(any("第 1 封信件的附图" in t for t in pkg))              # 包里两封：说明是第几封的
        self.assertEqual(self.pics, [("G2", PNG)])                               # 图只发到收信人的群
        pub = [t for g, t in self.sent if g == "ANN" and "寄给" in t][0]
        self.assertIn(lovemail.IMAGE_ONLY, pub)                                  # 曝光只有文字

    def test_single_letter_no_label_and_image_failure(self):
        set_config("lovemail_image_enabled", "1")
        lovemail.add_mail(self.a["id"], self.b["id"], "匿名", "一封", [PNG])

        async def broken(*a):
            raise RuntimeError("bot offline")
        res = run(lovemail.deliver_all(self.send, None, send_image=broken))
        self.assertEqual(res["success"], 1)                                      # 图发不出去，信照样算送达
        self.assertFalse(any("附图（署名" in t for _, t in self.sent))
        self.assertEqual(lovemail.pool_all(), [])

    def test_purge_keeps_unsent(self):
        set_config("lovemail_image_enabled", "1")
        lovemail.add_mail(self.a["id"], self.b["id"], "匿名", "x", [PNG])
        lovemail.add_mail(self.a["id"], self.c["id"], "匿名", "y", [PNG])
        with get_conn() as c:
            c.execute("UPDATE lovemail_images SET created_ts=?", (int(time.time()) - 10 * 86400,))
            c.execute("UPDATE lovemails SET status='sent' WHERE to_role_id=?", (self.c["id"],))
        self.assertEqual(msgimages.mail_delete_older_than(3), 1)                 # 只删派送过的
        self.assertEqual(msgimages.mail_stats()["n"], 1)


class LovemailPhone(Base):
    def setUp(self):
        super().setUp()
        set_config("lovemail_image_enabled", "1")
        phone_app._fails.clear()
        self.app = create_phone_app("k" * 32)

    def _login(self, role):
        c = self.app.test_client()
        c.get("/p")
        with c.session_transaction() as s:
            tok = s.setdefault("csrf", "tok")
        c.post("/p", data={"code": phonecodes.code_for_role(role["id"]), "_csrf": tok})
        return c, tok

    def test_upload_and_who_can_see(self):
        ca, tok = self._login(self.a)
        r = ca.post("/p/me/lovemail", data={"to": "乙", "content": "", "signature": "风", "_csrf": tok,
                                            "image": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(lovemail.my_pool(self.a["id"])), 1)
        iid = msgimages.mail_listing()[0]["id"]
        self.assertEqual(ca.get(f"/p/me/lovemail/img/{iid}").status_code, 200)      # 寄信人随时能看
        cb, _ = self._login(self.b)
        self.assertEqual(cb.get(f"/p/me/lovemail/img/{iid}").status_code, 404)      # 还没派送：收信人看不到
        run(lovemail.deliver_all(self.send, None, send_image=self.send_image))
        self.assertEqual(cb.get(f"/p/me/lovemail/img/{iid}").status_code, 200)
        self.assertIn(f"/p/me/lovemail/img/{iid}", cb.get("/p/me/lovemail").get_data(as_text=True))
        cc, _ = self._login(self.c)
        self.assertEqual(cc.get(f"/p/me/lovemail/img/{iid}").status_code, 404)

    def test_write_page_shows_picker_only_when_on(self):
        ca, _ = self._login(self.a)
        self.assertIn('name="image"', ca.get("/p/me/lovemail?view=write").get_data(as_text=True))
        set_config("lovemail_image_enabled", "0")
        self.assertNotIn('name="image"', ca.get("/p/me/lovemail?view=write").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
