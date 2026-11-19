"""照老长日系统补的规则：戏文括号场外话 / 简称 / 重发替换、弧长口径、约会最短时长 / 禁约小时 / 撞档期留言、
礼物丢失误送、「第 N 次记录」公告、通用物品库。"""
import asyncio, os, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "legacy.db"))
from changri.core import arclen, milestone, npc, pool, roles, roomlog, rooms, rpg, sendmsg, unilib, wishes  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("universal_items", "wishes", "messages", "chaos_letters", "room_lines", "room_members", "rooms", "rpg_shop", "rpg_inventory",
                      "rpg_items", "time_locks", "pool_groups", "blocks", "role_profiles", "role_groups", "roles", "seasons", "config"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        for i, n in enumerate("甲乙丙丁"):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        self.r = {n: roles.get_role_by_name(n) for n in "甲乙丙丁"}
        self.id = {n: self.r[n]["id"] for n in "甲乙丙丁"}
        for i in range(8):
            pool.register("B", f"POOL{i}", real_gid=f"9900{i}", set_name="A组")
        set_config("game_day", "1")

    def room(self, kind, who, time_text, place="客厅"):
        ok, info, rid = rooms.create(kind, self.id[who[0]], [self.id[x] for x in who[1:]], time_text, place if rooms.KINDS[kind] else "")
        return ok, info, rid

    def say(self, rid, who, ts, text=None, rp=1):
        with get_conn() as c:
            c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars) VALUES(?,?,?,?,?,?,?,?)",
                      (rid, ts, who, self.id[who], who, text or f"{who}：戏", rp, 1))


class RpText(Base):
    def test_ooc(self):
        self.assertEqual(roomlog.strip_ooc("走吧（等我一下（马上））好(ooc)。"), "走吧好。")
        self.assertEqual(roomlog.strip_ooc("走吧（没打完"), "走吧（没打完")
        self.assertEqual(roomlog.parse_rp("甲：走吧（场外）", {"甲"}), ("甲", "走吧"))
        self.assertIsNone(roomlog.parse_rp("甲：（全是场外）", {"甲"}))                   # 去完没字了：算闲聊

    def test_short_name_and_store(self):
        with get_conn() as c:
            c.execute("INSERT INTO role_profiles(role_id, short) VALUES(?,?) ON CONFLICT(role_id) DO UPDATE SET short=excluded.short", (self.id["甲"], "阿甲"))
        ok, _info, rid = self.room("约会", "甲乙", "1400-1500")
        self.assertTrue(ok)
        names = roomlog.names_of(rooms.members(rid))
        self.assertEqual(names["阿甲"], "甲")
        text, rp = roomlog.clean("阿甲\n　　走吧（这句不算）", names)
        self.assertEqual((text, rp), ("甲\n走吧", ("甲", "走吧")))
        raw = "甲\n　　第一段\n\n　　第二段"
        self.assertEqual(roomlog.clean(raw, names)[0], raw)                             # 没有场外话、首行是本名：排版原样
        self.assertEqual(roomlog.clean("随便聊聊（哈哈）", names), ("随便聊聊（哈哈）", None))
        self.assertTrue(roomlog.add_line(rid, self.id["甲"], "阿甲：你好（ooc）")[0])
        ln = roomlog.lines(rid)[0]
        self.assertEqual((ln["text"], ln["is_rp"], ln["chars"]), ("甲：你好", 1, 2))

    def test_redo(self):
        self.assertGreaterEqual(roomlog.similar("今天天气真好我们出去走走吧", "今天天气真好，我们出去走一走吧"), 0.6)
        self.assertLess(roomlog.similar("今天天气真好我们出去走走吧", "晚上一起吃饭吗我请客"), 0.6)
        ok, _i, rid = self.room("约会", "甲乙", "1400-1500")
        now = int(time.time())
        self.say(rid, "甲", now - 300, "甲：今天天气真好我们出去走走吧")
        with get_conn() as c:
            roomlog._drop_near_duplicate(c, rid, "甲", "甲：今天天气真好，我们出去走一走吧", now)
        self.assertEqual(len(roomlog.lines(rid)), 0)                                    # 10 分钟内、够像：前一条被替换
        self.say(rid, "甲", now - 700, "甲：今天天气真好我们出去走走吧")
        with get_conn() as c:
            roomlog._drop_near_duplicate(c, rid, "甲", "甲：今天天气真好，我们出去走一走吧", now)
        self.assertEqual(len(roomlog.lines(rid)), 1)                                    # 超过 10 分钟：不算重发


