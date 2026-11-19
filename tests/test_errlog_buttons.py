"""带按钮的消息被 QQ 拒绝：记一条「配置」进后台报错日志（带 QQ 返回的原文），10 分钟内不重复记；IP 白名单的问题不归这里。"""
import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "eb.db")
from changri.core import errlog  # noqa: E402
from changri.core.db import init_db  # noqa: E402

ERR = "<ActionFailed: 400, code=304003, message=keyboard not allowed>"


class ButtonRejected(unittest.TestCase):
    def setUp(self):
        init_db()
        errlog.clear()

    def test_recorded_with_qq_reason(self):
        self.assertTrue(errlog.note_button_rejected("主动推送通知时", ERR))
        rows = errlog.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["source"]), ("config", errlog.BUTTON_SOURCE))
        self.assertIn("主动推送通知时", rows[0]["message"])
        self.assertIn("keyboard not allowed", rows[0]["message"])

    def test_quiet_for_ten_minutes(self):
        t = 1_000_000.0
        self.assertTrue(errlog.note_button_rejected("回复指令时", ERR, now=t))
        rows = errlog.read_all()
        rows[-1]["ts"] = int(t)
        errlog._path().write_text("".join(__import__("json").dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        self.assertFalse(errlog.note_button_rejected("回复指令时", ERR, now=t + 60))
        self.assertTrue(errlog.note_button_rejected("回复指令时", ERR, now=t + errlog.BUTTON_QUIET + 1))
        self.assertEqual(len(errlog.read_all()), 2)

    def test_whitelist_not_counted(self):
        self.assertFalse(errlog.note_button_rejected("回复指令时", "code=11298 接口访问源IP不在白名单"))
        self.assertEqual(errlog.read_all(), [])


NO_PUSH = "<ActionFailed: 400, code=40034, message=主动消息失败, 无权限, trace_id=abc>"


class NoPushPermission(unittest.TestCase):
    """群里没打开「机器人主动在群聊内发言」：记成「配置」，说清去哪打开，不说是按钮 / Markdown 的问题。"""
    def setUp(self):
        init_db()
        errlog.clear()

    def test_button_reject_routed_to_no_push(self):
        self.assertTrue(errlog.note_button_rejected("主动推送通知时", NO_PUSH))
        rows = errlog.read_all()
        self.assertEqual((rows[0]["kind"], rows[0]["source"]), ("config", errlog.NO_PUSH_SOURCE))
        self.assertIn("机器人主动在群聊内发言", rows[0]["message"])
        self.assertNotIn("Markdown 权限", rows[0]["message"])

    def test_warning_becomes_config_once(self):
        from nonebot import logger
        sink = logger.add(errlog._loguru_sink, level="WARNING")
        try:
            logger.warning(f"点歌 #1 没发到公告群：{NO_PUSH}")
            logger.warning(f"[@] 没发出去（群里没打开机器人主动在群聊内发言）：{NO_PUSH}")
        finally:
            logger.remove(sink)
        rows = errlog.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["source"]), ("config", errlog.NO_PUSH_SOURCE))
        self.assertIn("点歌 #1 没发到公告群", rows[0]["message"])
        self.assertNotIn("trace_id", rows[0]["message"])

    def test_other_errors_untouched(self):
        self.assertFalse(errlog.is_no_push_error(ERR))
        self.assertTrue(errlog.is_no_push_error(NO_PUSH))


if __name__ == "__main__":
    unittest.main()
