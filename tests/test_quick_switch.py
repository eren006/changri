"""总览的快捷开关和「需要处理」里的一键按钮：改的就是原页面上那个开关，两边状态一致。"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "quick.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

from changri.admin import create_app, quick  # noqa: E402
from changri.core import godmode as god_core, sheets, webusers  # noqa: E402
from changri.core.db import get_config, get_conn, init_db, set_config  # noqa: E402


def setup_data(running=True):
    init_db()
    with get_conn() as c:
        for t in ("sheet_images", "sheets", "roles", "config", "web_users", "seasons", "global_perms"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:  # noqa: BLE001  有的表可能不存在
                pass
        ids = [c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES(?,?,1)", (n, f"M{n}")).lastrowid for n in ("甲", "乙")]
        c.execute("INSERT INTO roles(name,member_openid,created_ts,is_npc) VALUES('路人','MN',1,1)")
        if running:
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
    return ids


def client():
    webusers.create_user("boss", "longenough1", "owner")
    c = create_app().test_client()
    tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
    c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
    return c, tok


class Switches(unittest.TestCase):
    def test_same_setting_both_ways(self):
        """总览改了，原页面跟着变；原页面改了，总览跟着变。"""
        setup_data()
        c, tok = client()
        self.assertFalse(quick.get_switch("gongzhi"))
        self.assertIn('id="quick"', c.get("/").get_data(as_text=True))
        c.post("/quick/gongzhi", data={"_csrf": tok("/"), "on": "1"})
        self.assertEqual(get_config("gongzhi_public"), "1")
        self.assertTrue(sheets.public())
        self.assertIn("展示公知：开", c.get("/relations?view=sheets").get_data(as_text=True))
        c.post("/sheets/public", data={"_csrf": tok("/relations?view=sheets"), "on": "0"})   # 原页面关掉
        self.assertFalse(quick.get_switch("gongzhi"))
        c.post("/quick/gongzhi", data={"_csrf": tok("/")})                              # 复选框没勾 = 关
        self.assertFalse(quick.get_switch("gongzhi"))

        c.post("/quick/lovemail", data={"_csrf": tok("/"), "on": "0"})
        self.assertEqual(get_config("lovemail_enabled"), "0")
        c.post("/lovemail/open", data={"_csrf": tok("/lovemail"), "on": "1"})
        self.assertTrue(quick.get_switch("lovemail"))

        c.post("/quick/autoday", data={"_csrf": tok("/"), "on": "1"})
        self.assertEqual(get_config("auto_day"), "1")
        self.assertTrue(get_config("auto_day_last", ""))                               # 和季度页一样：从今天开始算

        c.post("/quick/god:sms", data={"_csrf": tok("/"), "on": "0"})
        self.assertTrue(god_core.global_off("sms"))
        c.post("/quick/god:sms", data={"_csrf": tok("/"), "on": "1"})
        self.assertFalse(god_core.global_off("sms"))

        self.assertEqual(c.post("/quick/nope", data={"_csrf": tok("/"), "on": "1"}).status_code, 404)


class MoreGroups(unittest.TestCase):
    def test_auto_send_switches(self):
        """「群里会自动发什么」：开关改的就是「功能与设置」里那一项；要填字的只显示现值，链接直接打开那一项。"""
        setup_data()
        c, tok = client()
        html = c.get("/").get_data(as_text=True)
        self.assertIn("群里会自动发什么", html)
        self.assertIn("礼物公开播报", html)
        self.assertIn("open=announce_frequency", html)                                # 频率：点了打开那一项
        self.assertFalse(quick.get_switch("cfg:gift_public_send"))                      # 默认关
        c.post("/quick/cfg:gift_public_send", data={"_csrf": tok("/"), "on": "1"})
        self.assertEqual(get_config("gift_public_send"), "1")
        self.assertTrue(quick.get_switch("cfg:lovemail_notice5"))                       # 默认开
        c.post("/quick/cfg:lovemail_notice5", data={"_csrf": tok("/")})
        self.assertEqual(get_config("lovemail_notice5"), "0")
        set_config("announce_frequency", "0")
        self.assertIn("不发", c.get("/").get_data(as_text=True))

    def test_hidden_when_extension_off(self):
        from changri.core import extensions
        setup_data()
        c, tok = client()
        extensions.set_enabled("lovemail", False)
        html = c.get("/").get_data(as_text=True)
        self.assertNotIn("心动信邮差提醒", html)
        self.assertNotIn("每日指令提示", html)
        self.assertEqual(c.post("/quick/cfg:lovemail_notice5", data={"_csrf": tok("/"), "on": "1"}).status_code, 404)
        self.assertIn("礼物公开播报", html)                                            # 礼物是核心功能，一直在

    def test_windows_and_master(self):
        setup_data()
        set_config("window_sms", "10-23")
        groups = dict((t, rows) for t, rows, _ in quick.groups(lambda ep, **kw: ep + "?" + "&".join(f"{k}={v}" for k, v in kw.items())))
        win = {r["key"]: r for r in groups["开放时段（不在时段里玩家就用不了）"]}
        self.assertEqual(win["window_sms"]["value"], "10:00–23:00")
        self.assertEqual(win["window_date"]["value"], "全天")
        self.assertIn("open=window_sms", win["window_sms"]["page"])
        master = {r["label"]: r for r in groups["功能总开关"]}
        self.assertEqual(master["关系线"]["value"], "开着")
        self.assertIn("tab=aux", master["关系线"]["page"])


class Todos(unittest.TestCase):
    def test_gongzhi_all_in(self):
        a, b = setup_data()
        c, tok = client()
        with get_conn() as conn:
            for rid in (a, b):
                conn.execute("INSERT OR REPLACE INTO sheets(role_id,kind,text,updated_ts) VALUES(?,?,?,1)", (rid, "gongzhi", "公知"))
        html = c.get("/").get_data(as_text=True)
        self.assertIn("公知都交齐了（2/2）", html)                                      # NPC 不算
        self.assertIn("现在展示", html)
        c.post("/quick/gongzhi", data={"_csrf": tok("/"), "on": "1", "from": "todo"})
        self.assertNotIn("公知都交齐了", c.get("/").get_data(as_text=True))

    def test_lovemail_and_god(self):
        setup_data()
        c, tok = client()
        set_config("lovemail_enabled", "0")
        god_core.set_global("gift", False)
        html = c.get("/").get_data(as_text=True)
        self.assertIn("心动信信箱是关的", html)
        self.assertIn("打开信箱", html)
        self.assertIn("全体关掉了：礼物", html)
        c.post("/quick/lovemail", data={"_csrf": tok("/"), "on": "1", "from": "todo"})
        self.assertNotIn("心动信信箱是关的", c.get("/").get_data(as_text=True))

    def test_no_season_no_lovemail_todo(self):
        setup_data(running=False)
        c, _ = client()
        set_config("lovemail_enabled", "0")
        self.assertNotIn("心动信信箱是关的", c.get("/").get_data(as_text=True))           # 季度结束了，心动信本来就该关


if __name__ == "__main__":
    unittest.main()
