"""Windows 在窗口里跑、没设开机自启：主账号打开后台时弹窗提醒，点「现在设好」去装（platform/winstart.need_popup / install_from_admin）。
更新时不自动装（作者 2026-11-24 定），只在这里提醒。"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "a.db")
os.environ["QQ_BOTS"] = "[]"
import changri.admin as admin_mod  # noqa: E402

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402
from changri.platform import layout, service, winstart  # noqa: E402


def token(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)


class WinBase(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(winstart, "OFF_MARK", d / "autostart_off"),
            mock.patch.object(winstart.sys, "platform", "win32"),
            mock.patch.object(layout, "INSTALLED", True),
            mock.patch.dict(os.environ, {"CHANGRI_SERVICE": ""}),
            mock.patch.object(winstart, "_admin_port", return_value=5091),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        winstart.forget_need()
        self.addCleanup(winstart.forget_need)


class NeedPopup(WinBase):
    def test_missing_task_needs_popup_and_is_cached(self):
        with mock.patch.object(winstart, "_task_exists", return_value=False) as te, \
                mock.patch.object(winstart, "_is_admin", return_value=True):
            self.assertEqual(winstart.need_popup(), {"admin": True, "port": 5091})
            winstart.need_popup()
        te.assert_called_once()                               # 5 分钟内不再查 schtasks

    def test_no_popup_when_installed_removed_service_or_not_windows(self):
        with mock.patch.object(winstart, "_task_exists", return_value=True):
            self.assertIsNone(winstart.need_popup())
        winstart.forget_need()
        with mock.patch.object(winstart, "_task_exists", return_value=False), mock.patch.object(winstart, "_is_admin", return_value=False):
            with mock.patch.dict(os.environ, {"CHANGRI_SERVICE": "1"}):
                self.assertIsNone(winstart.need_popup())
            with mock.patch.object(winstart.sys, "platform", "linux"):
                self.assertIsNone(winstart.need_popup())
            winstart.OFF_MARK.write_text("x")
            self.assertIsNone(winstart.need_popup())


class InstallFromAdmin(WinBase):
    def test_admin_installs_directly(self):
        with mock.patch.object(winstart, "_is_admin", return_value=True), \
                mock.patch.object(service, "install", return_value="ok") as inst, \
                mock.patch.object(winstart, "_elevate_install") as el:
            self.assertIn("已设为开机自动运行", winstart.install_from_admin(local=False))
        inst.assert_called_once()
        el.assert_not_called()

    def test_not_admin_on_server_asks_uac_in_background(self):
        with mock.patch.object(winstart, "_is_admin", return_value=False), \
                mock.patch.object(winstart, "_elevate_install", return_value=0) as el, \
                mock.patch.object(winstart.threading, "Thread") as th:
            msg = winstart.install_from_admin(local=True)
            th.call_args.kwargs["target"]()
        self.assertIn("点「是」", msg)
        el.assert_called_once()

    def test_not_admin_remote_tells_what_to_do(self):
        with mock.patch.object(winstart, "_is_admin", return_value=False), mock.patch.object(winstart, "_elevate_install") as el:
            self.assertIn("Install-Autostart.bat", winstart.install_from_admin(local=False))
        el.assert_not_called()                               # 弹在服务器上没人看得见，不弹


class Page(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        admin_mod._fails.clear()
        self.c = create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(self.c)})

    def test_popup_shown_only_when_needed(self):
        with mock.patch.object(winstart, "need_popup", return_value={"admin": True, "port": 5091}):
            html = self.c.get("/accounts").get_data(as_text=True)
        self.assertIn('id="as-dlg"', html)
        self.assertIn("现在设好", html)
        with mock.patch.object(winstart, "need_popup", return_value={"admin": False, "port": 5091}):
            html = self.c.get("/accounts", environ_base={"REMOTE_ADDR": "203.0.113.5"}).get_data(as_text=True)
        self.assertIn("Install-Autostart.bat", html)
        self.assertNotIn("autostart/install", html)          # 远程打开、不是管理员：按钮装不了，只说怎么做
        with mock.patch.object(winstart, "need_popup", return_value=None):
            self.assertNotIn('id="as-dlg"', self.c.get("/accounts").get_data(as_text=True))

    def test_install_button(self):
        tok = token(self.c, "/accounts")
        with mock.patch.object(winstart, "install_from_admin", return_value="已设为开机自动运行：测试") as inst:
            html = self.c.post("/autostart/install", data={"_csrf": tok, "next": "/accounts"}, follow_redirects=True).get_data(as_text=True)
            r = self.c.post("/autostart/install", data={"_csrf": tok, "next": "//evil.example"})
        self.assertIn("已设为开机自动运行：测试", html)
        self.assertTrue(inst.call_args_list[0].args[0])        # 测试客户端是 127.0.0.1
        self.assertNotIn("evil", r.headers["Location"])

    def test_non_owner_cannot_install(self):
        webusers.create_user("helper", "longenough2", "admin")
        c = create_app().test_client()
        c.post("/login", data={"username": "helper", "password": "longenough2", "_csrf": token(c)})
        with mock.patch.object(winstart, "need_popup", return_value={"admin": True, "port": 5091}):
            self.assertNotIn('id="as-dlg"', c.get("/").get_data(as_text=True))
        with mock.patch.object(winstart, "install_from_admin") as inst:
            c.post("/autostart/install", data={"_csrf": token(c, "/")})
        inst.assert_not_called()


if __name__ == "__main__":
    unittest.main()
