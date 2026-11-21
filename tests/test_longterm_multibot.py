"""长期群后台在接了机器人的情况下（/b/<AppID>/…）每一页都打得开。

0.22.13 线上出过 500：base.html 在接了机器人时会 {% set groups / subs / extras %}（左侧菜单），
页面自己传进来的同名变量被盖掉，长期群列表页拿到的是菜单的元组。没接机器人时这几个变量是空的，所以本地预览看不出来。
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


class LongtermMultiBotPages(unittest.TestCase):
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

    def _seed(self):
        """在这个机器人的工作区里造数据：打开模块、建长期群、注册、交字数（等批准）、道具、配方、动态。"""
        from changri.core import longterm as lt
        from changri.core.db import set_config
        with db.use_bot(BOT):
            set_config("ext:longterm", "1")
            gid, _ = lt.create_group("春日")
            lt.update_group(gid, kpi_chars=10, checkin_on=True, checkin_rewards="金币 1-3")
            lt.set_attr_def(gid, "HP", 10)
            a, _ = lt.register(gid, "OA", "阿青")
            lt.tally(gid, [{"nickname": "阿青", "text": "一二三四五"}, {"nickname": "路人", "text": "六七"}], submitter=a, pending=True)
            lt.tally(gid, [{"nickname": "阿青", "text": "直接入账的一段"}], submitter=None)
            lt.save_item_def(gid, "药水", "回血", "HP+1", on_sale=True, price=1)
            lt.save_recipe(gid, "大药水", "药水×2", "大药水×1")
            return gid, a["id"], lt.submissions(gid)[-1]["id"]

    def test_every_longterm_page_renders_under_bot_prefix(self):
        gid, mid, sid = self._seed()
        pages = [f"/b/{BOT}/longterm", f"/b/{BOT}/longterm/{gid}", f"/b/{BOT}/longterm/{gid}?kind=字数",
                 f"/b/{BOT}/longterm/{gid}?tab=players", f"/b/{BOT}/longterm/{gid}?tab=players&kpi=no",
                 f"/b/{BOT}/longterm/{gid}?tab=words", f"/b/{BOT}/longterm/{gid}?tab=items",
                 f"/b/{BOT}/longterm/{gid}?tab=items&item=药水", f"/b/{BOT}/longterm/{gid}?tab=items&recipe=大药水",
                 f"/b/{BOT}/longterm/{gid}?tab=settings", f"/b/{BOT}/longterm/member/{mid}", f"/b/{BOT}/longterm/sub/{sid}"]
        for p in pages:
            r = self.c.get(p)
            self.assertEqual(r.status_code, 200, p)
        html = self.c.get(f"/b/{BOT}/longterm").get_data(as_text=True)
        self.assertIn("春日", html)
        self.assertIn("1 次字数等批准", html)
        words = self.c.get(f"/b/{BOT}/longterm/{gid}?tab=words").get_data(as_text=True)
        self.assertIn(f"#{sid}", words)
        self.assertIn("等批准", words)
        items = self.c.get(f"/b/{BOT}/longterm/{gid}?tab=items").get_data(as_text=True)
        self.assertIn("药水", items)
        self.assertIn("大药水", items)
        nav = self.c.get(f"/b/{BOT}/longterm/{gid}").get_data(as_text=True)
        self.assertIn(">长期群<", nav)                                         # 左侧菜单那一节打开模块后出现


if __name__ == "__main__":
    unittest.main()


class ModulesHelpPage(LongtermMultiBotPages):
    def test_every_longterm_page_renders_under_bot_prefix(self):      # 父类的那个不重复跑
        pass

    def test_help_page_and_corner_buttons(self):
        """个性化模块介绍页：四个模块各一节，标出开没开；各模块页面右上角有「帮助」链到对应的一节。"""
        with db.use_bot(BOT):
            from changri.core.db import set_config
            set_config("ext:longterm", "1")
        html = self.c.get(f"/b/{BOT}/modules/help").get_data(as_text=True)
        for anchor in ("m-longterm", "m-paidan", "m-letters", "m-storycal"):
            self.assertIn(f'id="{anchor}"', html)
        self.assertIn("已打开", html)                                           # 长期群开着
        self.assertIn("没打开", html)                                           # 别的关着
        self.assertIn(f"/b/{BOT}/longterm", html)                               # 开着的给「打开长期群」
        page = self.c.get(f"/b/{BOT}/longterm").get_data(as_text=True)
        self.assertIn('class="mod-help"', page)
        self.assertIn("/modules/help#m-longterm", page)
        self.assertNotIn('class="mod-help"', self.c.get(f"/b/{BOT}/season").get_data(as_text=True))   # 别的页面没有
        self.assertIn("/modules/help", self.c.get(f"/b/{BOT}/extensions?tab=custom").get_data(as_text=True))
