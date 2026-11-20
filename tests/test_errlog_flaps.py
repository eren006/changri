"""短暂的毛病（和 QQ 服务器断线重连、网络超时、对方断开）：本机不记、不显示、不上报（作者 2026-11-20）；别的报错照常记。"""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "fl.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db  # noqa: E402


class Msg:
    def __init__(self, text, exc=None):
        rx = None
        if exc is not None:
            try:
                raise exc
            except BaseException as x:  # noqa: BLE001
                rx = type("E", (), {"type": type(x), "value": x, "traceback": x.__traceback__})()
        self.record = {"level": type("L", (), {"no": 40})(), "exception": rx, "name": "nonebot", "function": "log", "line": 412, "message": text}


class NetworkError(Exception):
    pass


WS = "QQ | <r><bg #f8bbd0>WebSocket Closed</bg #f8bbd0></r>"


class Flaps(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()
        errlog._notice_seen.clear()

    def test_flaps_not_recorded(self):
        for text in (WS, "QQ | Failed to get gateway info.", "QQ | Error while setup websocket to wss://api.sgroup.qq.com/websocket. Trying to reconnect..."):
            for _ in range(30):                                         # 再多也不记，不会合成一条「出错」
                errlog._loguru_sink(Msg(text))
        self.assertEqual(errlog.read_all(), [])

    def test_network_glitch_not_recorded(self):
        for last in ("httpx.ReadTimeout: timed out", "httpx.ConnectError: [Errno 104] Connection reset by peer",
                     "aiohttp.client_exceptions.ServerDisconnectedError: Server disconnected", "ConnectionResetError: [Errno 54]"):
            self.assertTrue(errlog.is_transient("发消息失败", "Traceback (most recent call last):\n  File \"x.py\", line 1\n" + last))
        self.assertFalse(errlog.is_transient("x", "Traceback (most recent call last):\nKeyError: 'a'"))
        self.assertFalse(errlog.is_transient("x", "ValueError: ReadTimeout should be positive"))   # 只认真正抛出来的异常类

    def test_old_flap_rows_hidden_and_not_uploaded(self):
        errlog.record("error", "QQ 连接", "30 分钟内和 QQ 服务器的连接断开 / 重连了 23 次")       # 旧版本记下的
        errlog.record("notice", "QQ 连接", "和 QQ 服务器的连接断了一下")
        errlog.record("error", "x.py:1", "真的错")
        self.assertEqual([r["source"] for r in errlog.read_all()], ["x.py:1"])

    def test_wrong_secret_is_config_not_glitch(self):
        """#569b42（2026-11-20）：AppSecret 不对，每 3 秒「Error while process data from websocket」，堆栈最后是 invalid appid or secret。
        这不是断线：记成一条「配置」，写清怎么改；10 分钟内只记一条。"""
        trace = ("Traceback (most recent call last):\n  File \"nonebot/adapters/qq/bot.py\", line 310, in get_access_token\n"
                 "nonebot.adapters.qq.exception.NetworkError: <NetWorkError message=Get authorization failed with invalid response "
                 "{'code': 100016, 'message': 'invalid appid or secret'}. Please check your config.>")
        self.assertFalse(errlog.is_transient("QQ | Error while process data from websocket", trace))
        for _ in range(50):
            errlog._loguru_sink(Msg("QQ | Error while process data from websocket wss://api.sgroup.qq.com/websocket. Trying to reconnect...",
                                    exc=NetworkError("<NetWorkError message=Get authorization failed with invalid response "
                                                     "{'code': 100016, 'message': 'invalid appid or secret'}. Please check your config.>")))
        rows = errlog.read_all()
        self.assertEqual([(r["kind"], r["source"]) for r in rows], [("config", errlog.AUTH_SOURCE)])
        self.assertIn("AppSecret", rows[0]["message"])
        self.assertIsNotNone(errlog.auth_issue())

    def test_old_qq_rows_with_wrong_secret_kept(self):
        errlog.record("error", "QQ 连接", "30 分钟内和 QQ 服务器的连接断开 / 重连了 540 次", "...invalid appid or secret...")
        self.assertEqual(len(errlog.read_all()), 1)

    def test_other_errors_still_recorded(self):
        errlog._loguru_sink(Msg("Running Matcher(...) failed."))
        self.assertEqual([r["kind"] for r in errlog.read_all()], ["error"])

    def test_whitelist_still_config(self):
        errlog._loguru_sink(Msg("QQ | ActionFailed code=11298 接口访问源IP不在白名单"))
        self.assertEqual([r["kind"] for r in errlog.read_all()], ["config"])


if __name__ == "__main__":
    unittest.main()
