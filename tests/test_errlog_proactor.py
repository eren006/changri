"""Windows Proactor 关连接时的 WinError 10054（_call_connection_lost）：不进报错日志，窗口里降成调试信息；别的异常照记。"""
import logging, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "pr.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db  # noqa: E402

MSG = "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)\nhandle: <Handle _ProactorBasePipeTransport._call_connection_lost(None)>"


def _exc(e):
    try:
        raise e
    except BaseException as x:  # noqa: BLE001
        return type(x), x, x.__traceback__


class Msg:
    def __init__(self, text, exc_info=None):
        exc = type("E", (), {"type": exc_info[0], "value": exc_info[1], "traceback": exc_info[2]})() if exc_info else None
        self.record = {"level": type("L", (), {"no": 40})(), "exception": exc, "name": "asyncio", "function": "default_exception_handler",
                       "line": 1833, "message": text, "extra": {}}


class Proactor(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()

    def test_connreset_dropped(self):
        errlog._loguru_sink(Msg(MSG, _exc(ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。"))))
        errlog._loguru_sink(Msg(MSG, _exc(ConnectionAbortedError(10053, "aborted"))))
        self.assertEqual(errlog.read_all(), [])

    def test_other_errors_kept(self):
        errlog._loguru_sink(Msg(MSG, _exc(ValueError("真出错了"))))                         # 同一个位置但不是断连
        errlog._loguru_sink(Msg("Exception in callback foo()", _exc(ConnectionResetError())))   # 断连但不是关连接那一步
        self.assertEqual(len(errlog.read_all()), 2)

    def test_bridge_downgrades(self):
        seen = []
        from loguru import logger
        hid = logger.add(lambda m: seen.append(m.record["level"].name), level=0)
        try:
            rec = logging.LogRecord("asyncio", logging.ERROR, __file__, 1, MSG, None, _exc(ConnectionResetError(10054, "x")))
            errlog._StdBridge().emit(rec)
        finally:
            logger.remove(hid)
        self.assertEqual(seen, ["DEBUG"])


if __name__ == "__main__":
    unittest.main()
