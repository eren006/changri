"""后台手机可用性：顶栏写当前机器人、全站共用的复制按钮、出错提示变红 + 填过的内容留着。"""
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

TPL = Path(__file__).resolve().parent.parent / "src" / "changri" / "admin" / "templates"


class AdminUx(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        from changri.admin import create_app
        self.app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        botconf.add_account("111111", "a" * 20, "甲号")
        self.c = self.app.test_client()
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self._csrf("/login")})

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def _csrf(self, path):
        h = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', h).group(1)

    def test_mobile_bar_shows_bot_only_when_several(self):
        h = self.c.get("/b/111111/god").get_data(as_text=True)
        self.assertNotIn('class="mtitle mt2"', h)          # 只有一个机器人：不用写
        botconf.add_account("222222", "b" * 20, "乙号很长很长的名字")
        h = self.c.get("/b/111111/god").get_data(as_text=True)
        self.assertIn('class="mtitle mt2"', h)
        self.assertIn('<span class="mt-b">甲号', h)
        self.assertIn("data-openmenu", h)
        h = self.c.get("/?pick=1").get_data(as_text=True)    # 不在某个机器人里：不写
        self.assertNotIn('class="mtitle mt2"', h)

    def test_copy_helper_is_the_only_clipboard_code(self):
        h = self.c.get("/b/111111/decks").get_data(as_text=True)
        self.assertIn("window.copyText=", h)
        self.assertIn("复制不了，请长按选中后复制", h)
        for p in TPL.glob("*.html"):
            if p.name == "base.html":
                continue
            s = p.read_text(encoding="utf-8")
            self.assertNotIn("navigator.clipboard", s, p.name)
            self.assertNotIn("execCommand", s, p.name)
        for path in ("/b/111111/decks", "/b/111111/giftshop", "/b/111111/extensions?tab=ai", "/b/111111/extensions?tab=api", "/https", "/bots", "/b/111111/tools"):
            r = self.c.get(path)
            self.assertIn(r.status_code, (200, 302, 404), path)   # 404：这个扩展默认关着
            if r.status_code == 200 and "复制" in r.get_data(as_text=True):
                self.assertIn("copyText(", r.get_data(as_text=True), path)

    def test_error_flash_is_red_alert_and_success_is_status(self):
        from flask import flash, render_template_string
        with self.app.test_request_context("/b/111111/"):
            flash("没保存：名字不能空", "error")
            flash("❌ 老写法也算错")
            flash("已保存")
            h = render_template_string('{% extends "base.html" %}{% block body %}<form method="post"></form>{% endblock %}')
        self.assertIn('<p class="flash bad" role="alert">没保存：名字不能空</p>', h)
        self.assertIn('<p class="flash bad" role="alert">❌ 老写法也算错</p>', h)
        self.assertIn('<p class="flash" role="status">已保存</p>', h)
        self.assertIn("changri-keep", h)                    # 表单内容 / 滚动位置那段脚本在

    def test_route_validation_flash_uses_error_category(self):
        tok = self._csrf("/b/111111/lovemail")
        h = self.c.post("/b/111111/lovemail/time", data={"_csrf": tok, "at": "25点"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn('class="flash bad" role="alert">时间要写成', h)
        h = self.c.post("/b/111111/lovemail/time", data={"_csrf": tok, "at": "21:30"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn('class="flash" role="status">已改', h)

if __name__ == "__main__":
    unittest.main()
