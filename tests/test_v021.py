"""0.21：我的弧长、寄信并进短信（混乱短信）、结戏奖励给抽取机会。"""
import asyncio
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "v021.db"))

from changri.core import arclen, chaosletter, extensions, gacha, rewards, roles, sendmsg  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class Sent(list):
    async def __call__(self, bot, gid, text):
        self.append((gid, text))
        return "mid"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class V021(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("messages", "chaos_letters", "room_lines", "room_members", "rooms", "reward_log", "reward_rules", "gacha_state", "gacha_prizes",
                      "gacha_pools", "blocks", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for i, n in enumerate("甲乙丙"):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        self.r = {n: roles.get_role_by_name(n) for n in "甲乙丙"}
        set_config("sms_cooldown_sec", "0")

    # ---------- 弧长 ----------
    def room(self, status, lines, members=("甲", "乙")):
        now = int(time.time())
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind, creator_id, status, created_ts) VALUES('约会',?,?,?)", (self.r["甲"]["id"], status, now - 7200)).lastrowid
            for m in members:
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, self.r[m]["id"]))
            for who, ago in lines:
                c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text) VALUES(?,?,?,?,'x')", (rid, now - ago, self.r[who]["id"], who))
        return rid

    def test_arc_report(self):
        self.room("ended", [("乙", 3600), ("甲", 3000)])                       # 甲 10 分钟后回
        rid = self.room("open", [("甲", 1800), ("乙", 1200)])                   # 乙说完 20 分钟了，等甲
        rep = arclen.report(self.r["甲"]["id"])
        self.assertIn("甲 的弧长", rep)
        self.assertIn("总平均：10 分钟（回复 1 次，2 场", rep)
        self.assertIn(f"R{rid} 约会：甲×乙 1v1（待你）", rep)
        self.assertIn("你还没回：20 分钟", rep)
        rep2 = arclen.report(self.r["乙"]["id"])
        self.assertIn("（待甲）", rep2)
        self.assertIn("甲未回：20 分钟", rep2)
        self.assertIn("还没有计时的回复", arclen.report(self.r["丙"]["id"]))
        self.assertEqual(arclen.report(9999), "")

    # ---------- 混乱短信 ----------
    def test_sms_plain_when_chaos_off(self):
        sent = Sent()
        ok, _ = run(sendmsg.send(self.r["甲"], self.r["乙"], "sms", "", "今天的月色很好", "", sent))
        self.assertTrue(ok)
        self.assertTrue(any("今天的月色很好" in t for _, t in sent))

    def test_sms_chaos_misdeliver_and_torn(self):
        extensions.set_enabled("chaos_letter", True)
        for k in ("chaos_typo", "chaos_lose", "chaos_blackout", "chaos_reverse", "chaos_sign"):
            set_config(k, "0")
        set_config("chaos_mis", "100"); set_config("chaos_torn", "100"); set_config("letter_public_send", "0")
        sent = Sent()
        ok, text = run(sendmsg.send(self.r["甲"], self.r["乙"], "sms", "", "今天的月色很好呀你呢", "", sent))
        self.assertTrue(ok)
        self.assertIn("已送达「乙」", text)                                          # 发件人不知道出了岔子
        with get_conn() as c:
            m = c.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1").fetchone()
            log = c.execute("SELECT * FROM chaos_letters").fetchall()
        self.assertEqual(m["to_role_id"], self.r["丙"]["id"])                         # 误投给了第三个人
        self.assertIn("后半截被撕走了", m["content"])
        self.assertEqual(len(log), 1)
        self.assertIn("误投", log[0]["effects"])
        extensions.set_enabled("chaos_letter", False)

    # ---------- 结戏奖励给抽取机会 ----------
    def test_reward_gives_gacha_tickets(self):
        self.assertTrue(gacha.save_pool("春池", "")[0])
        pid = gacha.pools()[0]["id"]
        fixed, err = rewards.rows_fixed([f"g:{pid}"], ["2"], ["100"])
        self.assertEqual(err, "")
        self.assertEqual(fixed[0]["type"], "gacha")
        self.assertTrue(rewards.rows_fixed(["g:9999"], ["1"])[1])                        # 池子不存在
        self.assertTrue(rewards.rows_fixed([f"g:{pid}"], ["-1"])[1])                     # 不能扣
        self.assertTrue(rewards.save_rule(None, "抽卡奖", ["约会"], {}, fixed, [], 1, True)[0])
        rule = rewards.rules()[0]
        self.assertIn("抽取机会「春池」×2", rewards.describe_rewards(rule))
        ok, got, err = rewards._grant(self.r["甲"]["id"], rule, 1)
        self.assertTrue(ok, err)
        self.assertIn("「春池」抽取机会 ×2", got[0])
        self.assertEqual(gacha.tickets(self.r["甲"]["id"])[0]["tickets"], 2)


    # ---------- 私约改名约会：老库迁移 ----------
    def test_migration_private_to_date(self):
        import shutil
        import sqlite3
        from changri.storage import migrate
        tmp = Path(tempfile.mkdtemp())
        old = tmp / "old"
        old.mkdir()
        for f in migrate.MIGRATIONS_DIR.iterdir():
            if f.name[:4].isdigit() and int(f.name[:4]) < 7:
                shutil.copy(f, old / f.name)
        db = tmp / "x.db"
        self.assertEqual(migrate.migrate(db, folder=old), 6)
        c = sqlite3.connect(db)
        c.execute("INSERT INTO roles(id, name, member_openid, created_ts) VALUES(1,'甲','M',0)")
        c.execute("INSERT INTO rooms(kind, creator_id, status, created_ts) VALUES('私约',1,'ended',0)")
        c.execute("INSERT INTO config(key, value) VALUES('limit_私约','3')")
        c.execute("INSERT INTO role_perms(role_id, feature, ts) VALUES(1,'私约',0)")
        c.execute("INSERT INTO reward_rules(name, enabled, kinds, conds, fixed, pool, pool_draws, created_ts) VALUES('r',1,'私约,电话','{}','[]','[]',1,0)")
        c.commit(); c.close()
        self.assertEqual(migrate.migrate(db), migrate.latest())
        c = sqlite3.connect(db)
        self.assertEqual(c.execute("SELECT kind FROM rooms").fetchone()[0], "约会")
        self.assertEqual(c.execute("SELECT value FROM config WHERE key='limit_约会'").fetchone()[0], "3")
        self.assertIsNone(c.execute("SELECT 1 FROM config WHERE key='limit_私约'").fetchone())
        self.assertEqual(c.execute("SELECT feature FROM role_perms").fetchone()[0], "约会")
        self.assertEqual(c.execute("SELECT kinds FROM reward_rules").fetchone()[0], "约会,电话")
        c.close()


if __name__ == "__main__":
    unittest.main()
