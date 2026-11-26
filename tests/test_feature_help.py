"""功能简介页（/features/help）：核心功能、辅助功能每张卡片都有一节，「功能与设置」两栏和每张卡片都链过去。

- features/modhelp/guide.py 里的 GUIDE 要跟核心功能 + 非个性化的官方扩展一一对上：新加功能忘了写简介、删了功能留着旧简介都会挂
- 简介里写的指令（反引号包着的）要是真有的指令
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


def _all_commands() -> set[str]:
    """plugins/ 里注册过的指令名和别名（_cmd("名", {"别名"}) / on_command("名", aliases=…)）。"""
    names = set()
    for p in (SRC / "plugins").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:_cmd|cmd|on_command)\(\s*\"([^\"]+)\"(?:\s*,\s*(?:aliases\s*=\s*)?\{([^}]*)\})?", text):
            names.add(m.group(1))
            names.update(re.findall(r"\"([^\"]+)\"", m.group(2) or ""))
    return names


# 不是注册的指令、但在群里照样能发的（按格式认的消息、骰子表达式、带参数的用法）
NOT_COMMANDS = {"短信", "送礼", "提交出场", "结束踩点"}


class GuideData(unittest.TestCase):
    def test_every_card_has_a_section(self):
        from changri.core import extensions
        from changri.features.modhelp.guide import GUIDE
        want = {c["id"] for c in extensions.CORE_CARDS} | {e["id"] for e in extensions.OFFICIAL if not e.get("personal")}
        self.assertEqual(set(GUIDE) - want, set(), "guide.py 里有已经不存在（或是个性化模块）的功能")
        self.assertEqual(want - set(GUIDE), set(), "这些功能在「功能与设置」里有卡片，但 guide.py 没写简介")
        for fid, g in GUIDE.items():
            self.assertEqual(set(g), {"what", "player", "admin"}, fid)
            for k, v in g.items():
                self.assertTrue(v.strip(), f"{fid}.{k} 是空的")
                self.assertEqual(v.count("`") % 2, 0, f"{fid}.{k} 的反引号没配对")

    def test_commands_exist(self):
        from changri.features.modhelp.guide import GUIDE
        cmds = _all_commands() | NOT_COMMANDS
        for fid, g in GUIDE.items():
            for v in g.values():
                for code in re.findall(r"`([^`]+)`", v):
                    head = code.lstrip("/").split()[0]
                    if head.startswith("#") or re.fullmatch(r"[a-z]+", head):
                        continue                                    # 礼物编号、骰子指令（rd / ra / coc 由骰子插件按前缀认）
                    self.assertIn(head, cmds, f"{fid}：简介里写了「{code}」，但没有「{head}」这个指令")


class GuidePages(unittest.TestCase):
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

    def test_page_lists_every_feature(self):
        from changri.features.modhelp.guide import GUIDE
        with db.use_bot(BOT):
            db.set_config("ext:wishes", "1")
            db.set_config("ext:lovemail", "0")
        r = self.c.get(f"/b/{BOT}/features/help")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for fid in GUIDE:
            self.assertIn(f'id="f-{fid}"', html)
        self.assertIn('id="g-core"', html)
        self.assertIn('id="g-aux"', html)
        self.assertIn("<code>挂心愿</code>", html)                              # 反引号变成 <code>
        self.assertNotIn("`", html.split("<body", 1)[-1].split("<script", 1)[0])
        wishes = html.split('id="f-wishes"', 1)[1].split("</article>", 1)[0]
        self.assertIn("已打开", wishes)
        lovemail = html.split('id="f-lovemail"', 1)[1].split("</article>", 1)[0]
        self.assertIn("没打开", lovemail)
        self.assertNotIn(f"/b/{BOT}/lovemail\"", lovemail)                       # 关着的不给管理页链接（会 404）
        self.assertIn(f"/b/{BOT}/extensions?tab=aux#x-lovemail", lovemail)
        season = html.split('id="f-season"', 1)[1].split("</article>", 1)[0]
        self.assertNotIn("没打开", season)                                       # 核心功能不标开关

    def test_extensions_tabs_link_here(self):
        core = self.c.get(f"/b/{BOT}/extensions").get_data(as_text=True)
        self.assertIn(f"/b/{BOT}/features/help#g-core", core)
        self.assertIn(f"/b/{BOT}/features/help#f-season", core)
        self.assertIn('id="x-season"', core)
        aux = self.c.get(f"/b/{BOT}/extensions?tab=aux").get_data(as_text=True)
        self.assertIn(f"/b/{BOT}/features/help#g-aux", aux)
        self.assertIn(f"/b/{BOT}/features/help#f-dinner", aux)
        custom = self.c.get(f"/b/{BOT}/extensions?tab=custom").get_data(as_text=True)
        self.assertIn(f"/b/{BOT}/modules/help#m-longterm", custom)


if __name__ == "__main__":
    unittest.main()