class Arc(Base):
    def test_turn_and_independent(self):
        L = lambda r, t: {"role_id": r, "ts": t}                                        # noqa: E731
        g, since = arclen.walk([L(1, 0), L(1, 3600), L(2, 7200)], [1, 2], set())
        self.assertEqual((g, since), ([(2, 2.0)], {1: 7200}))                           # 两人房：从对方第一句起算
        g, _s = arclen.walk([L(1, 0), L(1, 3600), L(2, 7200)], [1, 2, 3], set())
        self.assertEqual(g, [(2, 1.0)])                                                 # 多人房：从别人最近一句起算
        g, _s = arclen.walk([L(1, 0), L(2, 3600)], [1, 2], set(), window=(0, 1800))
        self.assertEqual(g, [])                                                         # 回复落在正式档期外：不计

    def test_only_rp_and_main_window(self):
        ok, _i, rid = self.room("约会", "甲乙", "1400-1500")
        now = int(time.time())
        self.say(rid, "甲", now - 7200)
        self.say(rid, "乙", now - 5400, "在吗", rp=0)                                    # 闲聊不算回复
        st = arclen.stats(now)
        self.assertEqual(st["people"], [])
        self.assertEqual(st["waits"][0]["who"], "乙")
        self.assertAlmostEqual(st["waits"][0]["hours"], 2.0, places=1)
        self.say(rid, "乙", now - 3600)
        st = arclen.stats(now)
        self.assertEqual([(p["name"], round(p["avg"], 1)) for p in st["people"]], [("乙", 1.0)])
        self.assertIn("待你", arclen.report(self.id["甲"], now))
        self.assertIn("待甲", arclen.report(self.id["乙"], now))
        with get_conn() as c:                                                           # 档期早就结束了（现在是补戏期）：只留场次，不计弧长
            c.execute("UPDATE seasons SET end_date='2000-01-02'")
        self.assertEqual(arclen.stats(now)["people"], [])
        self.assertIn("还没有计时的回复", arclen.report(self.id["乙"], now))


class Dates(Base):
    def test_min_minutes(self):
        self.assertFalse(self.room("电话", "甲乙", "1400-1420")[0])
        self.assertTrue(self.room("电话", "甲乙", "1400-1429")[0])
        ok, info, _ = self.room("约会", "甲丙", "1600-1630")
        self.assertFalse(ok); self.assertIn("至少要 59 分钟", info)
        set_config("min_minutes_约会", "0")
        self.assertTrue(self.room("约会", "甲丙", "1600-1630")[0])
        ok, _i, rid = rooms.create("官约", self.id["丙"], [self.id["丁"]], "1000-1010", "大厅", official=True)
        self.assertTrue(ok)                                                             # 官约不受限

    def test_blocked_hours(self):
        rooms.save_blocked_hours({1: [14, 15]})
        ok, info, _ = self.room("约会", "甲乙", "1300-1500")                             # 开始在 13 点，但盖到了 14 点
        self.assertFalse(ok); self.assertIn("14点", info)
        self.assertTrue(self.room("约会", "甲乙", "1200-1400")[0])                       # 正好在 14 点前结束
        self.assertEqual(rooms.blocked_hit(2, "14:00", "15:00"), "")                    # 别的天不受影响
        self.assertFalse(wishes.post(self.id["丙"], "1430-1530", "花园", "散步")[0])
        ok, _i, _r = rooms.create("官约", self.id["丙"], [self.id["丁"]], "1400-1500", "大厅", official=True)
        self.assertTrue(ok)

    def test_missed_notice(self):
        self.assertTrue(self.room("约会", "乙丙", "1400-1500")[0])
        ok, _info, _ = self.room("约会", "甲乙", "1430-1530")
        self.assertFalse(ok)
        m = rooms.pop_missed(self.id["甲"])
        self.assertEqual((m["role_id"], m["kind"], m["start_hm"]), (self.id["乙"], "约会", "14:30"))
        self.assertIsNone(rooms.pop_missed(self.id["甲"]))
        self.assertFalse(self.room("约会", "乙甲", "1430-1530")[0])                      # 是发起人自己撞了：不给别人留言
        self.assertIsNone(rooms.pop_missed(self.id["乙"]))


