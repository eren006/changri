import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "s.db")

from changri.core import entrance, lovemail, roles, season  # noqa: E402
from changri.core.db import get_config, get_conn, init_db, set_config  # noqa: E402


def at(date: str, hm: str = "12:00") -> time.struct_time:
    return time.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M")


class Sent(list):
    async def __call__(self, bot, gid, text):
        self.append((bot, gid, text))


class SeasonTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("seasons", "entrance", "lovemails", "rel_details", "relations", "messages", "blocks", "bind_codes", "role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")

    def run_tick(self, date, hm="12:00"):
        sent = Sent()
        r = asyncio.new_event_loop().run_until_complete(season.tick(sent, ("B", "G"), at(date, hm)))
        return r, sent

    def test_parse(self):
        self.assertEqual(season.parse_range("1115-1120", "2026-11-01"), ("2026-11-15", "2026-11-20"))
        self.assertEqual(season.parse_range("1228-0102", "2026-11-01"), ("2026-12-28", "2027-01-02"))
        self.assertIsNone(season.parse_range("1315-1120"))
        self.assertEqual(season.parse_mmdd("补戏1123", "2026-11-01"), "2026-11-23")

    def test_book_validation(self):
        self.assertTrue(season.book("甲季", "2026-11-15", "2026-11-20")[0])
        self.assertFalse(season.book("甲季", "2026-12-01", "2026-12-02")[0])          # 重名
        self.assertFalse(season.book("乙季", "2026-11-20", "2026-11-15")[0])
        self.assertFalse(season.book("丙季", "2026-11-15", "2026-11-20", "2026-11-18")[0])
        self.assertTrue(season.delete_booking(season.booked()[0]["id"]))

    def test_lifecycle_does_not_depend_on_real_date(self):
        """以前进入 D0 时记的是「真实的今天」：真实日期碰巧等于开始日时，当晚不进 D1（D0 变成两天）。现在只看触发时的日期。"""
        from unittest import mock
        for real in ("2026-11-15", "2030-01-01"):                                  # 碰巧同一天 / 完全不相干的一天
            with self.subTest(real=real), mock.patch.object(season, "today", lambda now=None, _r=real: time.strftime("%Y-%m-%d", now) if now else _r):
                self.setUp()
                season.book("甲季", "2026-11-15", "2026-11-17", "2026-11-18", "")
                season.start("", now="2026-11-14")
                self.assertEqual(self.run_tick("2026-11-15", "00:05")[0], "d0")
                self.assertEqual(self.run_tick("2026-11-15", "23:59")[0], "advanced")     # 开始日当天就是 D0，当晚进 D1
                self.assertEqual(season.current_day(), 1)
                season.end()

    def test_manual_start_after_start_date_keeps_d0_tonight(self):
        """档期已经开始后管理员才手动「开始季度」（可能已经很晚）：当晚不加一天，D0 留到第二天晚上。"""
        season.book("乙季", "2026-11-10", "2026-11-17", "2026-11-18", "")
        ok, msg = season.start("", now="2026-11-15")
        self.assertTrue(ok)
        self.assertIn("直接进入 D0", msg)
        self.assertEqual(self.run_tick("2026-11-15", "23:59")[0], "idle")
        self.assertEqual(season.current_day(), 0)
        self.assertEqual(self.run_tick("2026-11-16", "23:59")[0], "advanced")
        self.assertEqual(season.current_day(), 1)

    def test_missed_2359_is_caught_up_next_day(self):
        """23:59 那一分钟机器人不在线（更新重启、断网）：第二天上线补推进一次，不会整季少一天；同一天不重复补。"""
        season.book("甲季", "2026-11-15", "2026-11-20")
        season.start("", now="2026-11-14")
        self.assertEqual(self.run_tick("2026-11-15", "00:05")[0], "d0")
        r, sent = self.run_tick("2026-11-16", "08:30")                             # 11-15 的 23:59 错过了
        self.assertEqual(r, "caught_up")
        self.assertEqual(season.current_day(), 1)
        self.assertIn("补推进", sent[-1][2])
        self.assertEqual(self.run_tick("2026-11-16", "09:00")[0], "idle")
        self.assertEqual(self.run_tick("2026-11-16", "23:59")[0], "advanced")       # 当晚照常
        self.assertEqual(season.current_day(), 2)
        self.assertEqual(self.run_tick("2026-11-17", "00:01")[0], "idle")          # 刚推进过的不再补
        self.assertEqual(season.current_day(), 2)

    def test_d0_entered_late_is_not_caught_up(self):
        """开始日机器人不在线、第二天才自动进 D0：进 D0 那天就是 D0，不会马上补成 D1。"""
        season.book("甲季", "2026-11-15", "2026-11-20")
        season.start("", now="2026-11-14")
        self.assertEqual(self.run_tick("2026-11-16", "10:00")[0], "d0")
        self.assertEqual(self.run_tick("2026-11-16", "10:01")[0], "idle")
        self.assertEqual(season.current_day(), 0)

    def test_legacy_empty_marker_only_advances_at_2359(self):
        """老版本自动进 D0 时 auto_day_last 记成空：升级后白天不能误补一天。"""
        season.book("甲季", "2026-11-15", "2026-11-20")
        season.start("", now="2026-11-14")
        self.run_tick("2026-11-15", "00:05")
        set_config("auto_day_last", "")
        self.assertEqual(self.run_tick("2026-11-15", "15:00")[0], "idle")
        self.assertEqual(self.run_tick("2026-11-15", "23:59")[0], "advanced")
        self.assertEqual(season.current_day(), 1)

    def test_wipe_preview_lists_everything_that_goes(self):
        """收尾预览以前只列角色、短信、心动信、关系线 4 项，心愿、房间、论坛也会被删却没说。角色一直列，别的有才列。"""
        self.assertEqual(season.wipe_preview(), {"角色": 0})
        roles.create_role("甲", "B", "G1", "M1")
        roles.create_role("乙", "B", "G2", "M2")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        lovemail.add_mail(a, b, "匿名", "信")
        with get_conn() as c:
            c.execute("DELETE FROM wishes")
        pv = season.wipe_preview()
        self.assertEqual(pv["角色"], 2)
        self.assertEqual(pv["心动信"], 1)
        self.assertNotIn("心愿", pv)                                                 # 没有的不列
        self.assertNotIn("短信和礼物", pv)

    def test_stage_warnings(self):
        """「季度状态」的舞台检查：没设的群、没空群都点出来；设齐了就没有。"""
        from changri.core import announce, entrance
        w = "\n".join(season.stage_warnings())
        for k in ("公告群", "后台群", "戏群", "备用群池"):
            self.assertIn(k, w)
        announce.set_announce("B", "ANN")
        announce.set_backstage("B", "BS")
        entrance.set_stage("B", "STAGE")
        w = "\n".join(season.stage_warnings())
        for k in ("公告群", "后台群", "戏群"):
            self.assertNotIn("没设" + k, w)
        self.assertIn("备用群池", w)

    def test_prep_note_only_in_prep(self):
        """筹备期拉线、挂心愿、写心动信的回执末尾提醒「到 D0 会被清掉」；正式开季后没有。"""
        season.book("甲季", "2026-11-15", "2026-11-20")
        season.start("", now="2026-11-14")
        self.assertIn("筹备期", season.prep_note("关系线"))
        self.assertIn("11-15", season.prep_note("关系线"))
        self.run_tick("2026-11-15", "00:05")
        self.assertEqual(season.prep_note("关系线"), "")

    def test_end_reminds_players_to_save(self):
        """结束季度：没开季末报告时，给每位玩家（不含 NPC）的个人群单独发「收尾前存档」提醒；开了就并在报告里。"""
        season.book("甲季", "2026-11-15", "2026-11-20")
        season.start("", now="2026-11-14")
        roles.create_role("甲", "B", "G1", "M1")
        roles.create_role("乙", "B", "G2", "M2")
        sent = Sent()
        ok, msg = asyncio.new_event_loop().run_until_complete(season.end_and_report(sent))
        self.assertTrue(ok)
        self.assertIn("已提醒 2 位玩家", msg)
        to_players = [t for _, g, t in sent if g in ("G1", "G2")]
        self.assertEqual(len(to_players), 2)
        self.assertTrue(all("复盘 R12" in t and "收尾" in t for t in to_players))

    def test_wipe_clears_dinner_games_and_plans(self):
        """收尾清掉存在设置里、记着本季角色的小游戏和计划官约；上季没「结束晚餐」也不会带到下一季。别的设置不动。"""
        for k in season.SEASON_CONFIG_KEYS:
            set_config(k, '{"x": 1}')
        set_config("poke_at:3", "1")
        set_config("alarm_last:3", "x")
        set_config("guard_locations", "后山")
        set_config("lovemail_delivery_time", "21:30")
        season.wipe()
        for k in (*season.SEASON_CONFIG_KEYS, "poke_at:3", "alarm_last:3"):
            self.assertEqual(get_config(k, "空"), "空", k)
        self.assertEqual(get_config("guard_locations"), "后山")
        self.assertEqual(get_config("lovemail_delivery_time"), "21:30")

    def test_auto_day_announces_today_counts(self):
        """自动换天时公告群那条带上全员今天的计数（换天之前算）。"""
        season.book("丁季", "2026-11-10", "2026-11-17", "2026-11-18", "")
        season.start("", now="2026-11-15")
        key = season.day_key()
        line = season.day_summary_line()
        self.assertIn("约会 0 场", line); self.assertIn("短信 0 条", line)
        r, sent = self.run_tick("2026-11-16", "23:59")
        self.assertEqual(r, "advanced")
        self.assertIn("🌐 全员今天：", sent[-1][2])
        self.assertNotEqual(season.day_key(), key)

    def test_lifecycle_and_auto_day(self):
        season.book("甲季", "2026-11-15", "2026-11-17", "2026-11-18", "阿林、阿森")
        ok, msg = season.start("", now="2026-11-14")
        self.assertTrue(ok)
        self.assertEqual(season.phase("2026-11-14"), "prep")
        self.assertEqual(season.day_label(), "筹备期")
        self.assertFalse(season.start()[0])                                       # 已有进行中
        r, sent = self.run_tick("2026-11-14")
        self.assertEqual(r, "idle")
        r, sent = self.run_tick("2026-11-15", "00:05")
        self.assertEqual(r, "d0")
        self.assertEqual(season.day_label(), "D0")
        self.assertEqual(get_config("auto_day"), "1")
        self.assertTrue(sent and "D0" in sent[0][2])
        r, _ = self.run_tick("2026-11-15", "23:59")
        self.assertEqual(r, "advanced")
        self.assertEqual(season.current_day(), 1)
        self.assertEqual(self.run_tick("2026-11-15", "23:59")[0], "idle")          # 同一天不重复加
        r, sent = self.run_tick("2026-11-19")                                      # 补戏期也过了
        self.assertEqual(r, "closed")
        self.assertEqual(get_config("auto_day"), "0")
        self.assertEqual(get_config("lovemail_enabled"), "0")
        self.assertEqual(season.phase("2026-11-19"), "over")
        self.assertTrue(season.end()[0])
        self.assertEqual(season.phase(), "ended")

    def _prep_stuff(self):
        """筹备期里攒下的东西：短信、心动信、关系线、心愿、已结束和进行中的房间。"""
        with get_conn() as c:
            for t in ("rooms", "wishes"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B1", "P0", "M0")
        roles.create_role("乙", "B1", "P1", "M1")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        now = int(time.time())
        with get_conn() as c:
            c.execute("INSERT INTO messages(kind, from_role_id, to_role_id, content, created_ts) VALUES('sms',?,?,?,?)", (a, b, "筹备期的短信", now))
            c.execute("INSERT INTO lovemails(from_role_id, to_role_id, content, day, created_ts) VALUES(?,?,?,?,?)", (a, b, "信", "2026-11-14", now))
            rid = c.execute("INSERT INTO relations(role_lo, role_hi, initiator_id, created_ts) VALUES(?,?,?,?)", (a, b, a, now)).lastrowid
            c.execute("INSERT INTO rel_details(rel_id, from_role_id, text, created_ts) VALUES(?,?,?,?)", (rid, a, "细节", now))
            c.execute("INSERT INTO wishes(poster_id, content, created_ts) VALUES(?,?,?)", (a, "心愿", now))
            done = c.execute("INSERT INTO rooms(kind, creator_id, status, created_ts) VALUES('微信',?,'ended',?)", (a, now)).lastrowid
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (done, b))
            c.execute("INSERT INTO rooms(kind, creator_id, status, created_ts) VALUES('微信',?,'open',?)", (a, now))

    def _counts(self):
        with get_conn() as c:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("messages", "lovemails", "relations", "rel_details", "wishes", "rooms", "room_members", "roles")}

    def test_prep_interactions_cleared_at_d0(self):
        season.book("甲季", "2026-11-15", "2026-11-20")
        self.assertTrue(season.start(now="2026-11-14")[0])
        self._prep_stuff()
        self.assertEqual(self._counts()["messages"], 1)
        self.run_tick("2026-11-14", "12:00")                                       # 筹备期里的定时检查不清
        self.assertEqual(self._counts()["messages"], 1)
        r, sent = self.run_tick("2026-11-15", "00:05")
        self.assertEqual(r, "d0")
        n = self._counts()
        self.assertEqual((n["messages"], n["lovemails"], n["relations"], n["rel_details"], n["wishes"]), (0, 0, 0, 0, 0))
        self.assertEqual((n["rooms"], n["room_members"]), (1, 0))                  # 已结束的房间清了，进行中的留着
        self.assertEqual(n["roles"], 2)                                            # 角色不动
        self.assertIn("清零", sent[0][2])

    def test_manual_set_day_from_prep_also_clears(self):
        season.book("甲季", "2026-11-20", "2026-11-25")
        self.assertTrue(season.start(now="2026-11-14")[0])
        self._prep_stuff()
        season.set_day(0)
        self.assertEqual(self._counts()["messages"], 0)
        with get_conn() as c:
            c.execute("INSERT INTO messages(kind, from_role_id, to_role_id, content, created_ts) SELECT 'sms', id, id, 'x', 1 FROM roles LIMIT 1")
        season.set_day(1)                                                          # 已经在进行中，不再清
        self.assertEqual(self._counts()["messages"], 1)

    def test_start_inside_schedule_goes_d0(self):
        season.book("甲季", "2026-11-10", "2026-11-20")
        self.assertTrue(season.start(now="2026-11-12")[0])
        self.assertEqual(season.current_day(), 0)

    def test_wipe_guard_and_wipe(self):
        roles.create_role("甲", "B", "G1", "M1")
        season.book("甲季", "2026-11-10", "2026-11-20")
        ok, msg = season.start(now="2026-11-12")
        self.assertEqual((ok, msg), (False, "NEEDS_WIPE"))
        self.assertTrue(season.start(clear=True, now="2026-11-12")[0])
        self.assertEqual(season.role_count(), 0)
        self.assertEqual(len(season.all_seasons()), 1)                             # 季度记录保留

    def test_end_then_wipe_state(self):
        season.book("甲季", "2026-11-10", "2026-11-20")
        season.start(now="2026-11-12")
        season.end()
        self.assertEqual(season.phase(), "ended")
        season.wipe()
        self.assertEqual(season.phase(), "none")

    def test_day_key_resets_lovemail_limit(self):
        self.assertEqual(lovemail.today(), time.strftime("%Y-%m-%d"))              # 没季度按自然日
        season.book("甲季", "2026-11-10", "2026-11-20")
        self.assertTrue(season.start(now="2026-11-12")[0])
        roles.create_role("甲", "B", "G1", "M1")
        roles.create_role("乙", "B", "G2", "M2")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        k0 = lovemail.today()
        self.assertTrue(lovemail.add_mail(a, b, "匿名", "你好")[0])
        self.assertEqual(lovemail.day_count(a), 1)
        season.set_day(1)
        self.assertNotEqual(lovemail.today(), k0)
        self.assertEqual(lovemail.day_count(a), 0)                                 # 天数一变，计数重新开始


class EntranceTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("seasons", "entrance", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for i, n in enumerate(["甲", "乙", "丙", "阿林"]):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        season.book("甲季", "2026-11-10", "2026-11-20", "", "阿林")
        season.start(clear=True, now="2026-11-12")
        for i, n in enumerate(["甲", "乙", "丙", "阿林"]):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        from changri.core import npc
        npc.set_flag("阿林", True)
        entrance.set_stage("B", "STAGE")
        self.sent = Sent()

    def go(self, announce=False):
        asyncio.new_event_loop().run_until_complete(entrance.advance(self.sent, announce))

    def test_roster_excludes_npc(self):
        self.assertEqual(sorted(r["name"] for r in entrance.roster()), ["丙", "乙", "甲"])

    def test_flow(self):
        order = [r["name"] for r in entrance.draw()]
        self.assertEqual(sorted(order), ["丙", "乙", "甲"])
        self.assertEqual(entrance.start()[0], True)
        self.assertFalse(entrance.start()[0])
        first = entrance.current()
        self.go(True)
        self.assertEqual(len(self.sent), 1)                                        # 提醒发到本人个人群，不是戏群
        self.assertNotEqual(self.sent[-1][1], "STAGE")
        self.assertIn("轮到你出场", self.sent[-1][2])
        second_id = entrance.queue()[1]["role_id"]
        self.assertTrue(entrance.submit(second_id, "我先写好了")[0])               # 提前交
        self.go()
        self.assertEqual(len(self.sent), 1)                                        # 第一位没交，还停着
        self.assertTrue(entrance.submit(first["role_id"], "第一位出场")[0])
        self.go()
        texts = [m[2] for m in self.sent]
        self.assertTrue(any("第一位出场" in t for t in texts) and any("我先写好了" in t for t in texts))
        self.assertIn("轮到你出场", texts[-1])                                     # 第三位提醒（个人群）
        self.assertNotEqual(self.sent[-1][1], "STAGE")
        self.assertFalse(entrance.submit(first["role_id"], "再来一次")[0])         # 已出过场
        third = entrance.current()
        entrance.submit(third["role_id"], "最后一位")
        self.go()
        self.assertIn("全部完成", self.sent[-1][2])
        self.assertFalse(entrance.active())

    def test_skip_moves_to_tail(self):
        entrance.draw()
        entrance.start()
        cur = entrance.current()["name"]
        self.assertTrue(entrance.skip()[0])
        self.assertNotEqual(entrance.current()["name"], cur)
        self.assertEqual(entrance.queue()[-1]["name"], cur)

    def test_start_needs_stage_and_queue(self):
        entrance.set_stage("", "")
        entrance.draw()
        self.assertFalse(entrance.start()[0])
        entrance.set_stage("B", "S")
        with get_conn() as c:
            c.execute("DELETE FROM entrance")
        self.assertFalse(entrance.start()[0])

    def test_format_and_images(self):
        entrance.draw()
        entrance.start()
        q = entrance.queue()
        a, b = q[0], q[1]
        sent_imgs = []

        async def send_image(bot, gid, data):
            sent_imgs.append((gid, data))

        def run(announce=False):
            asyncio.new_event_loop().run_until_complete(entrance.advance(self.sent, announce, send_image))

        self.assertTrue(entrance.submit(a["role_id"], "第一段\n第二段", [b"IMG1", b"IMG2"])[0])
        self.assertTrue(entrance.submit(b["role_id"], "", [b"ONLY"])[0])              # 只发图也行
        run(True)
        stage = [m[2] for m in self.sent if m[1] == "STAGE"]
        self.assertEqual(stage[0], f"{a['name']}\n\n第一段\n第二段")                  # 名字、空行、正文，没有 emoji
        self.assertEqual(stage[1], b["name"])                                         # 只有图时只发名字
        self.assertEqual([d for _, d in sent_imgs], [b"IMG1", b"IMG2", b"ONLY"])      # 图在各自正文之后
        self.assertTrue(all(g == "STAGE" for g, _ in sent_imgs))
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM entrance_images").fetchone()[0], 0)   # 发完就清掉
        self.assertFalse(any("🎭" in m[2] for m in self.sent))
        self.assertFalse(entrance.submit(a["role_id"], "", [])[0])

    def test_mark_posted_no_resend(self):
        entrance.draw()
        entrance.start()
        a = entrance.queue()[0]
        entrance.submit(a["role_id"], "预存的", [b"X"])
        self.assertTrue(entrance.mark_posted(a["role_id"]))
        self.assertFalse(entrance.mark_posted(a["role_id"]))
        self.go(True)
        self.assertFalse(any("预存的" in m[2] for m in self.sent))                 # 没有重发
        self.assertNotEqual(entrance.current()["role_id"], a["role_id"])

    def test_split_name_text(self):
        self.assertEqual(entrance.split_name_text("甲 你好", "甲"), "你好")
        self.assertEqual(entrance.split_name_text("甲：你好", "甲"), "你好")
        self.assertEqual(entrance.split_name_text("甲\n你好", "甲"), "你好")
        self.assertIsNone(entrance.split_name_text("甲乙 你好", "甲"))
        self.assertIsNone(entrance.split_name_text("乙 你好", "甲"))
        self.assertEqual(entrance.split_name_text("甲", "甲"), "")


if __name__ == "__main__":
    unittest.main()
