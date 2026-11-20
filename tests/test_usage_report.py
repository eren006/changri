"""匿名使用情况上报（core/usage.py + plugins/botsync）：没报出去的指令次数存盘、重启接着算；
报成功清掉、失败并回去；机器人连上后比上次报的多才补报；每小时一次的任务注册上了。"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "usage.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")
import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter  # noqa: E402

from changri.core import usage  # noqa: E402
from changri.core.db import init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
botsync = nonebot.load_plugin("changri.plugins.botsync").module
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _restart():
    """模拟重启：内存里的数全没了，下次用到时重新读文件。"""
    usage._cmds, usage._by_name, usage._loaded, usage._dirty, usage._last_online = 0, {}, False, False, -1


class UsagePending(unittest.TestCase):
    def setUp(self):
        init_db()
        set_config("usage_report", "1")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old, usage.DATA_DIR = usage.DATA_DIR, Path(self.tmp.name)
        self.addCleanup(setattr, usage, "DATA_DIR", old)
        _restart()
        self.addCleanup(_restart)
        self.pending = Path(self.tmp.name) / "usage_pending.json"

    def test_counts_survive_restart(self):
        usage.count_command("ra")
        usage.count_command("ra")
        usage.count_command("")
        usage.save_pending()
        self.assertEqual(json.loads(self.pending.read_text(encoding="utf-8")), {"cmds": 3, "by_cmd": {"ra": 2}})
        _restart()
        usage.count_command("短信")
        self.assertEqual(usage._take_count(), (4, {"ra": 2, "短信": 1}))

    def test_save_skips_when_unchanged(self):
        usage.save_pending()
        self.assertFalse(self.pending.exists())

    def test_success_clears_file_and_remembers_online(self):
        usage.count_command("ra")
        usage.save_pending()
        with mock.patch.object(usage.urllib.request, "urlopen", return_value=_Resp()) as up:
            self.assertTrue(usage.report(1, 1))
        body = json.loads(up.call_args[0][0].data)
        self.assertEqual((body["bots"], body["online"], body["cmds"], body["by_cmd"]), (1, 1, 1, {"ra": 1}))
        self.assertFalse(self.pending.exists())
        self.assertEqual(usage.last_online(), 1)

    def test_failure_keeps_counts_on_disk(self):
        usage.count_command("ra")
        with mock.patch.object(usage.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertFalse(usage.report(1, 1))
        self.assertEqual(json.loads(self.pending.read_text(encoding="utf-8"))["cmds"], 1)
        self.assertEqual(usage.last_online(), -1)

    def test_disabled_drops_counts(self):
        set_config("usage_report", "0")
        usage.count_command("ra")
        usage.save_pending()
        with mock.patch.object(usage.urllib.request, "urlopen") as up:
            self.assertFalse(usage.report(1, 1))
        up.assert_not_called()
        self.assertFalse(self.pending.exists())


class UsageJobs(unittest.TestCase):
    def test_jobs_registered(self):
        jobs = {j.id: j for j in scheduler.get_jobs()}
        self.assertIn("usage_report", jobs)
        self.assertIn("usage_save", jobs)
        self.assertEqual(jobs["usage_report"].trigger.interval.total_seconds(), 3600)

    def test_on_connect_only_reports_when_more_online(self):
        with mock.patch.object(usage, "report") as rp, mock.patch.object(nonebot, "get_bots", return_value={"a": 1}):
            with mock.patch.object(usage, "last_online", return_value=1):
                asyncio.run(botsync._usage_report(only_if_more_online=True))
            rp.assert_not_called()
            with mock.patch.object(usage, "last_online", return_value=0):
                asyncio.run(botsync._usage_report(only_if_more_online=True))
            rp.assert_called_once()
            self.assertEqual(rp.call_args[0][1], 1)

    def test_connect_schedules_one_shot_report(self):
        asyncio.run(botsync._usage_on_connect())
        job = scheduler.get_job("usage_on_connect")
        self.assertIsNotNone(job)
        self.assertEqual(job.kwargs, {"only_if_more_online": True})
        scheduler.remove_job("usage_on_connect")


if __name__ == "__main__":
    unittest.main()
