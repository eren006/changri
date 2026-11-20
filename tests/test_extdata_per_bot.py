"""扩展数据每个机器人一份：两个机器人的 3 号角色不再共用一条记录；升级时把原来共用的那份复制给已有的机器人，之后新加的机器人从空的开始。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, extdata  # noqa: E402


def put(ext, key, value):
    with extdata.lock:
        d = extdata.load(ext)
        d[key] = value
        extdata.save(ext, d)


def get(ext, key):
    return extdata.load(ext).get(key)


class PerBot(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited), extdata.DATA_DIR)
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.d / "changri.db", self.d / "bots", False
        extdata.DATA_DIR = self.d
        db._inited.clear()
        db.forget_bots()
        db.init_db()

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        extdata.DATA_DIR = self.saved[4]
        db.forget_bots()

    def test_two_bots_do_not_share(self):
        botconf.add_account("111111", "a" * 20, "甲团")
        botconf.add_account("222222", "b" * 20, "乙团")
        with db.use_bot("111111"):
            put("daily_signin", "streak_3", 5)
        with db.use_bot("222222"):
            self.assertIsNone(get("daily_signin", "streak_3"))                   # 乙团的 3 号看不到甲团 3 号的连签
            put("daily_signin", "streak_3", 1)
        with db.use_bot("111111"):
            self.assertEqual(get("daily_signin", "streak_3"), 5)
        self.assertTrue((self.d / "bots" / "111111" / "ext_data" / "daily_signin.json").is_file())
        self.assertFalse((self.d / "ext_data").exists())

    def test_upgrade_copies_shared_once(self):
        shared = self.d / "ext_data"
        shared.mkdir()
        (shared / "daily_signin.json").write_text(json.dumps({"streak_3": 7}), encoding="utf-8")
        botconf.add_account("111111", "a" * 20, "甲团")
        botconf.add_account("222222", "b" * 20, "乙团")
        with db.use_bot("222222"):
            self.assertEqual(get("daily_signin", "streak_3"), 7)                  # 升级前的记录都还在
            put("daily_signin", "streak_3", 8)
        with db.use_bot("111111"):
            self.assertEqual(get("daily_signin", "streak_3"), 7)                  # 之后各写各的
        self.assertFalse(shared.exists())
        self.assertTrue((self.d / extdata.LEGACY_BACKUP / "daily_signin.json").is_file())   # 共用的那份改名留底
        botconf.add_account("333333", "c" * 20, "丙团")                            # 后来加的机器人：从空的开始
        db.forget_bots()
        with db.use_bot("333333"):
            self.assertIsNone(get("daily_signin", "streak_3"))

    def test_existing_bot_file_not_overwritten(self):
        botconf.add_account("111111", "a" * 20, "甲团")
        own = self.d / "bots" / "111111" / "ext_data"
        own.mkdir(parents=True)
        (own / "x_ext.json").write_text(json.dumps({"k": "mine"}), encoding="utf-8")
        shared = self.d / "ext_data"
        shared.mkdir()
        (shared / "x_ext.json").write_text(json.dumps({"k": "shared"}), encoding="utf-8")
        with db.use_bot("111111"):
            self.assertEqual(get("x_ext", "k"), "mine")

    def test_no_bot_uses_shared_folder(self):
        put("x_ext", "k", 1)                                                       # 一个机器人都没有：照旧放 DATA_DIR/ext_data
        self.assertTrue((self.d / "ext_data" / "x_ext.json").is_file())


if __name__ == "__main__":
    unittest.main()
