"""报错日志收得全：长日bot 自己代码的 warning 记成「提醒」（30 分钟同样的一条）、Python 自带 logging 也收、超过一天的自动清、满了先挤提醒。"""
import logging, os, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "en.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db, set_config  # noqa: E402

OWN = os.path.join(errlog._PKG_DIR, "plugins", "season.py")
THIRD = "/usr/lib/python3/site-packages/httpx/_client.py"


class Msg:
    def __init__(self, text, no=30, path=OWN, extra=None):
        f = type("F", (), {"path": path})()
        self.record = {"level": type("L", (), {"no": no})(), "exception": None, "name": "changri.plugins.season",
                       "function": "tick", "line": 280, "message": text, "file": f, "extra": extra or {}}


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        set_config("error_report_url", "")
        errlog.clear()
        errlog._notice_seen.clear()


class Notices(Base):
    def test_own_warning_becomes_notice(self):
        errlog._loguru_sink(Msg("[季度] 定时任务出错：database is locked"))
        rows = errlog.read_all()
        self.assertEqual([r["kind"] for r in rows], ["notice"])
        self.assertIn("定时任务出错", rows[0]["message"])

    def test_third_party_warning_ignored_but_error_kept(self):
        errlog._loguru_sink(Msg("some library warning", path=THIRD))
        self.assertEqual(errlog.read_all(), [])
        errlog._loguru_sink(Msg("library blew up", no=40, path=THIRD))
        self.assertEqual([r["kind"] for r in errlog.read_all()], ["error"])

    def test_same_problem_once_per_half_hour_with_count(self):
        t = 1_000_000.0
        self.assertTrue(errlog.note_notice("changri.plugins.alarm:tick:107", "[闹钟] 定时任务出错：R12 不存在", now=t))
        for i in range(3):                                   # 数字不同也算同一个问题
            self.assertFalse(errlog.note_notice("changri.plugins.alarm:tick:107", f"[闹钟] 定时任务出错：R{i} 不存在", now=t + 60 * (i + 1)))
        self.assertTrue(errlog.note_notice("changri.plugins.alarm:tick:107", "[闹钟] 定时任务出错：R12 不存在", now=t + errlog.NOTICE_QUIET + 1))
        rows = errlog.read_all()
        self.assertEqual(len(rows), 2)
        self.assertIn("还出现了 3 次", rows[1]["message"])

    def test_expected_messages_skipped(self):
        self.assertFalse(errlog.note_notice("changri.admin:x:1", "表单校验码对不上：POST /x（浏览器没带 cookie）"))
        self.assertFalse(errlog.note_notice("changri.core.qqsend:x:1", "[按钮] 主动消息带按钮被 QQ 拒绝，一小时内改发纯文字：…"))
        self.assertEqual(errlog.read_all(), [])


class StdLogging(Base):
    def setUp(self):
        super().setUp()
        from loguru import logger
        self.sink = logger.add(errlog._loguru_sink, level="WARNING", catch=False)
        errlog._bridge_std_logging()

    def tearDown(self):
        from loguru import logger
        logger.remove(self.sink)

    def test_std_exception_recorded_with_original_place(self):
        try:
            1 / 0
        except ZeroDivisionError:
            logging.getLogger("changri.core.season").exception("季度档案没存下来")
        rows = errlog.read_all()
        self.assertEqual([r["kind"] for r in rows], ["error"])
        self.assertIn("季度档案没存下来", rows[0]["message"])
        self.assertTrue(rows[0]["source"].startswith("changri.core.season:"))
        self.assertIn("ZeroDivisionError", rows[0]["trace"])

    def test_std_warning_from_own_code_is_notice(self):
        logging.getLogger("qqmanager").warning("查群 %s 成员出错：%s", "G1", "timeout")     # 这个测试文件不在 changri 包里……
        self.assertEqual(errlog.read_all(), [])                                           # ……所以不算自己的代码
        rec = logging.LogRecord("qqmanager", logging.WARNING, os.path.join(errlog._PKG_DIR, "core", "qqmanager.py"), 278,
                                "查群 %s 成员出错：%s", ("G1", "timeout"), None, func="members")
        logging.getLogger("qqmanager").handle(rec)
        rows = errlog.read_all()
        self.assertEqual([r["kind"] for r in rows], ["notice"])
        self.assertEqual(rows[0]["source"], "qqmanager:members:278")

    def test_bridge_installed_once(self):
        errlog._bridge_std_logging()
        n = sum(isinstance(h, errlog._StdBridge) for h in logging.getLogger().handlers)
        self.assertEqual(n, 1)


class Cleanup(Base):
    def test_older_than_a_day_pruned(self):
        now = time.time()
        errlog.record("error", "a", "old", now=now - errlog.RETAIN - 10)
        errlog.record("notice", "b", "old notice", now=now - errlog.RETAIN - 10)
        errlog.record("error", "c", "new", now=now - 60)
        self.assertEqual(errlog.prune(now), 2)
        self.assertEqual([r["message"] for r in errlog.read_all()], ["new"])

    def test_unsent_kept_until_sent(self):
        set_config("error_report_url", "https://example.invalid/api/errors")
        now = time.time()
        errlog.record("error", "a", "old unsent", now=now - errlog.RETAIN - 10)
        self.assertEqual(errlog.prune(now), 0)                       # 还没发出去：留着
        rows = errlog.read_all()
        rows[0]["reported"] = 1
        errlog._write(rows)
        self.assertEqual(errlog.prune(now), 1)                       # 发出去了：清

    def test_full_log_drops_notices_first(self):
        errlog.record("error", "e", "keep me")
        for i in range(errlog.KEEP):
            errlog.record("notice", "n", f"notice {i}")
        rows = errlog.read_all()
        self.assertEqual(len(rows), errlog.KEEP)
        self.assertIn("keep me", [r["message"] for r in rows])


class Report(Base):
    def test_batches_fit_receiver_limits(self):
        todo = [{"ts": i, "kind": "error", "source": "s", "message": "错" * 600, "trace": "t" * 1500} for i in range(120)]
        batches = list(errlog._batches({"app": "changri_bot"}, todo))
        self.assertEqual(sum(len(b) for b in batches), 120)
        for b in batches:
            self.assertLessEqual(len(b), 50)
            body = len(__import__("json").dumps({"app": "changri_bot", "errors": b}, ensure_ascii=False).encode("utf-8"))
            self.assertLessEqual(body, 64 * 1024)

    def test_only_sent_batches_marked(self):
        from unittest import mock
        set_config("error_report_url", "https://example.invalid/api/errors")
        for i in range(60):
            errlog.record("error", "s", f"错 {i}")
        calls = []

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("down")
            return Resp()
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(errlog.report_pending(), 50)
        self.assertEqual(sum(1 for r in errlog.read_all() if r["reported"]), 50)   # 第二批没发出去，不能标成已发


if __name__ == "__main__":
    unittest.main()
