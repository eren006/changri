import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = tempfile.mkdtemp()
os.environ["CHANGRI_DB_PATH"] = str(Path(_tmp) / "r.db")

from changri.core import admins, blocks, lovemail, messaging, relations, roles  # noqa: E402
from changri.core.db import get_config, get_conn, init_db, set_config  # noqa: E402
from changri.core.schedule import run_lovemail_tick  # noqa: E402


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("lovemails", "rel_details", "relations", "blocks", "messages", "bind_codes", "role_groups", "roles",
                      "admins", "admin_codes", "config"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B1", "G1", "M1"); roles.create_role("乙", "B1", "G2", "M2"); roles.create_role("丙", "B1", "G3", "M3")
        self.a, self.b, self.c = (roles.get_role_by_name(n)["id"] for n in "甲乙丙")
        self.sent = []

    def sender(self, fail=()):
        async def send(bot, group, text):
            if group in fail:
                raise RuntimeError("blocked")
            self.sent.append((group, text))
        return send


class BlockTests(Base):
    def test_block_rules(self):
        blocks.block(self.a, self.b, silent=False)
        self.assertEqual(blocks.get_block(self.a, self.b)["silent"], 0)
        self.assertIsNone(blocks.get_block(self.b, self.a))                       # 单向
        ok, info = blocks.unblock(self.a, self.b)
        self.assertFalse(ok); self.assertTrue(info.startswith("wait:"))           # 不满 2 小时不能解除
        since = blocks.get_block(self.a, self.b)["since_ts"]
        blocks.block(self.a, self.b, silent=True)                                  # 换模式不重新计时
        self.assertEqual(blocks.get_block(self.a, self.b)["since_ts"], since)
        self.assertEqual(blocks.get_block(self.a, self.b)["silent"], 1)
        with get_conn() as c:
            c.execute("UPDATE blocks SET since_ts=since_ts-7300")
        self.assertTrue(blocks.unblock(self.a, self.b)[0])
        self.assertEqual(blocks.unblock(self.a, self.b), (False, "not_blocked"))

    def test_mark_blocked(self):
        mid = messaging.queue_message("sms", self.b, self.a, "", "x")
        messaging.mark_blocked(mid, True)
        with get_conn() as c:
            r = c.execute("SELECT status, error FROM messages").fetchone()
        self.assertEqual(r["status"], "failed"); self.assertIn("静默", r["error"])


class LovemailTests(Base):
    def test_parse(self):
        self.assertEqual(lovemail.parse_post("乙 你好呀"), ("乙", "你好呀", ""))             # 简单格式署名留空，插件补真名
        self.assertEqual(lovemail.parse_post("【发送对象】乙\n【内容】想你\n多行\n【署名】小风"), ("乙", "想你\n多行", "小风"))
        self.assertEqual(lovemail.parse_post("乙")[0], "")

    def test_limits_and_revoke(self):
        set_config("lovemail_default_limit", "2")
        self.assertEqual(lovemail.add_mail(self.a, self.b, "匿名", "一")[2], 1)
        self.assertEqual(lovemail.add_mail(self.a, self.b, "匿名", "二")[2], 0)
        self.assertFalse(lovemail.add_mail(self.a, self.b, "匿名", "三")[0])       # 超上限
        self.assertTrue(lovemail.revoke(self.a, 1)[0])                              # 撤回退回次数
        self.assertTrue(lovemail.add_mail(self.a, self.b, "匿名", "四")[0])
        self.assertFalse(lovemail.revoke(self.a, 9)[0])
        self.assertFalse(lovemail.add_mail(self.a, self.b, "x" * 21, "内容")[0])    # 署名超 20 字
        set_config("lovemail_default_limit", "0")
        self.assertIn("不收投稿", lovemail.add_mail(self.a, self.b, "匿名", "内容")[1])
        set_config("lovemail_default_limit", "3"); set_config("lovemail_enabled", "0")
        self.assertIn("没有开放", lovemail.add_mail(self.a, self.b, "匿名", "内容")[1])

    def test_deliver_all(self):
        lovemail.add_mail(self.a, self.b, "小风", "给乙1"); lovemail.add_mail(self.c, self.b, "匿名", "给乙2")
        lovemail.add_mail(self.a, self.c, "匿名", "给丙")
        roles.add_slot(self.c, "123456")
        res = run(lovemail.deliver_all(self.sender(fail={"G3"}), ("B1", "ANN")))          # 丙的个人群发不出去
        self.assertEqual((res["success"], res["fail"]), (2, 1))
        pkg = [t for g, t in self.sent if g == "G2"][0]
        self.assertIn("包含 2 封", pkg); self.assertIn("给乙1", pkg); self.assertIn("署名：小风", pkg)
        self.assertTrue(any(g == "ANN" and "共派送 2 封信件，最高收信 2 封" in t for g, t in self.sent))
        self.assertEqual([m["to_name"] for m in lovemail.pool_all()], ["丙"])       # 失败的留在池里
        self.assertEqual(lovemail.day_count(self.a), 2)                             # 派送后仍计入当天次数
        res = run(lovemail.deliver_all(self.sender(), ("B1", "ANN")))                       # 修好之后下次能派出去
        self.assertEqual(res["success"], 1)

    def test_expose(self):
        set_config("lovemail_expose", "1"); set_config("lovemail_expose_chance", "100"); set_config("drop_hide_receiver", "1")
        lovemail.add_mail(self.a, self.b, "匿名", "秘密")
        run(lovemail.deliver_all(self.sender(), ("B1", "ANN")))
        pub = [t for g, t in self.sent if g == "ANN" and "寄给" in t][0]
        self.assertIn("某人", pub); self.assertIn("秘密", pub)

    def test_expose_hide_switch_split(self):
        """心动信曝光有自己的「隐藏收信人」开关；没单独设过时照老开关 drop_hide_receiver。"""
        set_config("lovemail_expose", "1"); set_config("lovemail_expose_chance", "100")
        set_config("drop_hide_receiver", "1"); set_config("lovemail_hide_receiver", "0")
        lovemail.add_mail(self.a, self.b, "匿名", "秘密")
        run(lovemail.deliver_all(self.sender(), ("B1", "ANN")))
        pub = [t for g, t in self.sent if g == "ANN" and "寄给" in t][0]
        self.assertIn("乙", pub); self.assertNotIn("某人", pub)
        self.assertTrue(messaging.hide_receiver("gift"))                           # 礼物没单独设：跟老开关

    def test_self_and_block(self):
        self.assertIn("自己", lovemail.add_mail(self.a, self.a, "匿名", "x")[1])
        blocks.block(self.b, self.a, silent=False)                                # 乙明着拉黑甲：直接拒
        ok, msg, _ = lovemail.add_mail(self.a, self.b, "匿名", "x")
        self.assertFalse(ok); self.assertIn("拒绝", msg)
        blocks.block(self.b, self.a, silent=True)                                 # 静默：看着投成功了，信不送
        ok, msg, _ = lovemail.add_mail(self.a, self.b, "匿名", "被吞")
        self.assertTrue(ok)
        self.assertEqual(len(lovemail.my_pool(self.a)), 1)                         # 寄信人自己看得到、能撤回
        self.assertEqual(lovemail.pool_all(), [])                                  # 不进派送
        run(lovemail.deliver_all(self.sender(), ("B1", "ANN")))
        self.assertFalse(any("被吞" in t for _, t in self.sent))
        self.assertEqual(lovemail.my_pool(self.a), [])
        self.assertEqual([m["content"] for m in lovemail.history(self.a)[1]], ["被吞"])   # 寄信人那边算已送出
        self.assertEqual(lovemail.history(self.b)[0], [])

    def test_chunk(self):
        parts = lovemail.chunk_text("\n".join(["字" * 300] * 5), 700)
        self.assertTrue(all(len(p) <= 700 for p in parts)); self.assertGreater(len(parts), 1)


class TickTests(Base):
    def at(self, hh, mm):
        return time.struct_time((2026, 11, 13, hh, mm, 0, 4, 317, -1))

    def _season(self, status="running", day="1"):
        with get_conn() as c:
            c.execute("DELETE FROM seasons")
            if status:
                c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01',?)", (status,))
        set_config("game_day", day)

    def tearDown(self):
        self._season(status=None, day="")

    def test_schedule(self):
        self._season()                                                             # 季度进行中（D1）
        set_config("announce_group", "B1|ANN")
        lovemail.add_mail(self.a, self.b, "匿名", "信")
        send = self.sender()
        self.assertEqual(run(run_lovemail_tick(send, self.at(21, 50))), "notice10")
        self.assertEqual(run(run_lovemail_tick(send, self.at(21, 50))), "idle")     # 同一天不重复提醒
        self.assertTrue({"G1", "G2", "G3"} <= {g for g, _ in self.sent})
        self.assertEqual(run(run_lovemail_tick(send, self.at(21, 55))), "notice5")
        self.assertEqual(run(run_lovemail_tick(send, self.at(21, 59))), "idle")     # 没到点不派
        self.assertEqual(lovemail.pool_all()[0]["to_name"], "乙")
        self.assertEqual(run(run_lovemail_tick(send, self.at(22, 0))), "delivered")
        self.assertEqual(lovemail.pool_all(), [])
        self.assertEqual(run(run_lovemail_tick(send, self.at(22, 1))), "idle")      # 当天不重复派

    def test_no_reminders_before_season_starts(self):
        """没有季度、只预订了、还在筹备期：两条提醒都不发；信池里的信照常派送。"""
        set_config("announce_group", "B1|ANN")
        lovemail.add_mail(self.a, self.b, "匿名", "信")
        send = self.sender()
        for i, (status, day) in enumerate([(None, ""), ("booked", ""), ("running", "")]):   # 没季度 / 预订了 / 筹备期
            self._season(status, day)
            for k in ("lovemail_notice10_day", "lovemail_notice5_day"):
                set_config(k, "")
            self.assertEqual(run(run_lovemail_tick(send, self.at(21, 50))), "notice10-skip", status)
            self.assertEqual(run(run_lovemail_tick(send, self.at(21, 55))), "notice5-skip", status)
        self.assertEqual(self.sent, [])
        self.assertEqual(run(run_lovemail_tick(send, self.at(22, 0))), "delivered")       # 派送不受影响
        self.assertTrue(self.sent)

    def test_after_delivery_counts_next_day(self):
        """当天的派送跑过以后再投的，算第二天：占第二天的次数、留到第二天派送；撤回退的也是第二天的。"""
        set_config("lovemail_default_limit", "1")
        self.assertTrue(lovemail.add_mail(self.a, self.b, "匿名", "今天的")[0])
        self.assertFalse(lovemail.add_mail(self.a, self.b, "匿名", "超了")[0])
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(22, 0))), "delivered")
        ok, msg, left = lovemail.add_mail(self.a, self.b, "匿名", "明天的")
        self.assertTrue(ok); self.assertIn("算明天", msg); self.assertEqual(left, 0)
        nxt = lovemail.next_key(lovemail.today())
        self.assertEqual(lovemail.my_pool(self.a)[0]["day"], nxt)
        self.assertEqual((lovemail.day_count(self.a), lovemail.day_count(self.a, nxt)), (1, 1))
        self.assertFalse(lovemail.add_mail(self.a, self.b, "匿名", "又一封")[0])           # 明天的也满了
        self.assertIn(lovemail.day_label(nxt), lovemail.revoke(self.a, 1)[1])
        self.assertEqual(lovemail.next_key("S3D2.0"), "S3D3.0"); self.assertEqual(lovemail.day_label("S3D2.0"), "D2")
        self.assertEqual(lovemail.next_key("2026-12-31"), "2027-01-01")

    def test_notice10_skipped_when_closed(self):
        """今天不收信（上限 0）时，10 分钟预告不发「抓紧投递」。"""
        set_config("lovemail_default_limit", "0")
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(21, 50))), "notice10-skip")
        self.assertEqual(self.sent, [])

    def test_notices_can_be_turned_off(self):
        """后台把两条派送前提醒分别关掉：到点不发，派送照常。"""
        set_config("announce_group", "B1|ANN")
        set_config("lovemail_notice10", "0"); set_config("lovemail_notice5", "0")
        lovemail.add_mail(self.a, self.b, "匿名", "信")
        send = self.sender()
        self.assertEqual(run(run_lovemail_tick(send, self.at(21, 50))), "notice10-skip")
        self.assertEqual(run(run_lovemail_tick(send, self.at(21, 55))), "notice5-skip")
        self.assertEqual(self.sent, [])
        self.assertEqual(run(run_lovemail_tick(send, self.at(22, 0))), "delivered")

    def test_season_end_turns_off_reminders(self):
        """手动「结束季度」也会关心动信：之后不再发派送前提醒。"""
        from changri.core import season
        season.book("测试季", "2026-11-10", "2026-11-30", "", "")
        self.assertTrue(season.start("", clear=True, now="2026-11-13")[0])          # 测试里已经有角色，开季要先清
        self.assertEqual(get_config("lovemail_enabled"), "1")
        self.assertTrue(season.end()[0])
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(21, 50))), "off")
        self.assertEqual(self.sent, [])

    def test_manual_deliver_while_closed(self):
        """档期结束后心动信自动关了：后台「立即派送」照样把信池剩下的信送出去，标记清掉；没点按钮就不派。"""
        lovemail.add_mail(self.a, self.b, "匿名", "信")
        set_config("lovemail_enabled", "0")
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(22, 0))), "off")      # 关着时不按点自动派
        self.assertEqual(len(lovemail.pool_all()), 1)
        set_config("lovemail_deliver_now", "1")
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(3, 0))), "delivered")
        self.assertEqual(lovemail.pool_all(), [])
        self.assertEqual(get_config("lovemail_deliver_now"), "0")

    def test_late_catchup_and_manual(self):
        lovemail.add_mail(self.a, self.b, "匿名", "信")
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(22, 40))), "delivered")  # 重启晚了 40 分钟照样补派
        lovemail.add_mail(self.a, self.c, "匿名", "信2")
        set_config("lovemail_deliver_now", "1")
        self.assertEqual(run(run_lovemail_tick(self.sender(), self.at(9, 0))), "delivered")    # 后台「立即派送」
        self.assertEqual(get_config("lovemail_deliver_now"), "0")


