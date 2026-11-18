"""和 QQ 服务器的临时断线（WebSocket Closed、Failed to get gateway info……）：偶尔一条只记成提醒，30 分钟内反复出现才合成一条出错。"""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "fl.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db  # noqa: E402


class Msg:
    def __init__(self, text):
        self.record = {"level": type("L", (), {"no": 40})(), "exception": None, "name": "nonebot", "function": "log", "line": 412, "message": text}


WS = "QQ | <r><bg #f8bbd0>WebSocket Closed</bg #f8bbd0></r>"


class Flaps(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()
        errlog._flaps.clear()
        errlog._flap_reported[0] = 0.0
        errlog._notice_seen.clear()

    def test_occasional_is_dropped(self):
        for _ in range(errlog.FLAP_LIMIT - 1):
            errlog._loguru_sink(Msg(WS))
        errlog._loguru_sink(Msg("QQ | Failed to get gateway info."))      # 别的断线算在一起，但还没到次数……
        errs = [r for r in errlog.read_all() if r["kind"] == "error"]
        self.assertEqual(len(errs), 1)                                  # ……第 5 次合成一条出错
        row = errs[0]
        self.assertEqual((row["kind"], row["source"]), ("error", "QQ 连接"))
        self.assertIn("5 次", row["message"])
        self.assertNotIn("<r>", row["message"])

    def test_few_spread_out_only_notices(self):
        t = 1_000_000.0
        for i in range(10):                                             # 每 20 分钟一次：30 分钟内最多 2 次
            self.assertFalse(errlog.note_flap(WS, now=t + i * 1200))
        kinds = [r["kind"] for r in errlog.read_all()]
        self.assertNotIn("error", kinds)
        self.assertEqual(kinds, ["notice"] * 5)                         # 提醒 30 分钟一条：200 分钟里记 5 条，没有一条算出错

    def test_quiet_after_report(self):
        t = 2_000_000.0
        got = [errlog.note_flap(WS, now=t + i) for i in range(12)]
        self.assertEqual(got.count(True), 1)                            # 30 分钟内只记一条
        got = [errlog.note_flap(WS, now=t + errlog.FLAP_WINDOW + 10 + i) for i in range(5)]
        self.assertEqual(got.count(True), 1)                            # 过了 30 分钟还在频繁断：再记一条

    def test_other_errors_still_recorded(self):
        errlog._loguru_sink(Msg("Running Matcher(...) failed."))
        self.assertEqual([r["kind"] for r in errlog.read_all()], ["error"])


if __name__ == "__main__":
    unittest.main()
