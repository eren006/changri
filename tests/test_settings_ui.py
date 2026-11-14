"""设置页：1/0 的项变开关，数字项变数字框，其余仍是文本框。"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "s.db")

from admin import SECTIONS, create_app, field_view  # noqa: E402
from core import webusers  # noqa: E402
from core.db import get_config, get_conn, init_db  # noqa: E402


class FieldView(unittest.TestCase):
    def test_kinds(self):
        f = field_view("wish_public_send", "挂心愿时同步发到公告群（1 开 / 0 关）", "0", "1")
        self.assertEqual((f["kind"], f["label"], f["checked"], f["hint"]), ("bool", "挂心愿时同步发到公告群", True, ""))
        f = field_view("pool_auto_free", "房间结束后群自动放回池（1 是 / 0 否，否 = 要管理员确认群里的人清空了才放回）", "0", "0")
        self.assertEqual((f["kind"], f["checked"]), ("bool", False))
        self.assertIn("要管理员确认", f["hint"])
        f = field_view("room_expire_hours", "房间开了多久自动结束（小时，0 = 不自动结束）", "24", "0")
        self.assertEqual((f["kind"], f["unit"], f["hint"]), ("number", "小时", "0 = 不自动结束"))
        f = field_view("sms_cooldown_sec", "短信冷却（秒，0 = 不限）", "60", "60")
        self.assertEqual((f["kind"], f["unit"]), ("number", "秒"))
        f = field_view("gift_public_chance", "礼物公开概率（%）", "50", "50")
        self.assertEqual((f["kind"], f["unit"], f["hint"]), ("number", "%", ""))
        self.assertEqual(field_view("hello_text", "/ping 的回复", "收到。", "收到。")["kind"], "text")
        self.assertEqual(field_view("lovemail_delivery_time", "每天派送时间（HH:MM）", "22:00", "22:00")["kind"], "text")

    def test_every_one_zero_setting_is_a_switch(self):
        for _, group in SECTIONS:
            for key, (label, default) in group.items():
                if "1 开 / 0 关" in label or "1 是 / 0 否" in label:
                    self.assertEqual(field_view(key, label, default, default)["kind"], "bool", key)


class SettingsPage(unittest.TestCase):
    def test_switch_roundtrip(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
            c.execute("DELETE FROM config")
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        html = c.get("/extensions").get_data(as_text=True)
        self.assertIn('class="wide setrow"', html)
        self.assertIn('type="number"', html)
        t = tok("/extensions")
        # 浏览器行为：勾选的开关同时提交 1 和 0（1 在前），没勾的只提交 0
        c.post("/settings/约会房间", data={"_csrf": t, "wish_public_send": ["1", "0"], "pool_auto_free": ["0"], "room_expire_hours": "12"})
        self.assertEqual(get_config("wish_public_send"), "1")
        self.assertEqual(get_config("pool_auto_free"), "0")
        self.assertEqual(get_config("room_expire_hours"), "12")


if __name__ == "__main__":
    unittest.main()


class NoDuplicateSwitches(unittest.TestCase):
    def test_outer_toggles_not_repeated_inside(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        html = c.get("/extensions").get_data(as_text=True)
        for key in ("lovemail_enabled", "rel_enabled", "dice_enabled"):
            self.assertNotIn(f'name="{key}"', html, key)
        self.assertIn('name="lovemail_delivery_time"', html)               # 其他心动信设置还在
        r = c.post("/lovemail/open", data={"_csrf": tok("/lovemail")})         # 关掉信箱
        self.assertEqual(get_config("lovemail_enabled"), "0")
        c.post("/lovemail/open", data={"_csrf": tok("/lovemail"), "on": "1"})
        self.assertEqual(get_config("lovemail_enabled"), "1")