class Gifts(Base):
    def _send(self, sent):
        async def send(bot, grp, text, **kw):
            sent.append((grp, text)); return "id"
        return send

    def test_lost_and_mis(self):
        set_config("ext:chaos_letter", "1"); set_config("gift_cooldown_sec", "0")
        set_config("chaos_gift_lost", "100")
        sent = []
        ok, text = asyncio.run(sendmsg.send(self.r["甲"], self.r["乙"], "gift", "玫瑰", "给你", "", self._send(sent)))
        self.assertTrue(ok); self.assertIn("已送达「乙」", text)                         # 送的人看不出来
        self.assertEqual(sent, [])
        with get_conn() as c:
            m = c.execute("SELECT status, error FROM messages").fetchone()
        self.assertEqual((m["status"], m["error"][:4]), ("failed", "礼物丢失"))
        set_config("chaos_gift_lost", "0"); set_config("chaos_gift_mis", "100")
        ok, text = asyncio.run(sendmsg.send(self.r["甲"], self.r["乙"], "gift", "百合", "", "", self._send(sent)))
        self.assertTrue(ok)
        self.assertTrue(sent and sent[0][0] in ("G2", "G3"))                            # 落到了丙或丁手里
        set_config("ext:chaos_letter", "0"); sent.clear()
        asyncio.run(sendmsg.send(self.r["甲"], self.r["乙"], "gift", "兰花", "", "", self._send(sent)))
        self.assertEqual(sent[0][0], "G1")                                              # 没开混乱短信：照常送到


class Milestone(Base):
    def test_bump_and_flush(self):
        self.assertEqual(milestone.frequency(), 2)                                      # 默认每 2 次发一句
        set_config("announce_frequency", "0")
        self.assertEqual(milestone.bump("电话"), "")                                    # 频率 0：只计数不发
        set_config("announce_frequency", "3")
        self.assertEqual(milestone.bump("电话"), "")
        self.assertEqual(milestone.bump("电话"), "☎️ 【第3次电话记录】")
        self.assertEqual(milestone.bump("心动信", 7), "💌 【第6次心动信派送记录】")       # 一次跨过好几个：只报最后一个整数倍
        self.assertEqual(milestone.bump("踩点"), "")
        sent = []

        async def send(bot, grp, text):
            sent.append((grp, text))
        self.assertEqual(asyncio.run(milestone.flush(send)), 0)                         # 没设公告群：不发，也不留着
        milestone.bump("电话", 3)
        set_config("announce_group", "B|ANN")
        self.assertEqual(asyncio.run(milestone.flush(send)), 1)
        self.assertEqual(sent, [("ANN", "☎️ 【第6次电话记录】")])
        self.assertEqual(asyncio.run(milestone.flush(send)), 0)

    def test_per_kind_frequency(self):
        set_config("announce_frequency", "2")
        set_config("announce_frequency_sms", "100")                                    # 短信单独：每 100 次
        set_config("announce_frequency_gift", "0")                                     # 礼物单独：不发
        set_config("announce_frequency_phone", "")                                     # 留空：跟总频率
        self.assertEqual(milestone.frequency("短信"), 100)
        self.assertEqual(milestone.frequency("礼物"), 0)
        self.assertEqual(milestone.frequency("电话"), 2)
        self.assertEqual(milestone.frequency(), 2)
        self.assertEqual(milestone.bump("短信", 2), "")
        self.assertEqual(milestone.bump("礼物", 5), "")
        self.assertEqual(milestone.bump("电话", 2), "☎️ 【第2次电话记录】")
        self.assertEqual(milestone.bump("短信", 98), "📮 【第100次短信记录】")

    def test_hooks(self):
        set_config("announce_frequency", "1")
        self.assertTrue(self.room("约会", "甲乙", "1400-1500")[0])
        self.assertFalse(self.room("约会", "甲乙", "1430-1530")[0])                      # 没开成的不算
        self.assertEqual(milestone.take(), ["💫 【第1次约会记录】"])


