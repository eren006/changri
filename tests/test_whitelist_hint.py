"""IP 白名单没加对（QQ 返回 401 / 11298）：记成一条「配置」而不是一堆出错堆栈，后台总览顶上提醒。"""
import os, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "wl.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402

TRACE = ('  File "bot.py", line 697, in _handle_response\n    raise UnauthorizedException(response)\n'
         "nonebot.adapters.qq.exception.UnauthorizedException: <ActionFailed: 401, code=11298, message=接口访问源IP不在白名单, trace_id=abc>")


class Msg:
    def __init__(self, text, trace=""):
        exc = None
        if trace:
            try:
                raise RuntimeError(trace)
            except RuntimeError as e:
                exc = type("E", (), {"type": type(e), "value": e, "traceback": e.__traceback__})()
        self.record = {"level": type("L", (), {"no": 40})(), "exception": exc, "name": "nonebot", "function": "_run_matcher", "line": 463, "message": text}


class WhitelistHint(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()

    def test_recorded_once_as_config(self):
        self.assertTrue(errlog.is_whitelist_error(TRACE))
        self.assertFalse(errlog.is_whitelist_error("address already in use"))
        errlog._loguru_sink(Msg("Running Matcher(...) failed.", TRACE))
        errlog._loguru_sink(Msg("Running Matcher(...) failed.", TRACE))                # 接着又撞一次：不再记
        rows = errlog.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["source"], rows[0]["trace"]), ("config", "IP 白名单", ""))
        self.assertIn("IP 白名单", rows[0]["message"]); self.assertIn("检查连接", rows[0]["message"])
        errlog._loguru_sink(Msg("别的错", "ValueError: x"))                             # 别的错照常记成出错
        self.assertEqual([r["kind"] for r in errlog.read_all()], ["config", "error"])
        self.assertTrue(errlog.whitelist_issue())
        self.assertIsNone(errlog.whitelist_issue(now=time.time() + 3600))             # 改好了不再撞：半小时后提醒消失

    def test_dashboard_banner(self):
        import re

        from changri.admin import common, create_app
        from changri.core import webusers
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        webusers.create_user("boss", "longenough1", "owner")
        common._ip_cache.update(ts=time.time(), ip="1.2.3.4")                          # 不真的去问外面的服务
        c = create_app().test_client()
        tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok})
        self.assertNotIn("IP 白名单没加对", c.get("/").get_data(as_text=True))
        errlog.note_whitelist()
        html = c.get("/").get_data(as_text=True)
        self.assertIn("IP 白名单没加对，机器人现在回不了话", html)
        self.assertIn("1.2.3.4", html)
        self.assertIn("配置", html)


if __name__ == "__main__":
    unittest.main()
