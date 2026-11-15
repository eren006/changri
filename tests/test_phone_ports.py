"""小手机：所有机器人共用一个端口，按登录 / 链接 / 激活码分到各自的工作区。"""
import http.cookiejar
import os
import re
import socket
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, phonecodes, phoneserver, roles  # noqa: E402

A, B = "111111", "222222"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PhonePorts(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()
        botconf.add_account(A, "a" * 20)
        botconf.add_account(B, "b" * 20)
        db.set_config("ext:phone", "1")
        phoneserver._bots_cache = (0.0, [])

    def tearDown(self):
        phoneserver._stop_server()
        phoneserver._apps.clear()
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def enable(self, bid, **cfg):
        with db.use_bot(bid):
            db.set_config("phone_enabled", "1")
            for k, v in cfg.items():
                db.set_config(k, v)

    def test_legacy_per_bot_ports_become_one(self):
        self.enable(A, phone_port="5094", phone_base_url="http://1.2.3.4:5094")
        self.enable(B, phone_port="5095", phone_base_url="http://1.2.3.4:5095")
        self.assertEqual(phoneserver.port(), 5094)                  # 第一个开着小手机的机器人的端口
        with db.use_bot(B):
            self.assertEqual(db.get_config("phone_base_url"), "http://1.2.3.4:5094")     # 另一个机器人的网址跟着改
        phoneserver.set_port(6100)
        with db.use_bot(A):
            self.assertEqual(db.get_config("phone_base_url"), "http://1.2.3.4:6100")

    def test_check_port(self):
        self.assertIn("1024", phoneserver.check_port("80"))
        self.assertIn("设置平台", phoneserver.check_port(str(phoneserver._admin_port())))
        with socket.socket() as s:
            s.bind(("0.0.0.0", 0))
            busy = s.getsockname()[1]
            self.assertIn("占用", phoneserver.check_port(str(busy)))
        self.assertEqual(phoneserver.check_port(str(free_port())), "")

    def test_pick_bot(self):
        bots = [A, B]
        self.assertEqual(phoneserver.pick_bot({"QUERY_STRING": f"b={B}"}, bots), B)
        self.assertEqual(phoneserver.pick_bot({"HTTP_COOKIE": f"phone_bot={B}"}, bots), B)
        self.assertEqual(phoneserver.pick_bot({"HTTP_COOKIE": f"phone_auth_{B}=x"}, bots), B)
        self.assertEqual(phoneserver.pick_bot({"HTTP_COOKIE": "phone_bot=999999"}, bots), A)    # 不认识的机器人：退回第一个
        self.assertIsNone(phoneserver.pick_bot({}, []))

    def test_one_port_serves_both_bots_and_code_finds_the_bot(self):
        self.enable(A)
        self.enable(B)
        with db.use_bot(A):
            roles.create_role("甲", A, "PA", "MA")
            ra = roles.find_role_for_sender(A, "PA", "MA")
        with db.use_bot(B):
            roles.create_role("乙", B, "PB", "MB")
            code_b = phonecodes.code_for_role(roles.find_role_for_sender(B, "PB", "MB")["id"])
        self.assertTrue(ra)
        port = free_port()
        phoneserver.set_port(port)
        self.assertIn(str(port), phoneserver.start())
        base = f"http://127.0.0.1:{port}"
        self.assertEqual(urllib.request.urlopen(base + "/p/health", timeout=5).status, 200)

        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        html = op.open(base + "/p", timeout=5).read().decode()
        tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        body = urllib.parse.urlencode({"code": code_b, "_csrf": tok}).encode()
        page = op.open(base + "/p", data=body, timeout=5).read().decode()
        cookies = {c.name: c.value for c in jar}
        self.assertEqual(cookies.get("phone_bot"), B)                # 激活码是 B 的：进 B
        self.assertIn(f"phone_auth_{B}", cookies)
        self.assertIn("<title>信息", page)                          # 登录后进了收件箱（要在 B 的工作区里校验通过才进得来）
        self.assertNotIn("激活码不对", page)

        with db.use_bot(A):
            db.set_config("phone_enabled", "0")
        self.assertIn("运行", phoneserver.start())                   # B 还开着：继续监听
        with db.use_bot(B):
            db.set_config("phone_enabled", "0")
        self.assertEqual(phoneserver.start(), "小手机已关闭")
        with self.assertRaises(Exception):
            urllib.request.urlopen(base + "/p/health", timeout=2)


if __name__ == "__main__":
    unittest.main()