class Universal(Base):
    def test_library(self):
        rpg.save_attr_def("体力", 0, 100, 50)
        self.assertTrue(rpg.save_item("", "金苹果", "item", "好吃", "体力+10", 5, False)[0])
        self.assertTrue(rpg.save_item("", "银币", "currency")[0])
        cand = {x["name"]: x for x in unilib.candidates()}
        self.assertIn("金苹果", cand)
        self.assertFalse(any(x["code"].startswith("SPEC_") for x in cand.values()))     # 系统自带的特殊道具不入库
        added, skipped, _ = unilib.add_from_bot([cand["金苹果"]["code"], cand["银币"]["code"]])
        self.assertEqual((added, skipped), (2, 0))
        self.assertEqual(unilib.add_from_bot([cand["金苹果"]["code"]])[:2], (0, 1))      # 同名跳过
        lib = {x["name"]: x for x in unilib.items()}
        self.assertEqual((lib["金苹果"]["eff_text"], lib["金苹果"]["max_uses"], lib["金苹果"]["can_give"]), ("体力+10", 5, False))
        with get_conn() as c:                                                           # 换了个季度 / 机器人：物品库清空，属性定义也没了
            c.execute("DELETE FROM rpg_items"); c.execute("DELETE FROM rpg_attr_defs")
        added, notes = unilib.import_to_bot([x["id"] for x in lib.values()])
        self.assertEqual(added, 1)                                                      # 银币进来了；金苹果缺「体力」属性，说明原因
        self.assertTrue(notes and "体力" in notes[0])
        rpg.save_attr_def("体力", 0, 100, 50)
        self.assertEqual(unilib.import_to_bot([lib["金苹果"]["id"], lib["银币"]["id"]])[0], 1)
        got = rpg.find_item("金苹果")
        self.assertEqual((got["max_uses"], got["can_give"], got["description"]), (5, 0, "好吃"))
        unilib.delete(lib["银币"]["id"])
        self.assertEqual([x["name"] for x in unilib.items()], ["金苹果"])


