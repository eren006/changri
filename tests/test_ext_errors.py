import os
import sys
import tempfile
import unittest
from pathlib import Path

# 不设 CHANGRI_DB_PATH：要多机器人的工作区模式（扩展报错存在全局库，当前机器人是另一个库）
os.environ.pop("CHANGRI_DB_PATH", None)
os.environ["CHANGRI_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from changri.core import db, extensions  # noqa: E402


class ExtErrorTests(unittest.TestCase):
    """扩展上次的加载报错，下次启动时要能清掉（改好了的扩展不能一直挂着红字）。"""

    def test_list_keys_reads_global_db(self):
        db.init_db()
        with db.get_global_conn() as c:
            c.execute("INSERT INTO bot_accounts(app_id, secret, enabled, created_ts) VALUES('1234567', ?, 1, 0)", ("x" * 16,))
        db.forget_bots()
        db.init_db()
        self.assertEqual(db.current_bot(), "1234567")
        db.set_config("ext_error:demo", "上次坏了")
        self.assertEqual(extensions.list_keys("ext_error:"), ["ext_error:demo"])
        for k in extensions.list_keys("ext_error:"):          # load_all 里清旧报错就是这样做的
            db.set_config(k, "")
        self.assertEqual(extensions.load_error("demo"), "")


if __name__ == "__main__":
    unittest.main()
