import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "longterm.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

from changri.core import longterm as lt, roomlog  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402

FWD = """--- 第1条 ---
[消息内容] 今天天气真好啊
[发送者] 阿青
--- 第2条 ---
[消息内容] 是啊[图片]
[发送者] 小白QQ
--- 第3条 ---
[消息内容] 一起出去走走吧
[发送者] 阿青
"""


class LongtermCore(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM lt_groups")
            c.execute("DELETE FROM config")
            c.execute("DELETE FROM trpg_identities")                      # 认人留下的关联不能漏到别的测试
            c.execute("DELETE FROM trpg_players")
        self.gid, err = lt.create_group("春日长期群")
        self.assertTrue(self.gid, err)

    def test_group_names_unique_and_binding(self):
        self.assertIsNone(lt.create_group("春日长期群")[0])
        g2, _ = lt.create_group("第二个")
        lt.bind("QG1", self.gid)
        self.assertEqual(lt.bound_group("QG1")["id"], self.gid)
        lt.bind("QG1", g2)
        self.assertEqual(lt.bound_group("QG1")["name"], "第二个")
        lt.delete_group(g2)
        self.assertIsNone(lt.bound_group("QG1"))

    def test_register_with_fields_and_rename(self):
        lt.update_group(self.gid, reg_fields="角色\nCP")
        name, info = lt.parse_register("阿青\n角色：剑客\nCP：小白\n别的：不认", ["角色", "CP"])
        self.assertEqual((name, info), ("阿青", {"角色": "剑客", "CP": "小白"}))
        m, err = lt.register(self.gid, "OA", name, info)
        self.assertEqual(m["info"]["角色"], "剑客", err)
        self.assertIsNone(lt.register(self.gid, "OB", "阿青")[0])           # 别人占了
        m2, _ = lt.register(self.gid, "OA", "阿青青")                          # 本人再发 = 改名
        self.assertEqual(m2["id"], m["id"])
        self.assertEqual(m2["info"]["CP"], "小白")

    def test_admin_added_member_claimed_by_register(self):
        pre, _ = lt.add_member(self.gid, "预留")
        lt.adjust_item(pre["id"], "钥匙", 1)
        m, _ = lt.register(self.gid, "OZ", "预留")
        self.assertEqual(m["id"], pre["id"])
        self.assertEqual(lt.items(m["id"]), [("钥匙", 1)])

    def test_tally_converts_dedups_and_records(self):
        lt.update_group(self.gid, chars_per_step=10, coins_per_step=3)
        a, _ = lt.register(self.gid, "OA", "阿青")
        items = roomlog.parse_quoted_forward(FWD)
        self.assertEqual(len(items), 3)
        res = lt.tally(self.gid, items, submitter=a, qq_group="QG")
        self.assertEqual(res["credited"], [("阿青", 7 + 7, 3)])              # 14 字 → 1 档 → 3 币，零头 4
        self.assertEqual(res["unclaimed"], [("小白QQ", 2)])                  # [图片] 不算字
        m = lt.get_member(a["id"])
        self.assertEqual((m["chars"], m["chars_left"], m["coins"]), (14, 4, 3))
        again = lt.tally(self.gid, items, submitter=a)
        self.assertEqual((again["total"], again["skipped"]), (0, 3))
        s, lines = lt.submission(res["id"])                                  # 留底能追溯
        self.assertEqual((s["submitter_name"], s["total_chars"], s["n_counted"]), ("阿青", 16, 3))
        self.assertEqual([l["member_name"] for l in lines], ["阿青", None, "阿青"])
        _, lines2 = lt.submission(again["id"])
        self.assertTrue(all(l["counted"] == 0 for l in lines2))
        b, _ = lt.register(self.gid, "OB", "小白")                           # 用别的名字注册：后台认领
        self.assertEqual(lt.claim(self.gid, "小白QQ", b["id"]), 2)
        self.assertIn("小白QQ", lt.get_member(b["id"])["alias_list"])
        _, lines = lt.submission(res["id"])
        self.assertEqual(lines[1]["member_name"], "小白")
        lt.tally(self.gid, [{"nickname": "小白QQ", "text": "新的一句话"}])     # 以后别名自动认
        self.assertEqual(lt.get_member(b["id"])["chars"], 7)

    def test_register_with_unclaimed_name_takes_chars(self):
        lt.tally(self.gid, [{"nickname": "路人", "text": "一二三四五"}])
        m, _ = lt.register(self.gid, "OC", "路人")
        self.assertEqual(lt.get_member(m["id"])["chars"], 5)
        self.assertEqual(lt.list_unclaimed(self.gid), [])

    def test_attrs_defaults_bounds_and_aliases(self):
        lt.set_attr_def(self.gid, "力量", 50, 0, 100)
        m, _ = lt.register(self.gid, "OA", "阿青")
        self.assertEqual(lt.get_attr(m["id"], "力量"), 50)
        self.assertEqual(lt.get_attr(m["id"], "STR"), 50)                    # 跑团别名
        lt.set_attr_def(self.gid, "魅力", 30)                                  # 后加的属性，已注册的人补默认值
        self.assertEqual(dict(lt.list_attrs(m["id"]))["魅力"], 30)
        self.assertTrue(lt.set_attrs(m["id"], [("力量", 300)])[0])
        self.assertEqual(lt.get_attr(m["id"], "力量"), 100)                  # 夹到最大值
        self.assertFalse(lt.set_attrs(m["id"], [("飞行", 3)], only_defined=True)[0])
        self.assertTrue(lt.set_attrs(m["id"], [("SAN", 40)])[0])            # 检定写回可以写没定义的
        self.assertEqual(lt.get_attr(m["id"], "理智"), 40)

    def test_give_items_and_currency(self):
        a, _ = lt.register(self.gid, "OA", "阿青")
        b, _ = lt.register(self.gid, "OB", "小白")
        lt.adjust_item(a["id"], "苹果", 3)
        lt.adjust_coins(a["id"], 20)
        self.assertEqual(lt.parse_give("小白 苹果 2"), ("小白", "苹果", 2))
        self.assertEqual(lt.parse_give("@小白 苹果x2"), ("小白", "苹果", 2))
        self.assertEqual(lt.parse_give("小白 红 苹果"), ("小白", "红 苹果", 1))
        self.assertTrue(lt.give(a["id"], b["id"], "苹果", 2)[0])
        self.assertFalse(lt.give(a["id"], b["id"], "苹果", 2)[0])
        self.assertTrue(lt.give(a["id"], b["id"], "金币", 5)[0])
        self.assertEqual(lt.get_member(b["id"])["coins"], 5)
        self.assertEqual(lt.items(b["id"]), [("苹果", 2)])
        self.assertFalse(lt.give(a["id"], a["id"], "苹果", 1)[0])

    def test_admin_adjust(self):
        lt.set_attr_def(self.gid, "力量", 50)
        m, _ = lt.register(self.gid, "OA", "阿青")
        ops = lt.parse_adjust("力量+5 金币=30 苹果+2 字数+100 金币-5")
        self.assertEqual(ops, [("力量", "+", 5), ("金币", "=", 30), ("苹果", "+", 2), ("字数", "+", 100), ("金币", "-", 5)])
        lt.apply_adjust(m["id"], ops)
        m = lt.get_member(m["id"])
        self.assertEqual(lt.get_attr(m["id"], "力量"), 55)
        self.assertEqual(lt.items(m["id"]), [("苹果", 2)])
        self.assertEqual(m["chars"], 100)
        self.assertEqual(m["coins"], 25 + 0)                                  # 100 字不到 1000 不换
        out = lt.apply_adjust(m["id"], [("金币", "-", 999)])
        self.assertIn("余额不够", out[0])
        self.assertEqual(lt.set_coins(m["id"], 7), (True, ""))
        self.assertEqual([r["kind"] for r in lt.ledger(m["id"])][:1], ["货币"])

    def test_shop_buy_use(self):
        lt.set_attr_def(self.gid, "HP", 10, 0, 20)
        m, _ = lt.register(self.gid, "OA", "阿青")
        self.assertFalse(lt.save_item_def(self.gid, "药水", effects="飞行+1")[0])          # 属性要先定义
        self.assertFalse(lt.save_item_def(self.gid, "金币")[0])                             # 不能跟货币同名
        self.assertTrue(lt.save_item_def(self.gid, "药水", "回点血", "HP+5", on_sale=True, price=3, stock=2)[0])
        self.assertTrue(lt.save_item_def(self.gid, "徽章", on_sale=False, price=1)[0])
        self.assertIn("药水", lt.shop_text(self.gid))
        self.assertNotIn("徽章", lt.shop_text(self.gid))
        self.assertIn("只有 0", lt.buy(m["id"], "药水", 1)[1])
        lt.adjust_coins(m["id"], 10)
        self.assertIn("没有「徽章」", lt.buy(m["id"], "徽章")[1])
        self.assertIn("只剩 2", lt.buy(m["id"], "药水", 3)[1])
        ok, msg = lt.buy(m["id"], "药水", 2)
        self.assertTrue(ok, msg)
        self.assertEqual(lt.get_member(m["id"])["coins"], 4)
        self.assertIn("卖完了", lt.buy(m["id"], "药水")[1])
        ok, msg = lt.use(m["id"], "药水")
        self.assertTrue(ok, msg)
        self.assertEqual(lt.get_attr(m["id"], "HP"), 15)
        lt.use(m["id"], "药水")
        self.assertEqual(lt.get_attr(m["id"], "HP"), 20)                                    # 上限夹住
        self.assertIn("没有「药水」", lt.use(m["id"], "药水")[1])
        lt.adjust_item(m["id"], "徽章", 1)
        self.assertIn("不能使用", lt.use(m["id"], "徽章")[1])
        lt.save_item_def(self.gid, "奖章", old_name="徽章")                                   # 改名：背包里的跟着改
        self.assertEqual(lt.items(m["id"]), [("奖章", 1)])

    def test_rewards_and_parts_parsing(self):
        self.assertEqual(lt.parse_rewards("银两 10-30\n药水 1\nHP 3~1\n乱写")[0], [("银两", 10, 30), ("药水", 1, 1), ("HP", 1, 3)])
        self.assertEqual(lt.parse_rewards("乱写")[1], ["乱写"])
        self.assertFalse(lt.update_group(self.gid, checkin_rewards="乱写")[0])
        self.assertEqual(lt.parse_parts("碎片×3 金币*10、木头")[0], [("碎片", 3), ("金币", 10), ("木头", 1)])

    def test_checkin_random_in_range(self):
        lt.update_group(self.gid, checkin_on=True, checkin_rewards="金币 5-8")
        m, _ = lt.register(self.gid, "OA", "阿青")
        self.assertTrue(lt.checkin(m["id"])[0])
        self.assertTrue(5 <= lt.get_member(m["id"])["coins"] <= 8)
        self.assertFalse(lt.checkin(m["id"])[0])

    def test_review_reject_frees_dedup(self):
        m, _ = lt.register(self.gid, "OA", "阿青")
        items = [{"nickname": "阿青", "text": "一二三四五"}]
        r = lt.tally(self.gid, items, submitter=m, pending=True)
        self.assertTrue(r["pending"])
        self.assertEqual(lt.pending_chars(m["id"]), 5)
        self.assertEqual(lt.tally(self.gid, items, pending=True)["skipped"], 1)       # 等批准时也占着去重
        lt.reject(r["id"])
        self.assertEqual(lt.pending_chars(m["id"]), 0)
        r2 = lt.tally(self.gid, items, submitter=m, pending=True)
        self.assertEqual(r2["total"], 5)
        lt.approve(r2["id"])
        self.assertEqual(lt.get_member(m["id"])["chars"], 5)

    def test_paras_and_kpi(self):
        lt.update_group(self.gid, kpi_period="week", kpi_chars=10, kpi_paras=3)
        m, _ = lt.register(self.gid, "OA", "阿青")
        lt.tally(self.gid, [{"nickname": "阿青", "text": "一二三四五"}, {"nickname": "阿青", "text": "六七八"}, {"nickname": "路人", "text": "哈"}])
        m = lt.get_member(m["id"])
        self.assertEqual((m["chars"], m["paras"]), (8, 2))
        p = lt.kpi_progress(self.gid)[m["id"]]
        self.assertEqual((p["chars"], p["paras"], p["ok"]), (8, 2, False))
        self.assertIn("字数 8/10", lt.kpi_text(m["id"]))
        lt.apply_adjust(m["id"], [("字数", "+", 2), ("段数", "+", 1)])
        self.assertTrue(lt.kpi_progress(self.gid)[m["id"]]["ok"])
        self.assertIn("达标", lt.panel_text(m["id"]))
        b, _ = lt.register(self.gid, "OB", "路人")                              # 认领时段数一起转过去
        self.assertEqual(lt.get_member(b["id"])["paras"], 1)
        # 上一期的不算进这一期
        with get_conn() as c:
            c.execute("UPDATE lt_ledger SET ts=ts-8*86400 WHERE member_id=?", (m["id"],))
        self.assertEqual(lt.kpi_progress(self.gid)[m["id"]]["chars"], 0)
        lt.update_group(self.gid, kpi_period="all")
        self.assertEqual(lt.kpi_progress(self.gid)[m["id"]]["chars"], 10)
        lt.update_group(self.gid, kpi_chars=0, kpi_paras=0)
        self.assertFalse(lt.kpi_on(lt.get_group(self.gid)))

    def test_kpi_start(self):
        import time as _t
        wed = _t.mktime((2026, 11, 18, 15, 0, 0, 0, 0, -1))                      # 2026-11-18 是周三
        self.assertEqual(_t.strftime("%Y-%m-%d %H:%M", _t.localtime(lt.kpi_start({"kpi_period": "week"}, wed))), "2026-11-16 00:00")
        self.assertEqual(_t.strftime("%Y-%m-%d", _t.localtime(lt.kpi_start({"kpi_period": "month"}, wed))), "2026-11-01")
        self.assertEqual(lt.kpi_start({"kpi_period": "all"}, wed), 0)

    def test_notes_text_box(self):
        """后台「改条目」文本框：【栏目】或空行后不带冒号的一行是栏目名，下面一行一个字段；存了再读回来一样。"""
        notes, err = lt.parse_notes_text("【魔杖】\n木材：冬青\n杖芯: 凤凰羽毛\n\n宠物\n名字：小灰\n种类 猫头鹰\n\n空栏")
        self.assertEqual(err, "")
        self.assertEqual(notes, {"魔杖": {"木材": "冬青", "杖芯": "凤凰羽毛"}, "宠物": {"名字": "小灰", "种类": "猫头鹰"}, "空栏": {}})
        self.assertEqual(lt.parse_notes_text(lt.notes_to_text(notes))[0], notes)
        self.assertIsNone(lt.parse_notes_text("木材：冬青")[0])                    # 没有栏目名
        m, _ = lt.register(self.gid, "OA", "阿青")
        lt.replace_notes(m["id"], notes)
        self.assertEqual(list(lt.get_member(m["id"])["notes"]), ["魔杖", "宠物", "空栏"])   # 顺序不变
        self.assertIn("长度", "".join(lt.set_note(m["id"], "魔杖", [("长度" * 7, "x")])[1:]))  # 字段名太长
        self.assertEqual(lt.parse_note_fields("木材：冬青 杖芯：凤凰 羽毛"), [("木材", "冬青"), ("杖芯", "凤凰 羽毛")])

    def test_kpi_custom_days(self):
        import time as _t
        day = lambda y, mo, d, h=0: _t.mktime((y, mo, d, h, 0, 0, 0, 0, -1))
        fmt = lambda ts: _t.strftime("%Y-%m-%d", _t.localtime(ts))
        g = {"kpi_period": "days", "kpi_days": 10, "kpi_anchor": int(day(2026, 11, 25))}
        s, e = lt.kpi_range(g, day(2026, 11, 25, 9))                             # 第一天
        self.assertEqual((fmt(s), fmt(e)), ("2026-11-25", "2026-12-05"))
        self.assertEqual(lt.kpi_left_days(g, day(2026, 11, 25, 9)), 10)
        self.assertEqual(fmt(lt.kpi_start(g, day(2026, 12, 4, 23))), "2026-11-25")  # 最后一天还是第一期
        self.assertEqual(fmt(lt.kpi_start(g, day(2026, 12, 5, 0))), "2026-12-05")   # 到期自动进下一期
        self.assertEqual(fmt(lt.kpi_start(g, day(2027, 1, 20, 12))), "2027-01-14")  # 跨年照样按 10 天一格
        self.assertEqual(lt.kpi_label(g, day(2026, 12, 6)), "本期（12-05～12-14）")
        self.assertEqual(fmt(lt.kpi_start(g, day(2026, 11, 20))), "2026-11-25")     # 还没到开始日：按第一期算
        # 后台保存：天数要在 1–366，日期写错要报错；不填日期 = 今天
        self.assertIn("1 到 366", lt.update_group(self.gid, kpi_period="days", kpi_days=0)[1])
        self.assertIn("2026-11-25", lt.update_group(self.gid, kpi_period="days", kpi_days=14, kpi_anchor="11/25")[1])
        self.assertTrue(lt.update_group(self.gid, kpi_period="days", kpi_days=14, kpi_anchor="")[0])
        gg = lt.get_group(self.gid)
        self.assertEqual((gg["kpi_days"], fmt(gg["kpi_anchor"])), (14, _t.strftime("%Y-%m-%d")))
        self.assertEqual(lt.kpi_left_days(gg), 14)
        lt.update_group(self.gid, kpi_chars=5)
        m, _ = lt.register(self.gid, "OA", "阿青")
        self.assertIn("本期（", lt.kpi_text(m["id"]))
        self.assertIn("还剩 14 天", lt.kpi_text(m["id"]))

    def test_rename_keeps_old_name_as_alias(self):
        m, _ = lt.register(self.gid, "OA", "阿轻")                               # 名字打错了
        b, _ = lt.register(self.gid, "OB", "小白")
        lt.update_member(b["id"], aliases="白白")
        self.assertIn("已经有人用了", lt.rename(m["id"], "小白")[1])
        self.assertIn("已经有人用了", lt.rename(m["id"], "白白")[1])              # 别人的别名也不行
        self.assertIn("本来就叫", lt.rename(m["id"], "阿轻")[1])
        self.assertTrue(lt.rename(m["id"], "阿青")[0])
        m = lt.get_member(m["id"])
        self.assertEqual((m["name"], m["alias_list"]), ("阿青", ["阿轻"]))
        lt.tally(self.gid, [{"nickname": "阿轻", "text": "旧名字写的一段"}])        # 转发里还是旧名字：照样算
        self.assertEqual(lt.get_member(m["id"])["chars"], 7)
        self.assertTrue(lt.rename(m["id"], "阿轻")[0])                           # 改回自己的别名可以
        self.assertEqual(lt.get_member(m["id"])["alias_list"], ["阿青"])
        m2, _ = lt.register(self.gid, "OA", "青青")                             # 再发一次注册 = 改名，同样留别名
        self.assertEqual((m2["name"], set(m2["alias_list"])), ("青青", {"阿青", "阿轻"}))
        self.assertIsNone(lt.register(self.gid, "OC", "白白")[0])                # 新人不能注册成别人的别名
        self.assertTrue(lt.update_member(m["id"], name="青", aliases="阿青,阿轻,青青")[0])   # 后台改名
        self.assertIn("青青", lt.get_member(m["id"])["alias_list"])
        self.assertTrue(any("改名为" in e["text"] for e in lt.events(self.gid)))

    def test_linked_identity_across_groups(self):
        """官方机器人给的 openid 如果每个群不一样：用「认人」认到一起后，另一个群里也认得出。"""
        from changri.core import trpg
        m, _ = lt.register(self.gid, "OA", "阿青")
        self.assertIsNone(lt.member_of(self.gid, "OB"))
        _, err = lt.register(self.gid, "OB", "阿青")
        self.assertIn("认人", err)                                                # 没认过：提示去认人
        ok, msg = trpg.use_link_code("OB", "G2", trpg.make_link_code(trpg.player_for("OA", "G1")))
        self.assertTrue(ok, msg)
        self.assertEqual(lt.member_of(self.gid, "OB")["id"], m["id"])
        again, err = lt.register(self.gid, "OB", "阿青")                          # 在新群再注册 = 还是同一位
        self.assertEqual(again["id"], m["id"], err)
        self.assertEqual(len(lt.list_members(self.gid)), 1)

    def test_panel_text(self):
        lt.set_attr_def(self.gid, "力量", 50)
        m, _ = lt.register(self.gid, "OA", "阿青")
        lt.adjust_item(m["id"], "苹果", 1)
        t = lt.panel_text(m["id"])
        self.assertIn("力量 50", t)
        self.assertIn("苹果×1", t)
        self.assertIn("春日长期群", t)


if __name__ == "__main__":
    unittest.main()