class NpcSharedGroup(Base):
    def _code(self, name):
        import re
        ok, msg = npc.create(name)
        self.assertTrue(ok)
        return re.search(r"认群 (\d{6})", msg).group(1)

    def test_npcs_share_players_do_not(self):
        a, b, c3 = self._code("猫"), self._code("狗"), self._code("鸟")
        self.assertTrue(roles.consume_bind_code(a, "B", "NPCG", "MA")[0])
        ok, msg = roles.consume_bind_code(b, "B", "NPCG", "MB")                      # 第二个 NPC 认同一个群：可以
        self.assertTrue(ok, msg)
        ok, msg = roles.consume_bind_code(c3, "B", "NPCG", "MA")                     # 同一个人在同一个群里扮第二个 NPC：分不清谁在说话，不行
        self.assertFalse(ok); self.assertIn("已经扮着「猫」", msg)
        self.assertTrue(roles.consume_bind_code(c3, "B", "NPCG2", "MA")[0])          # 码没被用掉，换个群还能认
        self.assertEqual(roles.find_role_for_sender("B", "NPCG", "MB")["name"], "狗")
        self.assertEqual(roles.find_role_for_sender("B", "NPCG", "MA")["name"], "猫")
        ok, msg = roles.create_role("新人", "B", "NPCG", "MZ")                        # 玩家不能在 NPC 的群里建角色
        self.assertFalse(ok)
        d = self._code("鱼")                                                         # NPC 也不能挤进玩家的个人群
        ok, msg = roles.consume_bind_code(d, "B", "G0", "MD")
        self.assertFalse(ok); self.assertIn("甲", msg)
        ok, msg = npc.set_flag("狗", False)                                          # 还共用着群：不能改回普通角色
        self.assertFalse(ok); self.assertIn("共用一个群", msg)
        self.assertTrue(npc.set_flag("鸟", False)[0])                                # 自己一个群的可以

    def test_messages_reach_each_npc(self):
        a, b = self._code("猫"), self._code("狗")
        roles.consume_bind_code(a, "B", "NPCG", "MA"); roles.consume_bind_code(b, "B", "NPCG", "MB")
        sent = []

        async def send(bot, grp, text, **kw):
            sent.append((grp, text)); return "id"
        dog = roles.get_role_by_name("狗")
        set_config("sms_cooldown_sec", "0")
        ok, _t = asyncio.run(sendmsg.send(self.r["甲"], dog, "sms", "", "你好", "", send))
        self.assertTrue(ok)
        self.assertEqual(sent[0][0], "NPCG")
        self.assertIn("MB", sent[0][1])                                             # @ 的是扮「狗」的那个人，不是同群的「猫」


class Pages(Base):
    def test_admin_pages(self):
        import re

        from changri.admin import create_app
        from changri.core import webusers
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)        # noqa: E731
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        # 禁约时段：全体设置里按天勾钟点
        r = c.post("/god/all/hours", data={"_csrf": tok("/god?role=all"), "day": "2", "h": ["14", "15"]}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("D2 的禁约时段已保存", html)
        self.assertEqual(rooms.blocked_hours(), {2: [14, 15]})
        self.assertIn("14点", html)
        self.assertEqual(c.get("/god?role=all&day=2").status_code, 200)                # 修改这一天：钟点格子能打开
        c.post("/god/all/hours", data={"_csrf": tok("/god?role=all"), "day": "2"})     # 全不勾 = 这一天不限了
        self.assertEqual(rooms.blocked_hours(), {})
        # 通用物品库：存进去、换空物品库、再导回来
        rpg.save_item("", "银币", "currency")
        code = rpg.find_item("银币")["code"]
        html = c.get("/rpg").get_data(as_text=True)
        self.assertIn("通用物品库", html)
        r = c.post("/rpg/universal/add", data={"_csrf": tok("/rpg"), "code": [code]}, follow_redirects=True)
        self.assertIn("已存入通用库 1 件", r.get_data(as_text=True))
        with get_conn() as cn:
            cn.execute("DELETE FROM rpg_items")
        uid = unilib.items()[0]["id"]
        r = c.post("/rpg/universal/import", data={"_csrf": tok("/rpg"), "id": [str(uid)]}, follow_redirects=True)
        self.assertIn("已导入 1 件", r.get_data(as_text=True))
        self.assertTrue(rpg.find_item("银币"))
        c.post(f"/rpg/universal/{uid}/delete", data={"_csrf": tok("/rpg")})
        self.assertEqual(unilib.items(), [])
        # 设置页：新加的几项都在
        html = c.get("/settings").get_data(as_text=True) if c.get("/settings").status_code == 200 else c.get("/extensions").get_data(as_text=True)
        for key in ("announce_frequency", "min_minutes_约会"):
            self.assertIn(key, html)


if __name__ == "__main__":
    unittest.main()