class RelationTests(Base):
    def test_flow_and_limits(self):
        ok, hint = relations.add_detail(self.a, self.b, "乙", "初见")
        self.assertTrue(ok); self.assertIn("关系线已建立", hint)
        ok, hint = relations.add_detail(self.b, self.a, "甲", "回应")               # 对方在同一条线上回
        self.assertTrue(ok); self.assertNotIn("已建立", hint)
        self.assertEqual(len(relations.details(relations.get_rel(self.a, self.b)["id"])), 2)
        items = {i["other_name"]: i for i in relations.list_for(self.a)}
        self.assertTrue(items["乙"]["mine"]); self.assertTrue(items["乙"]["pending"])  # 最后一条是乙发的，轮到甲回
        self.assertFalse(relations.add_detail(self.a, self.a, "甲", "x")[0])
        self.assertTrue(relations.confirm(self.a, self.b))
        self.assertFalse({i["other_name"]: i for i in relations.list_for(self.a)}["乙"]["pending"])
        self.assertFalse(relations.confirm(self.a, self.c))

    def test_caps(self):
        set_config("max_detail_chars", "5")
        self.assertFalse(relations.add_detail(self.a, self.b, "乙", "123456")[0])
        set_config("max_detail_chars", "500"); set_config("max_detail_count", "2")
        relations.add_detail(self.a, self.b, "乙", "1"); relations.add_detail(self.a, self.b, "乙", "2")
        self.assertIn("已经写满", relations.add_detail(self.a, self.b, "乙", "3")[1])
        set_config("max_detail_count", "20"); set_config("max_rel_total_chars", "4")
        self.assertIn("超过总字数", relations.add_detail(self.a, self.b, "乙", "12345")[1])
        set_config("max_rel_total_chars", "3000"); set_config("max_relationships_per_user", "1")
        self.assertIn("你发起的关系线", relations.add_detail(self.a, self.c, "丙", "x")[1])
        set_config("max_relationships_per_user", "20"); set_config("max_relationships_total", "1")
        self.assertIn("已经满了", relations.add_detail(self.c, self.b, "乙", "x")[1])  # 乙已有一条，对方满了
        set_config("rel_enabled", "0")
        self.assertFalse(relations.add_detail(self.c, self.b, "乙", "x")[0])

    def test_withdraw(self):
        relations.add_detail(self.a, self.b, "乙", "写错了"); relations.add_detail(self.b, self.a, "甲", "好")
        self.assertIsNone(relations.withdraw(self.a, self.b, "不存在"))
        self.assertIsNone(relations.withdraw(self.a, self.b, "好"))               # 只能撤回自己发的
        self.assertEqual(relations.withdraw(self.a, self.b, "写错了"), "写错了")
        self.assertEqual(relations.withdraw(self.a, self.c, "x"), "")              # 没有关系线
        self.assertEqual(relations.stats()[0]["n"], 1)


class AdminTests(Base):
    def test_claim(self):
        code = admins.make_code("我")
        self.assertFalse(admins.claim("000000", "B1", "MX")[0])
        self.assertTrue(admins.claim(code, "B1", "MX")[0])
        self.assertTrue(admins.is_admin("B1", "MX")); self.assertFalse(admins.is_admin("B1", "MY"))
        self.assertFalse(admins.claim(code, "B1", "MY")[0])                              # 码只能用一次
        admins.remove("B1", "MX"); self.assertFalse(admins.is_admin("B1", "MX"))


if __name__ == "__main__":
    unittest.main()
