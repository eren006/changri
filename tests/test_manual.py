"""使用手册（features/manual）：每条「我想……」都指到真实存在的设置 / 页面，每一组设置都有地方改。

- 手写的 TASKS 里写的设置项、页面名不能写错；页面名要跟左侧菜单里的一样
- SECTIONS 里每一组都要挂在某张功能卡片上（以前「复盘网页」「特殊道具」「房间复盘」三组没有卡片，后台根本没地方改）
- 挂进别的卡片的组，在那张卡片的设置框里保存时要一起存上
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, webusers  # noqa: E402

BOT = "111111"
SRC = Path(__file__).resolve().parent.parent / "src" / "changri"


class ManualData(unittest.TestCase):
    def test_every_group_has_a_card(self):
        from changri.admin.common import SECTIONS, group_card
        for g, _ in SECTIONS:
            self.assertIsNotNone(group_card(g), f"设置组「{g}」不在任何功能卡片里，后台没地方改")

    def test_tasks_point_to_real_things(self):
        from changri.admin.common import SETTINGS
        from changri.features.manual.admin import NAV, TASKS
        base = (SRC / "admin" / "templates" / "base.html").read_text(encoding="utf-8")
        for ep, (_grp, label) in NAV.items():
            self.assertIn(f"('{ep}','{label}')", base, f"手册里写的「{label}」跟左侧菜单对不上")
        for _t, _l, rows in TASKS:
            for q, target, _n in rows:
                if target[0] == "set":
                    self.assertIn(target[1], SETTINGS, q)
                elif target[0] == "page":
                    self.assertIn(target[1], NAV, q)


class ManualPages(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        from changri.admin import create_app
        self.app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        botconf.add_account(BOT, "a" * 20, "甲号")
        self.c = self.app.test_client()
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self._csrf("/login")})

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def _csrf(self, path):
        return re.search(r'name="_csrf" value="([^"]+)"', self.c.get(path).get_data(as_text=True)).group(1)

    def test_manual_page(self):
        r = self.c.get(f"/b/{BOT}/manual")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("短信冷却、每天能发几条", html)
        self.assertIn(f'href="/b/{BOT}/extensions?open=sms_cooldown_sec"', html)        # 核心卡片直接打开那一项
        self.assertIn("open=sighting_chance", html)
        self.assertIn('href="/update"', html)                                            # 共用页面不带 /b/<机器人>/
        self.assertIn('id="c-play"', html)
        self.assertIn("房间结束时自动把本场戏文", html)                                      # 挂进「约会」卡片的组也列出来
        self.assertIn("使用手册", self.c.get(f"/b/{BOT}/").get_data(as_text=True))          # 左侧菜单

    def test_extra_group_shows_and_saves_in_card(self):
        page = self.c.get(f"/b/{BOT}/extensions").get_data(as_text=True)
        self.assertIn('id="room_auto_review"', page)                                     # 在「约会」卡片的设置框里
        self.assertIn('name="season_view_sms"', page)                                      # 在「季度」卡片里
        self.assertIn(f'/b/{BOT}/manual#c-play', page)
        csrf = self._csrf(f"/b/{BOT}/extensions")
        r = self.c.post(f"/b/{BOT}/settings/约会房间", data={"_csrf": csrf, "room_auto_review": "1", "limit_约会": "2"})
        self.assertEqual(r.status_code, 302)
        with db.use_bot(BOT):
            self.assertEqual(db.get_config("room_auto_review", ""), "1")
            self.assertEqual(db.get_config("limit_约会", ""), "2")
        page = self.c.get(f"/b/{BOT}/manual").get_data(as_text=True)
        self.assertIn("你改成了", page)


if __name__ == "__main__":
    unittest.main()
