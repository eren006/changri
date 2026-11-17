"""季末结算海报：各皮肤都能画、尺寸对、素材下载不了退回纯色纸；季末报告先发文字、再单独发图，出错改发文字。"""
import asyncio
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

from PIL import Image  # noqa: E402

from changri.core import roles, seasonreport  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.core.render import season_poster as P  # noqa: E402
from changri.platform import remote_media  # noqa: E402


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


class PosterRenderTest(unittest.TestCase):
    def setUp(self):
        P.clear_cache()

    def tearDown(self):
        P.clear_cache()

    def test_every_skin_renders(self):
        for sid in P.SKINS:
            im = _open(P.render(P.SAMPLE, sid))
            self.assertEqual(im.format, "JPEG", sid)
            self.assertEqual(im.width, 1080, sid)
            self.assertGreaterEqual(im.height, 900, sid)

    def test_unknown_skin_falls_back_to_antique(self):
        self.assertEqual(P.get_skin("nope")[0], "antique")
        self.assertEqual(P.get_skin("")[0], "antique")
        self.assertEqual(P.render_image(P.SAMPLE, "nope").tobytes(), P.render_image(P.SAMPLE, "antique").tobytes())

    def test_missing_blocks_make_shorter_poster(self):
        full = P.render_image(P.SAMPLE, "rose").height
        small = P.render_image({"role_name": "甲", "sessions": 1}, "rose").height
        self.assertLess(small, full)
        self.assertGreaterEqual(small, 900)

    def test_assets_unavailable_plain_paper(self):
        def boom(rel):
            raise remote_media.MediaError("下载失败（URLError）")
        with mock.patch.object(remote_media, "local_path", side_effect=boom):
            self.assertFalse(P.prefetch("lace"))
            for sid in P.SKINS:
                im = P.render_image(P.SAMPLE, sid)
                self.assertEqual(im.width, 1080)
                self.assertEqual(im.getpixel((5, 5)), P._rgb(P.SKINS[sid]["paper"])[:3], sid)      # 纯色纸

    def test_downloads_from_site_when_not_in_package(self):
        """安装包里没有皮肤图：从主页下载、校验 sha256、缓存到数据目录"""
        site, data, pkg, media = (Path(tempfile.mkdtemp()) for _ in range(4))
        src = remote_media.PKG_DIR / "assets/skins/rose.png"
        (site / "assets/skins").mkdir(parents=True)
        (site / "assets/skins/rose.png").write_bytes(src.read_bytes())
        real = json.loads((remote_media.PKG_DIR / "remote_media.json").read_text())
        man = pkg / "remote_media.json"
        man.write_text(json.dumps(real))
        from changri.platform import layout
        with mock.patch.object(remote_media, "PKG_DIR", pkg), mock.patch.object(remote_media, "MANIFEST", man), \
                mock.patch.object(remote_media, "DATA_DIR", data), mock.patch.object(layout, "MEDIA_DIR", media), \
                mock.patch.dict(os.environ, {"CHANGRI_MEDIA_URL": site.as_uri() + "/"}):
            self.assertTrue(P.prefetch("rose"))
            self.assertTrue((data / "media/assets/skins/rose.png").is_file())
            P.clear_cache()
            self.assertFalse(P.prefetch("sage"))           # 主页上没有：退回纯色纸，不卡住
            self.assertEqual(P.render_image(P.SAMPLE, "sage").getpixel((5, 5)), P._rgb(P.SKINS["sage"]["paper"])[:3])

    def test_skins_are_remote_not_packaged(self):
        """皮肤图都登记成按需下载（packaging/build.py 的 package_files 会跳过它们，不进安装包）"""
        man = json.loads((remote_media.PKG_DIR / "remote_media.json").read_text())
        for rel in P.SKIN_FILES:
            self.assertIn(rel, man)
            self.assertTrue((remote_media.PKG_DIR / rel).is_file(), rel)


class SeasonReportPosterTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("room_lines", "room_members", "rooms", "lovemails", "messages", "seasons", "role_groups", "roles"):
                c.execute(f"DELETE FROM {t}")
        for i, n in enumerate("甲乙丙"):
            roles.create_role(n, "B1", f"P{i}", f"M{i}")
        with get_conn() as c:
            self.a, self.b, self.c = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            ts = int(time.mktime(time.strptime("2026-11-01 22:30", "%Y-%m-%d %H:%M")))
            for frm, to, kind, sign in ((self.a, self.b, "sms", ""), (self.a, self.b, "sms", ""), (self.a, self.c, "sms", ""),
                                        (self.b, self.a, "sms", "神秘人"), (self.c, self.a, "gift", "")):
                c.execute("INSERT INTO messages(kind,from_role_id,to_role_id,created_ts,status,sign) VALUES(?,?,?,?, 'sent', ?)", (kind, frm, to, ts, sign))
            c.execute("INSERT INTO rooms(id,kind,status,created_ts) VALUES(1,'约会','ended',?)", (ts,))
            c.executemany("INSERT INTO room_members(room_id,role_id) VALUES(1,?)", [(self.a,), (self.b,)])
            for i, (rid, name, text) in enumerate(((self.a, "甲", "甲：雨停了。"), (self.b, "乙", "乙：嗯。"), (self.a, "甲", "（场外）等我一下"))):
                c.execute("INSERT INTO room_lines(room_id,ts,role_id,name,text,is_rp,chars) VALUES(1,?,?,?,?,?,?)",
                          (ts + i, rid, name, text, int(i < 2), 4 if i < 2 else 0))
            c.execute("INSERT INTO seasons(name,start_date,end_date,status,ended_ts) VALUES('春季','2026-10-01','2026-11-01','ended',?)", (ts,))

    def test_stats_fields(self):
        d = seasonreport.stats(self.a)
        self.assertEqual((d["sessions"], d["sms_sent"], d["sms_recv"], d["gift_recv"]), (1, 3, 1, 1))
        self.assertEqual(d["top_sms_sent_to"], [["乙", 2], ["丙", 1]])
        self.assertEqual(d["top_sms_recv_from"], [["神秘人", 1]])          # 自定义署名的不露真名
        self.assertEqual(d["top_gift_recv_from"], [["丙", 1]])
        self.assertEqual((d["peak_slot"], d["time_title"]), ("22:00–24:00", "深夜电台长"))
        self.assertEqual(d["best_excerpt"], [{"role": "甲", "text": "雨停了。"}, {"role": "乙", "text": "嗯。"}])   # 场外闲聊不算
        _open(P.render(dict(d, show_name="春季"), "postal"))

    def test_no_excerpt_without_review(self):
        with get_conn() as c:
            c.execute("DELETE FROM room_lines")
        self.assertNotIn("best_excerpt", seasonreport.stats(self.a))

    def _run(self, send_image, render_ok=True):
        log = []

        async def send(bot, gid, text):
            log.append(("text", gid, text))
            return "m1"

        async def push_ex(role_id, text, send_fn, at=True):
            await send_fn("B1", f"G{role_id}", text)
            return True, f"G{role_id}", "B1", "m1"

        async def push(role_id, text, send_fn, at=True):
            await send_fn("B1", f"G{role_id}", text)
            return True, f"G{role_id}"
        patches = [mock.patch.object(seasonreport, "push_to_role_ex", push_ex), mock.patch.object(seasonreport, "push_to_role", push),
                   mock.patch.object(seasonreport, "PAUSE", 0)]
        if not render_ok:
            patches.append(mock.patch.object(P, "render", side_effect=RuntimeError("坏了")))
        for p in patches:
            p.start()
        try:
            res = asyncio.run(seasonreport.send_all(send, send_image(log) if send_image else None))
        finally:
            for p in reversed(patches):
                p.stop()
        return res, log

    def test_text_then_separate_image(self):
        def mk(log):
            async def send_image(bot, gid, data):
                self.assertIsInstance(data, bytes)
                log.append(("image", gid, _open(data).width))
            return send_image
        (ok, bad), log = self._run(mk)
        self.assertEqual((ok, bad), (3, 0))
        mine = [x for x in log if x[1] == f"G{self.a}"]
        self.assertEqual([x[0] for x in mine], ["text", "image"])
        self.assertIn("「春季」已落幕", mine[0][2])
        self.assertNotIn("[CQ:", mine[0][2])
        self.assertEqual(mine[1][2], 1080)

    def test_image_send_fails_falls_back_to_text(self):
        def mk(log):
            async def send_image(bot, gid, data):
                raise RuntimeError("发图失败")
            return send_image
        (ok, bad), log = self._run(mk)
        self.assertEqual((ok, bad), (3, 0))
        mine = [x[2] for x in log if x[1] == f"G{self.a}"]
        self.assertEqual(len(mine), 2)
        self.assertIn("本季小结", mine[1])

    def test_render_fails_sends_summary_only(self):
        def mk(log):
            async def send_image(bot, gid, data):
                log.append(("image", gid, 0))
            return send_image
        (ok, bad), log = self._run(mk, render_ok=False)
        self.assertEqual(ok, 3)
        self.assertFalse([x for x in log if x[0] == "image"])
        self.assertTrue(all("本季小结" in x[2] for x in log))

    def test_without_send_image_text_only(self):
        (ok, bad), log = self._run(None)
        self.assertEqual(ok, 3)
        self.assertTrue(all("本季小结" in x[2] for x in log))


class PosterAdminTest(unittest.TestCase):
    def test_skin_dropdown_and_preview(self):
        from changri.admin import create_app, field_view
        from changri.core import webusers
        f = field_view("season_poster_skin", "季末海报的样式（说明）", "antique", "bogus")
        self.assertEqual((f["kind"], f["value"], len(f["options"])), ("choice", "antique", 7))
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        webusers.create_user("boss", "longenough1", "owner")
        set_config("season_poster_skin", "lace")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        html = c.get("/extensions", follow_redirects=True).get_data(as_text=True)      # 跑过别的测试、有好几个机器人时会先跳到 /b/<id>/
        self.assertIn('<select id="season_poster_skin" name="season_poster_skin">', html)
        self.assertIn('<option value="lace" selected>蕾丝兔子</option>', html)
        self.assertIn("预览海报", html)
        r = c.get("/season/poster-preview?skin=sage", follow_redirects=True)
        self.assertEqual((r.status_code, r.mimetype), (200, "image/jpeg"))
        self.assertEqual(_open(r.data).width, 1080)


if __name__ == "__main__":
    unittest.main()
