"""NPC 标记、漂流瓶、混乱短信、加百列闹钟、晚餐、上帝模式删时段、一键通知候选、合成。"""
import asyncio
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "x2.db"))

from changri.core import roomlog, mystats, alarm, arclen, blocks, bottle, chaosletter, dinner, godmode, npc, roles, roomnotify, rpg  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class Extras(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("wishes", "messages", "alarms", "group_alarms", "drift_msgs", "drift_bottles", "chaos_letters", "room_lines", "room_members", "rooms",
                      "rpg_recipe_items", "rpg_recipes", "rpg_inventory", "pool_groups", "blocks", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        for i, n in enumerate("甲乙丙丁"):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        self.r = {n: roles.get_role_by_name(n) for n in "甲乙丙丁"}

    def test_npc(self):
        ok, _ = npc.create("阿林")
        self.assertTrue(ok)
        self.assertFalse(npc.create("阿林")[0])
        self.assertTrue(npc.set_flag("乙", True)[0])
        self.assertEqual(sorted(npc.names()), ["乙", "阿林"])
        self.assertTrue(npc.is_npc(self.r["乙"]["id"]))
        self.assertFalse(npc.set_flag("乙", True)[0])
        self.assertTrue(npc.set_flag("乙", False)[0])
        self.assertEqual(npc.names(), ["阿林"])

    def test_bottle(self):
        me = self.r["甲"]
        npc.set_flag("乙", True)
        blocks.block(self.r["丙"]["id"], me["id"], True)       # 丙拉黑了甲：瓶子只能落到丁手里
        for _ in range(3):
            ok, tip, catcher, bid = bottle.throw(me, "有人吗")
            self.assertTrue(ok)
            self.assertEqual(catcher, self.r["丁"]["id"])
        ok, tip, *_ = bottle.throw(me, "第四次")
        self.assertFalse(ok)                                    # 默认每天 3 次
        ok, tip, other, deliver = bottle.reply(self.r["丁"], bid, "我在")
        self.assertTrue(ok and deliver)
        self.assertEqual(other, me["id"])
        self.assertFalse(bottle.reply(self.r["丙"], bid, "我是外人")[0])

    def test_chaos(self):
        for k in ("typo", "lose", "blackout", "reverse", "sign", "mis", "torn"):
            set_config(f"chaos_{k}", "0")
        c = chaosletter.compose(self.r["甲"], self.r["乙"], "今天的月色很好。你呢？", random.Random(1))
        self.assertEqual((c["shown"], c["sign"], c["true_to"], c["effects"]), ("今天的月色很好。你呢？", "甲", self.r["乙"]["id"], []))
        for k in ("typo", "lose", "blackout", "reverse", "sign", "mis", "torn"):
            set_config(f"chaos_{k}", "100")
        c = chaosletter.compose(self.r["甲"], self.r["乙"], "今天的月色很好。你呢？我也是。", random.Random(2))
        self.assertIn("误投", c["effects"])
        self.assertNotEqual(c["true_to"], self.r["乙"]["id"])
        self.assertIsNotNone(c["torn"])
        chaosletter.log(self.r["甲"]["id"], self.r["乙"]["id"], c, "x")
        self.assertEqual(chaosletter.day_count(self.r["甲"]["id"]), 1)

    def test_alarm(self):
        now = time.mktime((2026, 11, 14, 10, 0, 0, 0, 0, -1))
        self.assertEqual(alarm.parse_time("30分钟后", now), (int(now) + 1800, "none"))
        self.assertEqual(alarm.parse_time("每天20:00", now)[1], "daily")
        self.assertEqual(alarm.parse_time("每周一09:00", now)[1], "weekly:1")
        self.assertIsNone(alarm.parse_time("随便", now))
        self.assertIsNone(alarm.parse_time("25:00", now))
        me = self.r["甲"]["id"]
        ok, aid = alarm.add(me, int(time.time()) - 5, "喝水")
        self.assertTrue(ok)
        self.assertEqual(alarm.due(), [(me, "喝水")])
        self.assertEqual(alarm.mine(me), [])
        self.assertTrue(alarm.snooze(me, 5)[0])
        self.assertEqual(len(alarm.mine(me)), 1)
        alarm.add(me, int(time.time()) - 5, "日报", "daily")
        alarm.due()
        self.assertTrue(any(r["content"] == "日报" and r["trigger_ts"] > time.time() for r in alarm.mine(me)))
        self.assertIsNone(alarm.delete(self.r["乙"]["id"], aid))
        # 群体闹钟
        items, errs = alarm.parse_group_lines("明天 20:00 记得交日报\n乱写", now)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(errs), 1)
        n, _ = alarm.group_add([(int(time.time()) - 10, "过时的"), (int(time.time()) - 7200, "太晚的")])
        self.assertEqual(n, 2)
        self.assertEqual(alarm.group_due(), ["过时的"])        # 错过 1 小时以上的不补发

    def test_dinner(self):
        self.assertFalse(dinner.sit("甲", 1)[0])
        self.assertTrue(dinner.start(4, "无菜")[0])
        self.assertTrue(dinner.sit("甲", 1)[0])
        self.assertFalse(dinner.sit("乙", 1)[0])
        dinner.sit("乙", 4)
        dinner.sit("丙", 3)
        self.assertEqual(dinner.poke_check("甲", "乙")[0], "")                 # 首尾相邻
        self.assertIn("相邻", dinner.poke_check("甲", "丙")[0])
        self.assertIn("没有入座", dinner.poke_check("丁", "甲")[0])
        dinner.sit("甲", 2)                                                    # 换座
        self.assertEqual(dinner.state()["seats"][0], None)
        dinner.mark_poked(self.r["甲"]["id"])
        self.assertGreater(dinner.cooldown_left(self.r["甲"]["id"]), 0)
        self.assertTrue(dinner.stop())
        self.assertIsNone(dinner.state())

    def _room(self, with_lines=True):
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind,creator_id,day,start_hm,end_hm,status,created_ts) VALUES('约会',?,1,'1400','1500','ended',?)",
                            (self.r["甲"]["id"], int(time.time()) - 5 * 3600)).lastrowid
            for n in "甲乙":
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, self.r[n]["id"]))
            if with_lines:
                c.execute("INSERT INTO room_lines(room_id, ts, name, text) VALUES(?,?,?,?)", (rid, int(time.time()), "甲", "你好"))
        return rid

    def test_arclen(self):
        set_config("arc_max_hours", "3")
        rid = self._room(with_lines=False)
        now = int(time.time())
        with get_conn() as c:
            c.execute("UPDATE rooms SET status='open' WHERE id=?", (rid,))
            for who, name, ts in (("甲", "甲", now - 10 * 3600), ("乙", "乙", now - 9 * 3600), ("甲", "甲", now - 8 * 3600 + 600)):
                c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text) VALUES(?,?,?,?,?)", (rid, ts, self.r[who]["id"], name, "x"))
        st = arclen.stats(now)
        by = {p["name"]: p for p in st["people"]}
        self.assertAlmostEqual(by["乙"]["avg"], 1.0, places=1)             # 乙隔 1 小时回的
        self.assertFalse(by["乙"]["over"])
        self.assertFalse(by["甲"]["over"])                                  # 甲隔 50 分钟回
        w = st["waits"][0]                                                 # 甲最后发言 8 小时前，在等乙
        self.assertEqual((w["who"], w["over"]), ("乙", True))
        self.assertEqual(st["over_waits"], 1)

    def test_mystats(self):
        self.assertIn("暂无统计", mystats.build(self.r["丁"]["id"]))
        rid = self._room(with_lines=False)
        now = int(time.time())
        with get_conn() as c:
            for who, ts, n in (("甲", now - 3600, 50), ("乙", now - 1800, 30)):
                c.execute("INSERT INTO room_lines(room_id, ts, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,1,?)", (rid, ts, self.r[who]["id"], who, "x", n))
            c.execute("INSERT INTO messages(kind,from_role_id,to_role_id,created_ts,status) VALUES('sms',?,?,?,'sent')", (self.r["甲"]["id"], self.r["乙"]["id"], now))
        t = mystats.build(self.r["乙"]["id"])
        self.assertIn("累计字数：30 字", t)
        self.assertIn("平均耗时：30 分钟", t)
        self.assertIn("最喜欢给你发短信", t)
        self.assertIn("1. 甲（1）", t)
        with get_conn() as c:      # 甲挂的心愿被乙摘走：甲看到「最喜欢摘你心愿：乙」，乙看到「你最喜欢摘谁的心愿：甲」
            c.execute("INSERT INTO wishes(poster_id, taker_id, status, created_ts) VALUES(?,?,'taken',?)", (self.r["甲"]["id"], self.r["乙"]["id"], now))
        a, b = mystats.build(self.r["甲"]["id"]), mystats.build(self.r["乙"]["id"])
        self.assertIn("最喜欢摘你心愿：\n　1. 乙（1）", a)
        self.assertNotIn("你最喜欢摘谁的心愿", a)
        self.assertIn("你最喜欢摘谁的心愿：\n　1. 甲（1）", b)

    def test_line_crud(self):
        rid = self._room(with_lines=False)
        a, b, c = (self.r[n]["id"] for n in "甲乙丙")
        self.assertTrue(roomlog.add_line(rid, a, "甲：今天天气很好")[0])
        self.assertFalse(roomlog.add_line(rid, c, "丙不在房间里")[0])
        self.assertFalse(roomlog.add_line(rid, a, "  ")[0])
        ln = roomlog.lines(rid)[0]
        self.assertEqual((ln["is_rp"], ln["chars"], ln["name"]), (1, 6, "甲"))
        self.assertTrue(roomlog.edit_line(rid, ln["id"], "随便聊聊", b)[0])      # 改内容和说话的人：变成闲聊
        ln = roomlog.lines(rid)[0]
        self.assertEqual((ln["is_rp"], ln["chars"], ln["name"], ln["role_id"]), (0, 0, "乙", b))
        self.assertFalse(roomlog.edit_line(rid, ln["id"], "", b)[0])
        self.assertFalse(roomlog.edit_line(rid + 99, ln["id"], "x")[0])
        self.assertTrue(roomlog.delete_line(rid, ln["id"]))
        self.assertFalse(roomlog.delete_line(rid, ln["id"]))

    def test_recall_and_near_duplicate(self):
        rid = self._room(with_lines=False)
        now = int(time.time())

        def put(qq, text, mid, ts):
            with get_conn() as c:
                roomlog._drop_near_duplicate(c, rid, qq, text, ts)
                c.execute("INSERT INTO room_lines(room_id, ts, qq, name, text, msg_id) VALUES(?,?,?,?,?,?)", (rid, ts, qq, "甲", text, mid))
        put("1", "今天的月色真的很好看呀", "m1", now)
        put("1", "今天的月色真的很好看呀！", "m2", now + 60)         # 几乎一样：只留后一条
        self.assertEqual([x["msg_id"] for x in roomlog.lines(rid)], ["m2"])
        put("2", "今天的月色真的很好看呀！", "m3", now + 90)         # 别人说的一样的话：不动
        put("1", "我们去河边走走吧，晚风很舒服", "m4", now + 120)    # 不相似：都留
        put("1", "好", "m5", now + 130)
        put("1", "好", "m6", now + 140)                               # 太短：不算重复
        self.assertEqual([x["msg_id"] for x in roomlog.lines(rid)], ["m2", "m3", "m4", "m5", "m6"])
        put("1", "我们去河边走走吧，晚风很舒服", "m7", now + 4000)    # 隔了 30 分钟以上：不算重复
        self.assertEqual(len(roomlog.lines(rid)), 6)
        set_config("room_dedup_similarity", "0")
        put("1", "我们去河边走走吧，晚风很舒服", "m8", now + 4010)    # 关掉：不处理
        self.assertEqual(len(roomlog.lines(rid)), 7)
        self.assertEqual(roomlog.recall("m4"), 1)                    # QQ 里撤回：复盘同步删
        self.assertEqual(roomlog.recall("m4"), 0)
        self.assertEqual(roomlog.recall(""), 0)

    def test_delete_slot(self):
        rid = self._room()
        self.assertEqual(len(godmode.timeline_all(self.r["甲"]["id"])), 1)
        self.assertFalse(godmode.delete_slot(self.r["丙"]["id"], rid)[0])
        self.assertTrue(godmode.delete_slot(self.r["甲"]["id"], rid)[0])
        self.assertEqual(godmode.timeline_all(self.r["乙"]["id"]), [])         # 乙的时间线上也没了
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM room_lines").fetchone()[0], 0)   # 复盘记录一起删

    def test_roomnotify_candidates(self):
        with get_conn() as c:
            pid = c.execute("INSERT INTO pool_groups(bot_id, group_openid, real_gid, state, in_pool, added_ts) VALUES('B','G9','123','occupied',1,0)").lastrowid
            rid = c.execute("INSERT INTO rooms(kind,creator_id,day,start_hm,end_hm,status,pool_id,created_ts) VALUES('约会',?,1,'1400','1500','open',?,?)",
                            (self.r["甲"]["id"], pid, int(time.time()) - 5 * 3600)).lastrowid
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, self.r["甲"]["id"]))
        self.assertEqual(len(roomnotify.candidates(2, "idle")), 1)
        self.assertEqual(len(roomnotify.candidates(6, "open")), 0)
        sent = []

        async def send(b, g, t):
            sent.append((b, g, t))
        ok, bad = asyncio.run(roomnotify.notify(roomnotify.candidates(2, "idle"), "快回", "B", send, None))
        self.assertEqual((ok, bad, sent), (1, [], [("B", "G9", "快回")]))

    def test_craft(self):
        me = self.r["甲"]["id"]
        rpg.seed_defaults()
        for code, name in (("SUIPIAN", "碎片"), ("MUTOU", "木头"), ("WANOU", "玩偶")):
            rpg.save_item(code, name)
        ok, msg = rpg.save_recipe("做玩偶", [("碎片", 3), ("木头", 1)], "玩偶", 1)
        self.assertTrue(ok, msg)
        self.assertFalse(rpg.save_recipe("坏", [], "玩偶", 1)[0])
        with get_conn() as c:
            it = {k: dict(c.execute("SELECT * FROM rpg_items WHERE code=?", (k,)).fetchone()) for k in ("SUIPIAN", "MUTOU", "WANOU")}
            rpg._change(c, me, it["SUIPIAN"], 3, "gm", "")                     # 只有碎片，没有木头
        ok, msg = rpg.craft(me, "做玩偶")
        self.assertFalse(ok)
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM rpg_inventory WHERE role_id=? AND item_code='SUIPIAN'", (me,)).fetchone()[0], 1)   # 碎片没被扣
            rpg._change(c, me, it["MUTOU"], 1, "gm", "")
        ok, msg = rpg.craft(me, "做玩偶")
        self.assertTrue(ok, msg)
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT count FROM rpg_inventory WHERE role_id=? AND item_code='WANOU'", (me,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()


class CollectTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("collect_items", "collections", "role_groups", "roles"):
                c.execute(f"DELETE FROM {t}")
        for i, n in enumerate("甲乙"):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        self.a = roles.get_role_by_name("甲")["id"]
        self.b = roles.get_role_by_name("乙")["id"]

    def test_flow(self):
        from changri.core import collect
        now = int(time.time())
        self.assertFalse(collect.create("", "", now, now + 60, 1)[0])
        ok, cid0 = collect.create("不限时", "", 0, 0)                  # 开始、结束都不填：现在开始、一直收
        self.assertTrue(ok)
        self.assertIn("不限时", collect.describe_open())
        collect.delete(int(cid0))
        self.assertFalse(collect.create("x", "", now, now - 1, 1)[0])
        self.assertFalse(collect.create("x", "", now, now + 2 * 86400, 3)[0])
        ok, cid = collect.create("晚间", "说一句", now - 60, now + 3600, 1)
        self.assertTrue(ok)
        cid = int(cid)
        self.assertEqual(collect.submit(self.a, "你好")[0], True)
        self.assertIn("更新", collect.submit(self.a, "你好呀")[1])
        self.assertTrue(collect.submit(self.b, "嗨", cid)[0])
        its = collect.items(cid, 0)
        self.assertEqual([i["content"] for i in its], ["你好呀", "嗨"])
        self.assertEqual(collect.missing(cid, 0), [])
        collect.set_closed(cid, True)
        self.assertFalse(collect.submit(self.a, "晚了")[0])
        self.assertEqual(collect.status(collect.get(cid))[0], "已关闭")
        collect.set_closed(cid, False)
        self.assertTrue(collect.submit(self.a, "又开了")[0])

    def test_repeat_rounds(self):
        from changri.core import collect
        now = int(time.time())
        # 第 1 轮已过，现在在第 2 轮
        ok, cid = collect.create("每日", "", now - 86400 - 60, now - 86400 + 3600, 3)
        col = collect.get(int(cid))
        self.assertEqual(collect.active_round(col, now), 1)
        self.assertEqual(collect.active_round(col, now - 86400 + 100), 0)
        self.assertIsNone(collect.active_round(col, now + 5 * 3600))
        ok, cid2 = collect.create("每日2", "", now - 86400 + 60, now - 86400 + 7200, 3)   # 第 2 轮正好是现在附近
        self.assertEqual(collect.active_round(collect.get(int(cid2)), now - 86400 + 100 + 86400), 1)
        self.assertEqual(collect.status(collect.get(int(cid2)), now + 10 * 86400)[0], "已结束")


class GachaTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("gacha_log", "gacha_state", "gacha_prizes", "gacha_pools", "rpg_inventory", "role_groups", "roles"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B", "G0", "M0")
        self.a = roles.get_role_by_name("甲")["id"]
        rpg.save_item("gc_a", "普通糖", "item")
        rpg.save_item("gc_b", "闪闪星", "item")

    def test_draw_and_pity(self):
        from changri.core import gacha
        self.assertTrue(gacha.save_pool("春", "", 3)[0])
        self.assertFalse(gacha.save_pool("春", "")[0])
        pid = gacha.pools()[0]["id"]
        self.assertTrue(gacha.add_prize(pid, "gc_a", 1, 1000)[0])
        self.assertTrue(gacha.add_prize(pid, "gc_b", 1, 1, True)[0])
        self.assertFalse(gacha.add_prize(pid, "gc_b")[0])
        self.assertFalse(gacha.draw(self.a, "", 1)[0])             # 没次数
        self.assertTrue(gacha.grant(self.a, "春", 6)[0])
        self.assertEqual(gacha.tickets(self.a)[0]["tickets"], 6)
        ok, tip = gacha.draw(self.a, "", 3)                       # 权重 1000:1，第 3 抽必然保底
        self.assertTrue(ok)
        self.assertIn("保底", tip)
        self.assertEqual(sum(1 for i in rpg.inventory(self.a) if i["item_code"] == "GC_B"), 1)
        self.assertEqual(gacha.my_status(self.a)[0]["tickets"], 3)
        self.assertFalse(gacha.draw(self.a, "", 4)[0])
        self.assertIn("闪闪星", gacha.pool_text("春"))
        self.assertIn("春", gacha.pool_text(""))
