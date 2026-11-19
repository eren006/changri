"""Windows 上 asyncio 的连接噪音（对方突然断开：WinError 64 / 10054 / 10053）：不记进报错日志，玩家后台看不到，也不上报。"""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "wn.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db  # noqa: E402

ASYNCIO = "asyncio:default_exception_handler:1833"


class Msg:
    """模拟一条从 Python logging 转过来的 ERROR：来源在 extra 里，堆栈来自 exc。"""
    def __init__(self, text, src=ASYNCIO, exc=None):
        e = None
        if exc is not None:
            e = type("E", (), {"type": type(exc), "value": exc, "traceback": None})()
        self.record = {"level": type("L", (), {"no": 40})(), "exception": e, "name": "asyncio", "function": "x", "line": 1,
                       "message": text, "extra": {"std_src": src, "std_path": "C:\\runtime\\Lib\\asyncio\\base_events.py"}}


class WinNoise(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()

    def kinds(self):
        return [r["kind"] for r in errlog.read_all()]

    def test_connection_lost_10054_dropped(self):
        errlog._loguru_sink(Msg("Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
                                exc=ConnectionResetError("[WinError 10054] 远程主机强迫关闭了一个现有的连接。")))
        self.assertEqual(self.kinds(), [])

    def test_accept_64_dropped(self):
        errlog._loguru_sink(Msg("Task exception was never retrieved future: <Task finished name='Task-22285' "
                                "coro=<IocpProactor.accept.<locals>.accept_coro() done> "
                                "exception=OSError(22, '指定的网络名不再可用。', None, 64, None)>"))
        errlog._loguru_sink(Msg("Accept failed on a socket", exc=OSError("[WinError 64] 指定的网络名不再可用。")))
        self.assertEqual(self.kinds(), [])

    def test_own_code_connection_errors_still_recorded(self):
        errlog._loguru_sink(Msg("发消息失败", src="changri.core.qqsend:send:10",
                                exc=ConnectionResetError("[WinError 10054] 远程主机强迫关闭了一个现有的连接。")))
        self.assertEqual(self.kinds(), ["error"])

    def test_other_asyncio_errors_still_recorded(self):
        errlog._loguru_sink(Msg("Task exception was never retrieved", exc=ValueError("别的错")))
        self.assertEqual(self.kinds(), ["error"])


if __name__ == "__main__":
    unittest.main()
