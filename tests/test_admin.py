import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "a.db")
os.environ["QQ_BOTS"] = "[]"          # 不读开发机 .env 里的真实机器人（单独跑这个文件时会被导入测试库）
import changri.admin as admin_mod  # noqa: E402   导入时会读本机 .env，所以环境变量要在导入之后再清

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import lovemail, roles, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


def token(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)


class AdminTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("seasons", "entrance", "lovemails", "rel_details", "relations", "messages", "bind_codes", "role_groups", "roles", "admins", "web_users", "config"):
                c.execute(f"DELETE FROM {t}")
        admin_mod._fails.clear()
        self.app = create_app()
        self.c = self.app.test_client()

    def login(self, name="boss", pw="longenough1"):
        if webusers.count() == 0:
            webusers.create_user(name, pw, "owner")     # 第一个账号就是主账号
        return self.c.post("/login", data={"username": name, "password": pw, "_csrf": token(self.c)})

    def post(self, path, **data):
        return self.c.post(path, data={**data, "_csrf": token(self.c, "/accounts")}, follow_redirects=True)

    def test_theme_switch(self):
        """后台外观：睡莲 / 芭蕾 / 圣诞 / 默认，记在 cookie 里，回到刚才的页面；圣诞会飘雪。"""
        self.login()
        html = self.c.get("/season").get_data(as_text=True)
        self.assertIn('aria-label="睡莲"', html)
        self.assertIn('<html lang="zh-CN">', html)                              # 默认：<html> 上没有主题
        tok = token(self.c, "/season")
        r = self.c.post("/theme", data={"theme": "xmas", "_csrf": tok}, headers={"Referer": "http://localhost/season?x=1"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/season?x=1"))
        html = self.c.get("/season").get_data(as_text=True)
        self.assertIn('data-theme="xmas"', html)
        self.assertIn('id="snow"', html)
        self.assertIn('aria-label="圣诞" aria-pressed="true"', html)
        r = self.c.post("/theme", data={"theme": "lily", "_csrf": tok}, headers={"Referer": "http://evil.example/x"})
        self.assertEqual(r.headers["Location"].rsplit("localhost", 1)[-1], "/")       # 别的网站来的：回首页，不跳出去
        html = self.c.get("/season").get_data(as_text=True)
        self.assertIn('data-theme="lily"', html)
        self.assertNotIn('id="snow"', html)
        self.c.post("/theme", data={"theme": "<script>", "_csrf": tok})                # 不认识的值 = 恢复默认
        self.assertIn('<html lang="zh-CN">', self.c.get("/season").get_data(as_text=True))
        self.c.set_cookie("admin_theme", "ballet")
        self.assertIn('data-theme="ballet"', self.c.get("/login").get_data(as_text=True))   # 登录页也跟着变

    def test_phone_admin_page(self):
        from unittest import mock
        from changri.core import phonecodes
        from changri.core.db import get_config
        roles.create_role("甲", "B1", "P0", "M0")
        self.login()
        self.assertEqual(self.c.get("/phone").status_code, 404)           # 小手机是可选的「额外」，默认没开
        from changri.core import extensions
        extensions.set_enabled("phone", True)
        html = self.c.get("/phone").get_data(as_text=True)
        self.assertIn("开启小手机", html)                                # 没开过：先走初始化
        self.assertNotIn("网络体检", html)
        r = self.post("/phone/enable", phone_port="80")
        self.assertIn("1024", r.get_data(as_text=True))
        import socket
        with socket.socket() as k:
            k.bind(("127.0.0.1", 0))
            free = k.getsockname()[1]
        with mock.patch("changri.core.netcheck.public_ip", return_value="9.9.9.9"):
            r = self.post("/phone/enable", phone_port=str(free))
        from changri.core import phoneserver
        phoneserver.stop()
        self.assertIn("放行", r.get_data(as_text=True))
        self.assertEqual(get_config("phone_enabled"), "1")
        self.assertEqual(get_config("phone_base_url"), f"http://9.9.9.9:{free}")
        html = self.c.get("/phone").get_data(as_text=True)
        self.assertIn("网络体检", html)
        self.assertIn("机器人连上了吗", html)
        r = self.post("/phone/settings", phone_port="80", phone_base_url="x")
        self.assertIn("1024", r.get_data(as_text=True))
        r = self.post("/phone/settings", phone_port="5092", phone_base_url="http://1.2.3.4:5092/", phone_enabled="1", visual="classic", copy="classic")
        self.assertEqual(get_config("phone_base_url"), "http://1.2.3.4:5092")
        self.assertEqual(get_config("phone_https"), "0")
        self.post("/phone/codes/generate")
        rid = roles.get_role_by_name("甲")["id"]
        old = phonecodes.code_for_role(rid)
        self.post(f"/phone/codes/{rid}/reset")
        self.assertNotEqual(phonecodes.code_for_role(rid), old)
        with mock.patch("changri.core.netcheck._http", return_value=(200, '{"state": "timeout"}')):
            r = self.post("/phone/check")
        self.assertIn("安全组", r.get_data(as_text=True))

    def test_setup_needs_code_when_remote(self):
        from changri.core import setupcode
        setupcode.clear()
        t = token(self.c, "/setup")
        data = {"username": "站长01", "password": "longenough1", "again": "longenough1", "_csrf": t}
        env = {"REMOTE_ADDR": "8.8.8.8"}
        r = self.c.post("/setup", data=data, environ_overrides=env, follow_redirects=True)
        self.assertIn("验证码不对", r.get_data(as_text=True))
        self.assertEqual(webusers.count(), 0)                                   # 没验证码，建不出账号
        self.assertIn("验证码", self.c.get("/setup", environ_overrides=env).get_data(as_text=True))
        r = self.c.post("/setup", data={**data, "setup_code": setupcode.code()}, environ_overrides=env, follow_redirects=True)
        self.assertEqual(webusers.count(), 1)
        self.assertFalse(setupcode._path().exists())                            # 建好后验证码作废

    def test_login_progressive_lock(self):
        webusers.create_user("boss", "longenough1", "owner")
        t = token(self.c)
        for i in range(5):
            self.c.post("/login", data={"username": f"u{i}", "password": "bad", "_csrf": t})
        # 换用户名乱猜也一样被这个 IP 的总账锁住，连正确密码都进不去
        r = self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": t})
        self.assertEqual(r.status_code, 429)
        self.assertIn("输错太多次", r.get_data(as_text=True))
        self.assertEqual(admin_mod._fails["127.0.0.1|*"][0], 5)

    def test_login_lock_survives_restart(self):
        from changri.core.lockstore import FailStore
        webusers.create_user("boss", "longenough1", "owner")
        t = token(self.c)
        for i in range(5):
            self.c.post("/login", data={"username": f"u{i}", "password": "bad", "_csrf": t})
        fresh = FailStore("admin")                                   # 模拟进程重启：一个全新的对象，只能从数据库里读
        n, until = fresh["127.0.0.1|*"]
        self.assertEqual(n, 5)
        self.assertGreater(until, time.time())
        self.assertIsNone(FailStore("phone").get("127.0.0.1|*"))      # 后台和小手机各记各的

    def test_first_run_setup(self):
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertTrue(self.c.get("/").headers["Location"].endswith("/setup"))
        t = token(self.c, "/setup")
        r = self.c.post("/setup", data={"username": "a", "password": "longenough1", "again": "longenough1", "_csrf": t})
        self.assertIn("2 到 20", self.c.post("/setup", data={"username": "a", "password": "longenough1", "again": "longenough1", "_csrf": t}, follow_redirects=True).get_data(as_text=True))
        self.assertIn("不一样", self.c.post("/setup", data={"username": "站长", "password": "longenough1", "again": "zzz", "_csrf": t}, follow_redirects=True).get_data(as_text=True))
        r = self.c.post("/setup", data={"username": "站长", "password": "longenough1", "again": "longenough1", "_csrf": t})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)                       # 建完直接登录
        self.assertTrue(self.c.get("/setup").headers["Location"].endswith("/login"))   # 之后 setup 不能再用

    def test_login_csrf_and_lockout(self):
        webusers.create_user("boss", "longenough1", "owner")
        r = self.c.post("/login", data={"username": "boss", "password": "longenough1"})          # 没带 CSRF：不登录，回登录页说清楚（以前是 400）
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/login"))
        self.assertEqual(self.c.get("/roles").status_code, 302)                   # 没登进去
        token(self.c)                                                             # 有了 cookie 以后，校验码过期的提示
        html = self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": "过期的"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("页面过期了", html)
        self.assertIsNotNone(self.c.get_cookie("changri_admin"))                  # cookie 用专属名字，不和同一台服务器上的别的网站抢「session」
        self.assertIn("不对", self.c.post("/login", data={"username": "boss", "password": "bad", "_csrf": token(self.c)}, follow_redirects=True).get_data(as_text=True))
        self.assertEqual(self.login().status_code, 302)
        self.c.post("/logout", data={"_csrf": token(self.c, "/accounts")})
        self.assertEqual(self.c.get("/roles").status_code, 302)                   # 退出后进不去
        for _ in range(5):
            self.c.post("/login", data={"username": "boss", "password": "bad", "_csrf": token(self.c)})
        r = self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(self.c)})
        self.assertEqual(r.status_code, 429)                                      # 连错 5 次后，连正确密码也先锁住
        admin_mod._fails.clear()
        self.assertEqual(self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(self.c)}).status_code, 302)

    def test_all_pages_render(self):
        roles.create_role("甲", "B1", "G1", "M1"); roles.create_role("乙", "B1", "G2", "M2")
        a, b = (roles.get_role_by_name(n)["id"] for n in "甲乙")
        lovemail.add_mail(a, b, "匿名", "你好")
        self.login()
        for p in ["/", "/extensions", "/pool", "/forum", "/rpg", "/lovemail", "/relations", "/admins", "/bots", "/accounts"]:
            self.assertEqual(self.c.get(p, follow_redirects=True).status_code, 200, p)
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn("今日通告", html); self.assertIn("22:00", html); self.assertIn("信池里 1 封", html)

    def test_owner_and_generated_admins(self):
        self.login()
        me = webusers.list_users()[0]
        self.assertEqual(me["role"], "owner")
        # 主账号不能删、不能停用、不能被重置
        self.assertIn("主账号不能删除", self.post(f"/accounts/{me['id']}/delete").get_data(as_text=True))
        self.assertIn("主账号不能停用", self.post(f"/accounts/{me['id']}/disable", off="1").get_data(as_text=True))
        self.assertIn("不能重置", self.post(f"/accounts/{me['id']}/reset").get_data(as_text=True))
        self.assertEqual(webusers.create_user("另一个主", "longenough9", "owner")[0], False)      # 主账号只能有一个
        # 生成管理员：密码只显示一次
        html = self.post("/accounts/generate", note="活动搭档").get_data(as_text=True)
        cred = re.search(r"账号 <b[^>]*>(admin\d{4})</b>.*?密码 <b[^>]*>([\w-]+)</b>", html, re.S)
        self.assertIsNotNone(cred)
        self.assertNotIn(cred.group(2), self.c.get("/accounts").get_data(as_text=True))
        self.assertEqual(webusers.verify(cred.group(1), cred.group(2))["role"], "admin")
        aid = webusers.verify(cred.group(1), cred.group(2))["id"]
        # 管理员登录：能用日常页面，不能管账号，也看不到账号列表
        c2 = self.app.test_client()
        self.assertEqual(c2.post("/login", data={"username": cred.group(1), "password": cred.group(2), "_csrf": token(c2)}).status_code, 302)
        self.assertEqual(c2.get("/").status_code, 200)
        page = c2.get("/accounts").get_data(as_text=True)
        self.assertIn("你是管理员账号", page); self.assertNotIn("生成管理员账号", page)
        t = token(c2, "/accounts")
        self.assertEqual(c2.post("/accounts/generate", data={"_csrf": t}).status_code, 403)
        self.assertEqual(c2.post(f"/accounts/{me['id']}/delete", data={"_csrf": t}).status_code, 403)
        # 停用：马上无法继续使用（已登录的会话也失效），恢复后又能用
        self.assertIn("已停用", self.post(f"/accounts/{aid}/disable", off="1").get_data(as_text=True))
        self.assertEqual(c2.get("/roles").status_code, 302)
        c3 = self.app.test_client()
        r = c3.post("/login", data={"username": cred.group(1), "password": cred.group(2), "_csrf": token(c3)})
        self.assertEqual(r.status_code, 403); self.assertIn("已被停用", r.get_data(as_text=True))
        self.post(f"/accounts/{aid}/disable", off="0")
        self.assertEqual(c3.post("/login", data={"username": cred.group(1), "password": cred.group(2), "_csrf": token(c3)}).status_code, 302)
        # 重置密码：旧密码立刻失效
        self.post(f"/accounts/{aid}/reset")
        self.assertIsNone(webusers.verify(cred.group(1), cred.group(2)))
        # 删除
        self.assertIn("已删除", self.post(f"/accounts/{aid}/delete").get_data(as_text=True))
        self.assertEqual(webusers.count(), 1)

    def test_change_own_password(self):
        self.login()
        self.assertIn("原密码不对", self.post("/accounts/password", old="x", new="newpassword1", again="newpassword1").get_data(as_text=True))
        self.assertIn("至少 8 位", self.post("/accounts/password", old="longenough1", new="short", again="short").get_data(as_text=True))
        self.assertIn("不一样", self.post("/accounts/password", old="longenough1", new="newpassword1", again="other").get_data(as_text=True))
        self.assertIn("已修改", self.post("/accounts/password", old="longenough1", new="newpassword1", again="newpassword1").get_data(as_text=True))
        self.assertIsNotNone(webusers.verify("boss", "newpassword1"))

    def test_deleted_user_loses_access_and_legacy_env(self):
        self.login()
        webusers.create_user("second", "longenough2")      # 普通管理员
        c2 = self.app.test_client()
        c2.post("/login", data={"username": "second", "password": "longenough2", "_csrf": token(c2)})
        self.assertEqual(c2.get("/").status_code, 200)
        sid = [u for u in webusers.list_users() if u["username"] == "second"][0]["id"]
        webusers.delete_user(sid, 0)
        self.assertEqual(c2.get("/roles").status_code, 302)                       # 账号被删，已登录的会话也立刻失效
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        os.environ["ADMIN_PASSWORD"] = "legacy-password-1"
        try:
            create_app()
            self.assertEqual(webusers.verify("admin", "legacy-password-1")["role"], "owner")   # 旧部署的 ADMIN_PASSWORD 自动变成主账号 admin
        finally:
            os.environ.pop("ADMIN_PASSWORD")


    def test_season_page_flow(self):
        self.login()
        page = self.c.get("/season").get_data(as_text=True)
        self.assertIn("现在没有进行中的季度", page)
        today = time.strftime("%Y-%m-%d")
        r = self.c.post("/season/book", data={"name": "甲季", "start": today, "end": today, "_csrf": token(self.c, "/season")}, follow_redirects=True)
        self.assertIn("已预订", r.get_data(as_text=True))
        r = self.c.post("/season/start", data={"name": "甲季", "_csrf": token(self.c, "/season")}, follow_redirects=True)
        self.assertIn("D0", r.get_data(as_text=True))
        r = self.c.post("/season/day", data={"day": "D4", "_csrf": token(self.c, "/season")}, follow_redirects=True)
        self.assertIn("现在是 D4", r.get_data(as_text=True))
        r = self.c.post("/season/end", data={"_csrf": token(self.c, "/season")}, follow_redirects=True)
        self.assertIn("已结束", r.get_data(as_text=True))
        r = self.c.post("/season/wipe", data={"confirm": "不对", "_csrf": token(self.c, "/season")}, follow_redirects=True)
        self.assertIn("没有清空", r.get_data(as_text=True))
        r = self.c.post("/season/wipe", data={"confirm": "收尾", "_csrf": token(self.c, "/season")}, follow_redirects=True)
        self.assertIn("已收尾", r.get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
