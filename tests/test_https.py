"""一键 HTTPS：Caddy 配置的生成与合并、开通/证书到位/关闭时网址怎么变、经本机反代时认出真实 IP、小手机 http→https 跳转、后台页面。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "x.db"))

import changri.core.db as db  # noqa: E402
from changri.admin.remote import LocalProxyFix, is_blocked_path  # noqa: E402
from changri.core import botconf, httpsproxy as hp, webusers  # noqa: E402
from changri.core.db import get_config, set_config, use_bot  # noqa: E402

IP = "203.0.113.7"
FOREIGN = {"apps": {"http": {"servers": {"mysite": {"listen": [":443"]}, "changri_old": {"listen": [":9999"]}}},
                    "tls": {"automation": {"policies": [{"subjects": ["example.com"]}, {"subjects": ["198.51.100.1"]}]}}},
           "admin": {"listen": "localhost:2019"}}


class Config(unittest.TestCase):
    def test_check_domain(self):
        self.assertEqual(hp.check_domain(""), "")
        self.assertEqual(hp.check_domain("Bot.Example.com"), "")
        for bad in ("https://a.com", "a.com:443", "a.com/x", "localhost", "1.2.3.4", "a_b.com"):
            self.assertNotEqual(hp.check_domain(bad), "", bad)

    def test_build_ip_uses_shortlived_and_default_sni(self):
        items = [{"name": "changri_admin", "listen": 5443, "upstream": 5091}, {"name": "changri_phone_1", "listen": 6092, "upstream": 5092}]
        servers, policy = hp.build(IP, items, is_domain=False)
        self.assertEqual(servers["changri_admin"]["listen"], [":5443"])
        self.assertEqual(servers["changri_phone_1"]["routes"][0]["handle"][0]["upstreams"], [{"dial": "127.0.0.1:5092"}])
        self.assertEqual(servers["changri_admin"]["tls_connection_policies"], [{"default_sni": IP}])
        self.assertEqual(policy["subjects"], [IP])
        self.assertEqual(policy["issuers"][0]["profile"], "shortlived")
        self.assertNotIn("challenges", policy["issuers"][0])                    # 80、443 两种验证都开着
        _, dpol = hp.build("bot.example.com", items, is_domain=True)
        self.assertNotIn("profile", dpol["issuers"][0])

    def test_alt_http_port_set_and_undone(self):
        servers, policy = hp.build(IP, [{"name": "changri_admin", "listen": 5443, "upstream": 5091}], False)
        on = hp.merge({}, servers, policy, set(), alt_http=True)
        self.assertEqual(on["apps"]["http"]["http_port"], hp.ALT_HTTP_PORT)
        off = hp.merge(on, servers, policy, set(), alt_http=False)
        self.assertNotIn("http_port", off["apps"]["http"])
        mine = {"apps": {"http": {"http_port": 8080, "servers": {"x": {}}}}}               # 别人自己设的 http_port 不动
        self.assertEqual(hp.merge(mine, servers, policy, set())["apps"]["http"]["http_port"], 8080)
        self.assertEqual(hp.merge(on, {}, None, {IP}), {})                                 # 关闭时一起撤销

    def test_blocked(self):
        self.assertFalse(hp.blocked("none", "none"))
        self.assertFalse(hp.blocked("nginx", "none"))           # 80 被占，443 空着：走 443
        self.assertFalse(hp.blocked("caddy", "other"))          # 80 是 Caddy 自己：它自己回应
        self.assertTrue(hp.blocked("nginx", "other"))
        self.assertTrue(hp.blocked("other", "other"))
        self.assertIn(f"127.0.0.1:{hp.ALT_HTTP_PORT}", hp.nginx_snippet())

    def test_merge_keeps_other_sites(self):
        servers, policy = hp.build(IP, [{"name": "changri_admin", "listen": 5443, "upstream": 5091}], False)
        out = hp.merge(FOREIGN, servers, policy, {"198.51.100.1"})
        self.assertEqual(set(out["apps"]["http"]["servers"]), {"mysite", "changri_admin"})        # 旧的 changri_ 站点换掉，别人的留着
        subs = [p["subjects"] for p in out["apps"]["tls"]["automation"]["policies"]]
        self.assertEqual(subs, [[IP], ["example.com"]])                                        # 旧 IP 的规则删掉，我们的放最前
        self.assertEqual(out["admin"], {"listen": "localhost:2019"})
        self.assertIn("changri_old", FOREIGN["apps"]["http"]["servers"])                       # 不改动传进来的原配置

    def test_merge_disable_cleans_up(self):
        servers, policy = hp.build(IP, [{"name": "changri_admin", "listen": 5443, "upstream": 5091}], False)
        mine = hp.merge({}, servers, policy, set())
        self.assertEqual(hp.merge(mine, {}, None, {IP}), {})
        left = hp.merge(hp.merge(FOREIGN, servers, policy, set()), {}, None, {IP})
        self.assertEqual(set(left["apps"]["http"]["servers"]), {"mysite"})
        self.assertEqual(len(left["apps"]["tls"]["automation"]["policies"]), 2)


class ProxyFix(unittest.TestCase):
    def run_env(self, **env):
        seen = {}

        def app(environ, start_response):
            seen.update(ip=environ.get("REMOTE_ADDR"), scheme=environ.get("wsgi.url_scheme"))
            return [b""]
        LocalProxyFix(app)({"wsgi.url_scheme": "http", **env}, lambda *a: None)
        return seen

    def test_trusts_only_local(self):
        self.assertEqual(self.run_env(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.1.1.1, 9.9.9.9", HTTP_X_FORWARDED_PROTO="https"),
                         {"ip": "9.9.9.9", "scheme": "https"})
        self.assertEqual(self.run_env(REMOTE_ADDR="8.8.8.8", HTTP_X_FORWARDED_FOR="127.0.0.1", HTTP_X_FORWARDED_PROTO="https"),
                         {"ip": "8.8.8.8", "scheme": "http"})               # 外面直接连来的，自带的头不信
        self.assertEqual(self.run_env(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="junk")["ip"], "127.0.0.1")
        self.assertEqual(self.run_env(REMOTE_ADDR="127.0.0.1")["ip"], "127.0.0.1")

    def test_https_page_blocked_from_public(self):
        self.assertTrue(is_blocked_path("/https"))
        self.assertTrue(is_blocked_path("/b/123/https/enable"))


class Flow(unittest.TestCase):
    """多机器人：开通 → 证书到位才换网址 → 端口变了跟着同步 → 关闭改回 http。"""

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()
        botconf.add_account("111111", "a" * 20, "甲")
        botconf.add_account("222222", "b" * 20, "乙")
        for bid, port in (("111111", "5092"), ("222222", "5093")):
            with use_bot(bid):
                set_config("phone_enabled", "1")
                set_config("phone_port", port)
                set_config("phone_base_url", f"http://{IP}:{port}")
                set_config("phone_https", "0")
        self.caddy = {}

        def fake_api(method, path, body=None, timeout=10):
            if method == "GET":
                return self.caddy
            self.caddy = body
            return None
        self.patches = [mock.patch.object(hp, "_api", side_effect=fake_api),
                        mock.patch("changri.core.netcheck.public_ip_cached", return_value=IP)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def base(self, bid):
        with use_bot(bid):
            return get_config("phone_base_url"), get_config("phone_https")

    def test_full_cycle(self):
        from changri.core import phoneserver
        self.assertEqual(hp.enable(""), IP)
        # 所有机器人共用一个小手机端口：只有一个小手机站点（老配置里两个机器人各 5092/5093，统一成第一个的 5092）
        self.assertEqual(set(self.caddy["apps"]["http"]["servers"]), {"changri_admin", "changri_phone"})
        self.assertEqual(self.caddy["apps"]["http"]["servers"]["changri_phone"]["listen"], [":6092"])
        self.assertEqual(self.base("111111"), (f"http://{IP}:5092", "0"))         # 证书还没好，先不换网址
        self.assertEqual(self.base("222222"), (f"http://{IP}:5092", "0"))
        with mock.patch.object(hp, "cert_ok", return_value=False):
            self.assertFalse(hp.activate_if_ready())
        with mock.patch.object(hp, "cert_ok", return_value=True):
            self.assertTrue(hp.activate_if_ready())
        self.assertEqual(self.base("111111"), (f"https://{IP}:6092", "1"))
        self.assertEqual(self.base("222222"), (f"https://{IP}:6092", "1"))
        self.assertEqual(get_config("https_active"), "1")                         # 全局设置，不在某个机器人库里

        phoneserver.set_port(5100)
        self.assertEqual(hp.sync(), "")
        self.assertEqual(self.caddy["apps"]["http"]["servers"]["changri_phone"]["listen"], [":6100"])
        self.assertEqual(self.base("222222"), (f"https://{IP}:6100", "1"))

        hp.disable()
        self.assertEqual(self.caddy, {})
        self.assertEqual(self.base("111111"), (f"http://{IP}:5100", "0"))
        self.assertEqual(self.base("222222"), (f"http://{IP}:5100", "0"))
        self.assertEqual(hp.sync(), "")                                           # 关着时 sync 什么都不做
        self.assertEqual(self.caddy, {})

    def test_domain_switch_forgets_old_cert(self):
        hp.enable("")
        hp.enable("bot.example.com")
        subs = [p["subjects"] for p in self.caddy["apps"]["tls"]["automation"]["policies"]]
        self.assertEqual(subs, [["bot.example.com"]])
        self.assertNotIn("profile", self.caddy["apps"]["tls"]["automation"]["policies"][0]["issuers"][0])
        with self.assertRaises(hp.CaddyError):
            hp.enable("http://x.com")

    def test_admin_page(self):
        from changri.admin import create_app
        app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        c = app.test_client()
        import re
        tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok})
        with mock.patch.object(hp, "caddy_version", return_value=(2, 10, 2)), mock.patch.object(hp, "api_ok", return_value=True), \
                mock.patch.object(hp, "is_linux", return_value=True):
            html = c.get("/https").get_data(as_text=True)
            self.assertIn("开通 HTTPS", html)
            self.assertIn("6092", html)                                # 小手机共用一个 https 端口
            tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
            r = c.post("/https/enable", data={"domain": "", "_csrf": tok})
            self.assertEqual(r.status_code, 302)
            self.assertEqual(get_config("https_enabled"), "1")
            with mock.patch.object(hp, "cert_ok", return_value=True):
                self.assertEqual(c.get("/https/status").get_json()["ready"], True)
                self.assertIn(f"https://{IP}:6092/p", c.get("/https").get_data(as_text=True))
        self.assertEqual(self.base("111111")[1], "1")

    def _page(self, p80, p443):
        from changri.admin import create_app
        import re
        app = create_app()
        if not webusers.count():
            webusers.create_user("boss", "longenough1", "owner")
        c = app.test_client()
        tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok})
        with mock.patch.object(hp, "caddy_version", return_value=(2, 10, 2)), mock.patch.object(hp, "api_ok", return_value=True), \
                mock.patch.object(hp, "is_linux", return_value=True), mock.patch.object(hp, "port80_state", return_value=p80), \
                mock.patch.object(hp, "port443_state", return_value=p443):
            return c.get("/https").get_data(as_text=True), c.get("/https/status").get_json()

    def test_port_hints(self):
        html, _ = self._page("none", "none")
        self.assertNotIn("占着", html)
        html, _ = self._page("nginx", "none")
        self.assertIn("不要紧", html)
        self.assertIn("让 nginx 转发验证请求", html)
        hp.enable("")
        html, st = self._page("nginx", "other")
        self.assertIn("80 和 443 都被nginx占着", html)
        self.assertIn(f"proxy_pass http://127.0.0.1:{hp.ALT_HTTP_PORT};", html)
        self.assertEqual(st, {"ready": False, "blocked": True})
        html, _ = self._page("other", "other")
        self.assertIn(f"127.0.0.1:{hp.ALT_HTTP_PORT}", html)
        self.assertNotIn("让 nginx 转发验证请求", html)

    def test_windows_page(self):
        from changri.admin import create_app
        import re
        app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        c = app.test_client()
        tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok})
        with mock.patch.object(hp, "is_linux", return_value=False), mock.patch.object(hp, "is_windows", return_value=True), \
                mock.patch.object(hp, "caddy_version", return_value=None), mock.patch.object(hp, "api_ok", return_value=False), \
                mock.patch.object(hp, "port80_state", return_value="nginx"), mock.patch.object(hp, "port443_state", return_value="other"):
            self.assertTrue(hp.install_command().endswith("Install-HTTPS.bat"))
            html = c.get("/https").get_data(as_text=True)
            self.assertIn("以管理员身份运行", html)
            self.assertIn("Install-HTTPS.bat", html)
            self.assertNotIn("sudo", html)                                       # 命令和 nginx 重载都是 Windows 写法
            self.assertIn("nginx -s reload", html)
            self.assertNotIn("还不支持", html)
        with mock.patch.object(hp, "is_linux", return_value=False), mock.patch.object(hp, "is_windows", return_value=False):
            self.assertIn("还不支持", c.get("/https").get_data(as_text=True))

    def test_push_uses_alt_port_when_80_taken(self):
        with mock.patch.object(hp, "port80_state", return_value="nginx"):
            hp.enable("")
        self.assertEqual(self.caddy["apps"]["http"]["http_port"], hp.ALT_HTTP_PORT)
        with mock.patch.object(hp, "port80_state", return_value="none"):
            hp.sync()
        self.assertNotIn("http_port", self.caddy["apps"]["http"])


class PhoneRedirect(unittest.TestCase):
    def setUp(self):
        db.init_db()
        set_config("phone_enabled", "1")
        set_config("phone_https", "1")
        set_config("phone_base_url", f"https://{IP}:6092")
        from changri.phone.app import create_phone_app
        self.c = create_phone_app("k" * 32).test_client()

    def tearDown(self):
        set_config("phone_https", "0")
        set_config("phone_base_url", "")

    def test_redirect_to_https_port(self):
        r = self.c.get("/p/inbox?x=1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], f"https://{IP}:6092/p/inbox?x=1")
        self.assertEqual(self.c.get("/p/health").status_code, 200)               # 外网探测要能直接打到
        self.assertNotEqual(self.c.get("/p", base_url="https://localhost").status_code, 302)


if __name__ == "__main__":
    unittest.main()
