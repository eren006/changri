"""plugins/botsync 里跟更新有关的定时任务：真把插件加载起来，确认任务都注册上了、跑一遍不出错。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "botsync.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")
import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter  # noqa: E402

from changri.core import autoupdate, updater  # noqa: E402
from changri.core.db import init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
botsync = nonebot.load_plugin("changri.plugins.botsync").module
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


class BotsyncUpdateJobs(unittest.TestCase):
    def setUp(self):
        init_db()
        set_config("update_latest", "")

    def test_jobs_registered(self):
        ids = {j.id for j in scheduler.get_jobs()}
        self.assertTrue({"update_post_check", "auto_update", "update_check"} <= ids)
        self.assertNotIn("update_confirm", ids)

    def test_one_shot_jobs_never_skipped(self):
        # 只跑一次的任务不能因为到点时程序正忙就被跳过（APScheduler 默认晚 1 秒就跳过）
        jobs = {j.id: j for j in scheduler.get_jobs()}
        for jid in ("update_post_check", "ext_boot_confirm"):
            self.assertIsNone(jobs[jid].misfire_grace_time, jid)

    def test_snapshot_in_real_process(self):
        snap = autoupdate.snapshot()
        self.assertEqual(snap["bots_online"], 0)
        self.assertIn("update_check", snap["jobs"])
        self.assertNotIn("auto_update", snap["jobs"])          # 检查用的任务自己不算

    def test_auto_update_job_idle_without_new_version(self):
        with mock.patch.object(autoupdate, "maybe_update") as mu:
            asyncio.run(botsync._auto_update())
        mu.assert_not_called()

    def test_auto_update_job_restarts_after_install(self):
        set_config("update_latest", "0.9.0")
        with mock.patch.object(updater, "read_pending", return_value=None), \
                mock.patch.object(autoupdate, "maybe_update", return_value="0.9.0"), mock.patch.object(updater, "restart") as rs:
            asyncio.run(botsync._auto_update())
        rs.assert_called_once()

    def test_auto_update_job_survives_install_error(self):
        set_config("update_latest", "0.9.0")
        with mock.patch.object(updater, "read_pending", return_value=None), \
                mock.patch.object(autoupdate, "maybe_update", side_effect=updater.UpdateError("连不上")), \
                mock.patch.object(updater, "restart") as rs:
            asyncio.run(botsync._auto_update())
        rs.assert_not_called()

    def test_auto_update_waits_while_update_pending(self):
        set_config("update_latest", "0.9.0")
        with mock.patch.object(updater, "read_pending", return_value={"to": "0.9.0"}), mock.patch.object(autoupdate, "maybe_update") as mu:
            asyncio.run(botsync._auto_update())
        mu.assert_not_called()

    def test_post_check_job_without_pending(self):
        with mock.patch.object(updater, "read_pending", return_value=None), mock.patch.object(updater, "request_rollback") as rb:
            asyncio.run(botsync._post_update_check())
        rb.assert_not_called()

    def test_post_check_job_swallows_its_own_errors(self):
        with mock.patch.object(autoupdate, "post_check", side_effect=RuntimeError("boom")):
            asyncio.run(botsync._post_update_check())             # 不抛出：检查本身坏了不能把机器人带崩


if __name__ == "__main__":
    unittest.main()
