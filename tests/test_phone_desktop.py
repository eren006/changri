"""小手机桌面：底栏第四格「桌面」，页面是应用图标墙，关着的功能不出图标（关系线显示「未开放」）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "dk.db")

from changri.core import phonecodes, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.phone import app as phone_app  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402


class Desktop(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("phone_codes", "phone_seen", "roles", "role_groups", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")
        roles.create_role("甲", "B1", "P0", "M0")
        phone_app._fails.clear()
        self.c = create_phone_app("k" * 32).test_client()
        self.c.get("/p")
        with self.c.session_transaction() as s:
            tok = s.setdefault("csrf", "tok")
        self.c.post("/p", data={"code": phonecodes.code_for_role(roles.get_role_by_name("甲")["id"]), "_csrf": tok})

    def test_nav_has_desktop(self):
        html = self.c.get("/p/me/discover").get_data(as_text=True)
        self.assertIn('href="/p/me/desktop"', html)
        self.assertIn("桌面</a>", html)

    def test_desktop_page(self):
        set_config("ext:precall", "0")
        set_config("ext:chatroom", "0")
        set_config("ext:groupchat", "0")
        r = self.c.get("/p/me/desktop")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for name in ("小手机", "关系线", "戏录", "RPG"):
            self.assertIn(f"<span>{name}</span>", html)
        self.assertNotIn("<span>前置电话</span>", html)
        self.assertNotIn("<span>邀约</span>", html)
        self.assertIn('aria-current="page"', html)
        set_config("ext:precall", "1")
        set_config("ext:groupchat", "1")
        html = self.c.get("/p/me/desktop").get_data(as_text=True)
        self.assertIn("<span>前置电话</span>", html)
        self.assertIn("<span>邀约</span>", html)

    def test_needs_login(self):
        c = create_phone_app("k" * 32).test_client()
        self.assertEqual(c.get("/p/me/desktop").status_code, 302)


if __name__ == "__main__":
    unittest.main()
