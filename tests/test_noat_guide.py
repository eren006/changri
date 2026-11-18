"""「不用 @ 也能回」：说明页写清进入路径；总览上有入口卡片，叉掉后不再显示，说明页和左侧菜单还在；备用群池那一步链过去。"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "noat.db")
os.environ["QQ_BOTS"] = "[]"
import changri.admin as admin_mod  # noqa: E402

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import webusers  # noqa: E402
from changri.core.db import get_config, get_conn, init_db  # noqa: E402


def token(client, path="/no-at"):
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', client.get(path).get_data(as_text=True)).group(1)


class NoAtGuide(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("web_users", "config"):
                c.execute(f"DELETE FROM {t}")
        admin_mod._fails.clear()
        self.c = create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(self.c, "/login")})

    def test_page_has_path_and_copy_text(self):
        html = self.c.get("/no-at").get_data(as_text=True)
        for s in ("群机器人", "管理", "获取群内全部消息", "机器人主动在群聊内发言", "只有群主能改", 'id="na-text"'):
            self.assertIn(s, html)

    def test_dashboard_card_dismiss(self):
        self.assertIn('class="card noat"', self.c.get("/").get_data(as_text=True))
        html = self.c.post("/no-at/dismiss", data={"_csrf": token(self.c)}, follow_redirects=True).get_data(as_text=True)
        self.assertNotIn('class="card noat"', html)
        self.assertEqual(get_config("noat_tip_done"), "1")
        self.assertNotIn('class="card noat"', self.c.get("/").get_data(as_text=True))
        self.assertEqual(self.c.get("/no-at").status_code, 200)                # 叉掉了说明页照样能看

    def test_dismiss_needs_csrf(self):
        self.c.post("/no-at/dismiss", data={})
        self.assertNotEqual(get_config("noat_tip_done"), "1")

    def test_menu_entry(self):
        base = (Path(__file__).resolve().parent.parent / "src/changri/admin/templates/base.html").read_text(encoding="utf-8")
        self.assertIn("('noat_page','不用 @ 也能回')", base)                     # 左侧「通用」里有入口（进了机器人才显示菜单）

    def test_pool_links_here(self):
        self.assertIn('href="/no-at"', self.c.get("/backup-pool").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
