"""overview.group_line：多人群聊（微信）不分 QQ 群，提示要指去小手机，不能说「群池没空群」。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "gl.db")

from changri.core import overview, rooms  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


class GroupLine(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM rooms")

    def _room(self, kind: str) -> object:
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, note, dkey, created_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                            (kind, 1, None if kind == "微信" else 1, "20:00", "21:00", "", "", "k", int(time.time()))).lastrowid
        return rooms.get(rid)

    def test_wechat_points_to_phone_even_without_chatroom(self):
        self.assertFalse(rooms.web_mode())                       # 聊天室关着（只开多人群聊时就是这样）
        line = overview.group_line(self._room("微信"))
        self.assertIn("小手机", line)
        self.assertNotIn("群池", line)

    def test_date_without_group_still_says_pool_empty(self):
        self.assertIn("群池", overview.group_line(self._room("约会")))


if __name__ == "__main__":
    unittest.main()
