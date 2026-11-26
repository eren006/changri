"""后台「彻底删除」机器人：Windows 上库文件还被连接占着时 rmtree 会抛 PermissionError（WinError 32），
以前直接 500、数据留着、账号却已经删了。现在先 gc + 重试；还删不掉就做记号，下次启动（还没打开库时）再删。
Mac / Linux 不会占着删不掉，这里用假的 rmtree 模拟 Windows。"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "a.db")
os.environ["QQ_BOTS"] = "[]"

from changri.core import botconf, db  # noqa: E402

_real_rmtree = shutil.rmtree


def _locked(*a, **kw):
    raise PermissionError(32, "The process cannot access the file because it is being used by another process")


class EraseTests(unittest.TestCase):
    def setUp(self):
        # 多机器人模式（指定了 CHANGRI_DB_PATH 默认是单库，不分工作区），照 test_admin_multibot 的写法
        d = Path(tempfile.mkdtemp())
        saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        db.init_db()

        def restore():
            db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = saved[:3]
            db._inited.clear()
            db._inited.update(saved[3])
            db.forget_bots()
        self.addCleanup(restore)
        self.sleep = mock.patch("time.sleep")      # 重试之间不真的等
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def _make(self, app_id):
        botconf.add_account(app_id, "s" * 20, "测试号")
        with db.use_bot(app_id):
            db.set_config("x", "1")
        self.assertTrue(db.workspace_path(app_id).exists())

    def test_deletes_normally(self):
        self._make("300001")
        botconf.delete_account("300001")
        self.assertEqual(db.erase_workspace("300001"), "gone")
        self.assertFalse(db.workspace_path("300001").parent.exists())

    def test_retries_when_briefly_locked(self):
        self._make("300002")
        botconf.delete_account("300002")
        calls = []

        def flaky(path, *a, **kw):
            calls.append(path)
            if len(calls) < 3:
                _locked()
            return _real_rmtree(path, *a, **kw)

        with mock.patch.object(db.shutil, "rmtree", flaky):
            self.assertEqual(db.erase_workspace("300002"), "gone")
        self.assertEqual(len(calls), 3)
        self.assertFalse(db.workspace_path("300002").parent.exists())

    def test_still_locked_marks_and_next_start_deletes(self):
        self._make("300003")
        botconf.delete_account("300003")
        with mock.patch.object(db.shutil, "rmtree", _locked):
            self.assertEqual(db.erase_workspace("300003"), "later")      # 不抛异常（以前是 500）
        folder = db.workspace_path("300003").parent
        self.assertTrue((folder / db.ERASE_MARK).exists())
        self.assertEqual(db.purge_erased(), ["300003"])                # 下次启动
        self.assertFalse(folder.exists())

    def test_readded_before_restart_keeps_data(self):
        self._make("300004")
        botconf.delete_account("300004")
        with mock.patch.object(db.shutil, "rmtree", _locked):
            self.assertEqual(db.erase_workspace("300004"), "later")
        botconf.add_account("300004", "s" * 20, "又接回来了")
        self.assertEqual(db.purge_erased(), [])
        folder = db.workspace_path("300004").parent
        self.assertTrue(db.workspace_path("300004").exists())
        self.assertFalse((folder / db.ERASE_MARK).exists())

    def test_bad_appid_or_no_data(self):
        self.assertEqual(db.erase_workspace("../etc"), "")
        self.assertEqual(db.erase_workspace("399999"), "")


if __name__ == "__main__":
    unittest.main()
