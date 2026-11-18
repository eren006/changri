"""「我的待回」：轮到你回的场次置顶（超过最长弧长的标 🔴 排最前，写清上一句是谁说的、等了多久）；
已经结束还没退的 QQ 小号群（问群成员）；关系线写等了多久。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = tempfile.mkdtemp()
os.environ["CHANGRI_DB_PATH"] = str(Path(_tmp) / "p.db")

from changri.core import overview, pool, qqmanager, roles, rooms, season  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class TodoTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("room_lines", "room_members", "rooms", "pool_groups", "person_qq", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for p in (mock.patch.object(season, "phase", lambda: "playing"), mock.patch.object(season, "current_day", lambda: 1),
                  mock.patch.object(pool, "allocate", lambda rid: True)):
            p.start()
            self.addCleanup(p.stop)
        for n in "甲乙丙":
            roles.create_role(n, "B1", "G" + n, "M" + n)
        self.a, self.b, self.c = (roles.get_role_by_name(n) for n in "甲乙丙")

    def _say(self, room_id, role, ts):
        with get_conn() as c:
            c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp) VALUES(?,?,?,?,?,1)", (room_id, ts, role["id"], role["name"], "……"))
            c.execute("UPDATE room_members SET entered_ts=? WHERE room_id=? AND role_id=?", (ts, room_id, role["id"]))

    def test_my_turn_on_top_overdue_first(self):
        set_config("arc_max_hours", "12")
        now = time.time()
        _, _, r1 = rooms.create("约会", self.a["id"], [self.b["id"]], "1000-1100", "花园")
        _, _, r2 = rooms.create("约会", self.a["id"], [self.c["id"]], "1200-1300", "湖边")
        _, _, r3 = rooms.create("电话", self.b["id"], [self.a["id"]], "1400-1500")
        with get_conn() as c:
            g = c.execute("INSERT INTO pool_groups(bot_id, group_openid, real_gid, in_pool, state, added_ts) VALUES('B1','qq:1','1',1,'occupied',0)").lastrowid
            c.execute("UPDATE rooms SET pool_id=?", (g,))
        self._say(r1, self.a, now - 3600); self._say(r1, self.b, now - 1800)          # r1：乙刚回，轮到甲，等了 30 分钟
        self._say(r2, self.a, now - 20 * 3600); self._say(r2, self.c, now - 13 * 3600)  # r2：丙回了 13 小时，超过 12 小时
        self._say(r3, self.b, now - 7200); self._say(r3, self.a, now - 600)           # r3：甲说完了，等乙
        items = overview.todo_items(self.a, now=now)
        self.assertTrue(items[0].startswith("🔴 已超时") and f"R{r2}" in items[0] and "丙 说完到现在 13小时" in items[0], items)
        self.assertTrue(items[1].startswith("⏳") and f"R{r1}" in items[1] and "轮到你回" in items[1] and "30分钟" in items[1], items)
        self.assertIn(f"R{r3}", items[2]); self.assertIn("等 乙 回", items[2])

    def test_lingering_only_qq_groups(self):
        with get_conn() as c:
            g_qq = c.execute("INSERT INTO pool_groups(bot_id, group_openid, real_gid, in_pool, state, added_ts) VALUES('B1','qq:123456','123456',1,'free',0)").lastrowid
            g_off = c.execute("INSERT INTO pool_groups(bot_id, group_openid, real_gid, in_pool, state, added_ts) VALUES('B1','OFFICIAL1','654321',1,'free',0)").lastrowid
            c.execute("INSERT INTO person_qq(qq, member_openid, created_ts) VALUES('10001','M甲',0)")
        _, _, r1 = rooms.create("约会", self.a["id"], [self.b["id"]], "1000-1100", "花园")
        _, _, r2 = rooms.create("约会", self.a["id"], [self.c["id"]], "1200-1300", "湖边")
        with get_conn() as c:
            c.execute("UPDATE rooms SET pool_id=? WHERE id=?", (g_qq, r1))
            c.execute("UPDATE rooms SET pool_id=? WHERE id=?", (g_off, r2))
        rooms.end(r1); rooms.end(r2)

        async def members(bot_id, gid):
            return {"10001", "99999"}
        with mock.patch.object(qqmanager, "group_members", members):
            out = asyncio.run(overview.lingering(self.a))
        self.assertEqual(len(out), 1)                                                    # 官方机器人的群查不了成员，不列
        self.assertIn(f"R{r1}", out[0][1]); self.assertIn("还没退群", out[0][1]); self.assertIn("123456", out[0][1])

        async def gone(bot_id, gid):
            return {"99999"}
        with mock.patch.object(qqmanager, "group_members", gone):
            self.assertEqual(asyncio.run(overview.lingering(self.a)), [])                  # 退了就不列
        self.assertIn(out[0][1], overview.todo_items(self.a, out))

    def test_dismiss_key_follows_this_wait(self):
        """暂不提醒认的是「这一次」等待：对方再回一轮，key 变了，又会提醒。"""
        from changri.core import pending
        now = time.time()
        _, _, r1 = rooms.create("约会", self.a["id"], [self.b["id"]], "1000-1100", "花园")
        with get_conn() as c:
            g = c.execute("INSERT INTO pool_groups(bot_id, group_openid, real_gid, in_pool, state, added_ts) VALUES('B1','qq:1','1',1,'occupied',0)").lastrowid
            c.execute("UPDATE rooms SET pool_id=?", (g,))
        self._say(r1, self.b, now - 600)
        key = overview.todo_entries(self.a, now=now)[0][0]
        pending.set_dismissed(self.a["id"], key, True)
        self.assertIn(key, pending.dismissed(self.a["id"]))
        self._say(r1, self.a, now - 300); self._say(r1, self.b, now - 60)                # 又来回了一轮
        self.assertNotIn(overview.todo_entries(self.a, now=now)[0][0], pending.dismissed(self.a["id"]))
        pending.set_dismissed(self.a["id"], key, False)
        self.assertEqual(pending.dismissed(self.a["id"]), set())


if __name__ == "__main__":
    unittest.main()
