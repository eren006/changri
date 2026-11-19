import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "pd.db")

from changri.core import extensions as ec, paidan as pd  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402

D = "2026-11-18"


class PaidanTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("pd_charges", "pd_codes", "pd_orders", "pd_quota_extends", "pd_quotas", "pd_slots",
                      "pd_clients", "pd_businesses", "pd_pauses", "pd_bind_codes", "config"):
                c.execute(f"DELETE FROM {t}")
        self.a, _ = pd.save_business("a", "溶图", 7, 3, "名称：\n尺寸：\n备注（选填）：")
        self.b, _ = pd.save_business("B", "板鞋", 3, 2, "名称：")
        self.cid, _ = pd.create_client("小明", {self.b: 1})

    def order(self, cid=None, bid=None, day=D, **kw):
        o, err = pd.place_order(bid or self.a, "x", cid=self.cid if cid is None else cid, day=day, **kw)
        self.assertEqual(err, "", err)
        return o

    # ---- 开关 ----
    def test_default_off_personal(self):
        self.assertFalse(ec.is_enabled("paidan"))
        self.assertTrue(next(e for e in ec.OFFICIAL if e["id"] == "paidan").get("personal"))

    # ---- 业务、格式 ----
    def test_business_letter_unique_and_format(self):
        _, err = pd.save_business("A", "别的", 1, 1, "")
        self.assertIn("已经被", err)
        b = pd.business(self.a)
        self.assertEqual(pd.order_format(b), "【A下单】\n名称：\n尺寸：\n备注（选填）：\nQQ号：")
        self.assertTrue(pd.order_format(b, sandan=True).endswith("\n兑换码："))
        p = pd.parse_order("【a下单】\n名称：猫\n尺寸：\n备注（选填）：\n兑换码：abcd")
        self.assertEqual(p["letter"], "A")
        self.assertEqual(p["code"], "ABCD")
        self.assertEqual(pd.missing_fields(b, p["values"]), ["尺寸"])
        self.assertIsNone(pd.parse_order("随便说说"))

    # ---- QQ 号、私聊 owner ----
    def test_qq_field_required(self):
        b = pd.business(self.a)
        self.assertTrue(pd.template_missing_qq(b["template"]))
        self.assertIn("QQ号：", pd.order_format(b))                       # 格式里漏了自动补上
        p = pd.parse_order("【A下单】\n名称：猫\n尺寸：A4")
        self.assertIn("QQ号", pd.check_submission(b, p))                   # 没有这一栏交不了
        p = pd.parse_order("【A下单】\n名称：猫\n尺寸：A4\nQQ号：abc")
        self.assertIn("QQ号", pd.check_submission(b, p))                   # 不是数字也不行
        p = pd.parse_order("【A下单】\n名称：猫\n尺寸：\nQQ号：12345678")
        self.assertIn("尺寸", pd.check_submission(b, p))
        p = pd.parse_order("【A下单】\n名称：猫\n尺寸：A4\nQQ号：12345678")
        self.assertEqual((pd.check_submission(b, p), p["qq"]), ("", "12345678"))
        pd.save_business("A", "溶图", 7, 3, "名称：\nQQ号：", bid=self.a)
        self.assertFalse(pd.template_missing_qq(pd.business(self.a)["template"]))
        self.assertEqual(pd.order_format(pd.business(self.a)).count("QQ号"), 1)

    def test_owner_dm(self):
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        o, _ = pd.place_order(self.a, "【A下单】\n名称：猫\nQQ号：123456", cid=self.cid, qq="123456", day=D)
        text = pd.new_order_dm(o)
        self.assertTrue(text.startswith("【123456/小明 已排单-A-0001-DDL 11/25】"), text)
        self.assertIn("兑换码：无", text)
        self.assertIn("名称：猫", text)
        self.assertNotIn("【A下单】", text)
        codes, _ = pd.gen_codes(self.a, 1, "2026-11-30", D)
        o2, _ = pd.place_order(self.a, "x", code=codes[0], orderer_name="阿花", qq="654321", day=D)
        self.assertIn("【654321/散单·阿花 已排单-A-0002", pd.new_order_dm(o2))
        self.assertIn("兑换码：有：" + codes[0], pd.new_order_dm(o2))

    def test_client_real_gid_in_dm(self):
        self.assertIn("5–12", pd.set_client_gid(self.cid, "12"))
        pd.set_client_gid(self.cid, "群 987654321")
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        self.assertIn("客户群：987654321", pd.new_order_dm(self.order()))
        cid2, _ = pd.create_client("小红", real_gid="11223344")
        self.assertEqual(pd.client(cid2)["real_gid"], "11223344")

    def test_bundle_request(self):
        pd.bind(pd.new_bind_code("client", self.cid), "g_c", "u")
        pd.set_client_gid(self.cid, "987654321")
        dm, reply = pd.bundle_request("g_c", now=1_800_000_000)
        self.assertIn("【图包】小明 要发送图包", dm)
        self.assertIn("群号：987654321", dm)
        self.assertIn("已通知 owner", reply)
        dm2, reply2 = pd.bundle_request("g_c", now=1_800_000_100)      # 10 分钟内不重复通知
        self.assertEqual(dm2, "")
        self.assertIn("已经通知过", reply2)
        self.assertTrue(pd.bundle_request("g_c", now=1_800_000_700)[0])
        self.assertEqual(pd.bundle_request("g_unknown"), ("", ""))       # 没绑定的群不响应
        pd.bind(pd.new_bind_code("sandan"), "g_s", "u")
        pd.set_sandan_real_gid("55667788")
        dm, _ = pd.bundle_request("g_s", "阿花", now=1_800_000_000)
        self.assertIn("散单群·阿花", dm)
        self.assertIn("55667788", dm)

    # ---- 已完结、通知、我的额度、改内容、催单去处 ----
    def test_archive(self):
        pd.bind(pd.new_bind_code("client", self.cid), "g_c", "u")
        pd.add_quota(self.cid, self.a, 3, "2026-11-23", D)
        o = self.order()
        self.assertIn("在排", pd.set_archived(self.cid, True))            # 有在排的单不能完结
        pd.cancel_order(o["id"])
        self.assertEqual(pd.set_archived(self.cid, True), "")
        self.assertIn("完结", pd.place_order(self.a, "x", cid=self.cid, day=D)[1])
        self.assertEqual(pd.quota_reminders(D), [])                       # 不提醒
        self.assertEqual(pd.notify_groups(), [])                          # 不发暂停通知
        self.assertEqual(pd.overview(D)["expiring"], [])
        pd.set_archived(self.cid, False)
        self.order()

    def test_done_and_cancel_notice(self):
        pd.bind(pd.new_bind_code("client", self.cid), "g_c", "u_ming")
        pd.add_quota(self.cid, self.a, 2, "2026-11-30", D)
        o = self.order()
        pd.complete_order(o["id"], 3, day=D)
        text = pd.done_notice(pd.order(o["id"]), D)
        self.assertTrue(text.startswith("{{AT:u_ming:小明}}A-0001 已完成，扣 3 张，溶图还剩 0 张。"), text)
        self.assertIn("还差 1 张", text)
        self.assertEqual(pd.order_target(o)[0], "g_c")
        codes, _ = pd.gen_codes(self.a, 1, "2026-11-30", D)
        o2, _ = pd.place_order(self.a, "x", code=codes[0], orderer_openid="u_hua", orderer_name="阿花", day=D)
        pd.cancel_order(o2["id"])
        t2 = pd.cancel_notice(pd.order(o2["id"]))
        self.assertTrue(t2.startswith("{{AT:u_hua:阿花}}A-0002 已取消。兑换码已作废。"), t2)

    def test_my_status(self):
        pd.add_quota(self.cid, self.a, 1, "2026-11-21", D)
        pd.add_quota(self.cid, self.a, 5, "2026-12-30", D)
        self.assertIn("溶图：共 6 张（1 张 11/21 到期、5 张 12/30 到期）· 单口 0/3", pd.my_status(self.cid, D))
        o = self.order()
        pd.record_delivery(o["id"])
        self.assertIn("在排：A-0001（DDL 11/25，已发图）", pd.my_status(self.cid, D))
        cid2, _ = pd.create_client("小红")
        self.assertIn("没有有效额度", pd.my_status(cid2, D))

    def test_edit_content(self):
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        o = self.order()
        self.assertEqual(pd.edit_content(o["id"], "名称：狗"), "")
        self.assertEqual(pd.order(o["id"])["content"], "名称：狗")
        self.assertIn("空", pd.edit_content(o["id"], "  "))
        pd.complete_order(o["id"], 1, day=D)
        self.assertIn("在排", pd.edit_content(o["id"], "x"))

    def test_work_target_fallback(self):
        self.assertEqual(pd.work_target(self.a), ("", ""))
        pd.bind(pd.new_bind_code("owner"), "g", "u_owner")
        self.assertEqual(pd.work_target(self.a), ("owner", "u_owner"))      # 工作群没绑：私聊 owner
        pd.bind(pd.new_bind_code("work", self.a), "g_w", "u_owner")
        self.assertEqual(pd.work_target(self.a), ("group", "g_w"))

    def test_add_note(self):
        pd.bind(pd.new_bind_code("client", self.cid), "g_c", "u")
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        o = self.order()
        pd.lock_uploads(o["no"], "g_c")
        o2, err = pd.add_note("a-1", "g_c", "背景换成蓝色", now=1_795_000_000)      # 锁了图也能补充文字
        self.assertEqual(err, "")
        self.assertRegex(o2["content"], r"x\n【补充 \d+/\d+ \d\d:\d\d】背景换成蓝色$")
        self.assertIn("不是这个群", pd.add_note(o["no"], "g_other", "x")[1])
        self.assertIn("要写补充的内容", pd.add_note(o["no"], "g_c", " ")[1])
        self.assertIn("太长", pd.add_note(o["no"], "g_c", "字" * 501)[1])
        pd.complete_order(o["id"], 1, day=D)
        self.assertIn("已完成", pd.add_note(o["no"], "g_c", "x")[1])

    def test_client_qq_fallback(self):
        pd.set_client_qq(self.cid, "88888888")
        self.assertIn("5–12", pd.set_client_qq(self.cid, "12"))
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        self.assertEqual(self.order()["qq"], "88888888")

    # ---- 单口 ----
    def test_slots_default_and_custom(self):
        self.assertEqual(pd.slots_of(self.cid, self.a), 3)
        self.assertEqual(pd.slots_of(self.cid, self.b), 1)
        pd.save_business("B", "板鞋", 3, 4, "名称：", bid=self.b)
        pd.save_business("A", "溶图", 7, 5, "名称：", bid=self.a)
        pd.sync_default_slots(self.a)
        pd.sync_default_slots(self.b)
        self.assertEqual(pd.slots_of(self.cid, self.a), 5)      # 没改过的跟着变
        self.assertEqual(pd.slots_of(self.cid, self.b), 1)      # 单独改过的不变

    def test_slots_count_orders_across_quotas(self):
        pd.add_quota(self.cid, self.a, 1, "2026-11-30", D)
        pd.add_quota(self.cid, self.a, 5, "2026-12-30", D)
        for _ in range(3):
            self.order()
        _, err = pd.place_order(self.a, "x", cid=self.cid, day=D)
        self.assertIn("单口满了", err)

    def test_need_at_least_one_quota(self):
        _, err = pd.place_order(self.a, "x", cid=self.cid, day=D)
        self.assertIn("没有可用的额度", err)
        pd.add_quota(self.cid, self.a, 1, D, D)
        self.order()
        self.order()           # 只剩 1 张也能下第二单：不按在排单数预留
        self.assertEqual(pd.open_count(self.cid, self.a), 2)

    def test_order_numbers_per_business(self):
        pd.add_quota(self.cid, self.a, 5, "2026-12-30", D)
        pd.add_quota(self.cid, self.b, 5, "2026-12-30", D)
        self.assertEqual(self.order()["no"], "A-0001")
        self.assertEqual(self.order()["no"], "A-0002")
        o = self.order(bid=self.b)
        self.assertEqual(o["no"], "B-0001")
        self.assertEqual(o["deadline"], "2026-11-21")
        self.assertEqual(pd.order_by_no("b-1")["id"], o["id"])
        self.assertEqual(pd.find_no("回复 A-12 好的"), "A-0012")

    # ---- 到期、延期 ----
    def test_expiry_day_still_valid(self):
        pd.add_quota(self.cid, self.a, 2, D, D)
        self.assertEqual(pd.quota_left(self.cid, self.a, D), 2)
        self.assertEqual(pd.quota_left(self.cid, self.a, "2026-11-19"), 0)

    def test_extend_only_before_expiry(self):
        qid, _ = pd.add_quota(self.cid, self.a, 2, "2026-11-20", D)
        self.assertEqual(pd.extend_quota(qid, "2026-11-25", D), "")
        self.assertEqual(pd.quota(qid)["expires"], "2026-11-25")
        self.assertEqual(len(pd.quota_extends(qid)), 1)
        self.assertIn("过期", pd.extend_quota(qid, "2026-12-30", "2026-11-26"))

    # ---- 扣张数 ----
    def test_charge_earliest_first_and_cross(self):
        q1, _ = pd.add_quota(self.cid, self.a, 1, "2026-11-30", D)
        q2, _ = pd.add_quota(self.cid, self.a, 5, "2026-12-30", D)
        o = self.order()
        res, err = pd.complete_order(o["id"], 3, day=D)
        self.assertEqual(err, "")
        self.assertEqual(res["plan"], [(q1, 1), (q2, 2)])
        self.assertEqual(pd.quota(q1)["used"], 1)
        self.assertEqual(pd.quota(q2)["used"], 2)
        self.assertEqual(pd.order(o["id"])["status"], "done")
        self.assertEqual(pd.open_count(self.cid, self.a), 0)      # 完成后单口空出来

    def test_charge_pick_quota(self):
        q1, _ = pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        q2, _ = pd.add_quota(self.cid, self.a, 3, "2026-12-30", D)
        o = self.order()
        res, _ = pd.complete_order(o["id"], 2, first_quota=q2, day=D)
        self.assertEqual(res["plan"], [(q2, 2)])
        self.assertEqual(pd.quota(q1)["used"], 0)

    def test_charge_shortfall(self):
        q1, _ = pd.add_quota(self.cid, self.a, 1, "2026-11-30", D)
        o = self.order()
        res, _ = pd.complete_order(o["id"], 3, day=D)
        self.assertEqual(res["shortfall"], 2)
        self.assertEqual(pd.quota(q1)["used"], 1)
        self.assertEqual(pd.order(o["id"])["shortfall"], 2)
        self.assertEqual(len(pd.shortfall_orders(self.cid)), 1)

    def test_recharge_refunds(self):
        q1, _ = pd.add_quota(self.cid, self.a, 2, "2026-11-30", D)
        q2, _ = pd.add_quota(self.cid, self.a, 5, "2026-12-30", D)
        o = self.order()
        pd.complete_order(o["id"], 3, day=D)
        res, err = pd.recharge(o["id"], 1, first_quota=q2, day=D)
        self.assertEqual(err, "")
        self.assertEqual((pd.quota(q1)["used"], pd.quota(q2)["used"]), (0, 1))
        self.assertEqual(pd.order(o["id"])["charged"], 1)

    def test_expired_quota_not_charged(self):
        q1, _ = pd.add_quota(self.cid, self.a, 5, "2026-11-19", D)
        o = self.order()
        res, _ = pd.complete_order(o["id"], 1, day="2026-11-20")
        self.assertEqual(res["shortfall"], 1)
        self.assertEqual(pd.quota(q1)["used"], 0)

    # ---- 散单、兑换码 ----
    def test_sandan_code(self):
        codes, _ = pd.gen_codes(self.a, 2, "2026-11-30", D)
        self.assertEqual(len(set(codes)), 2)
        _, err = pd.place_order(self.b, "x", code=codes[0], day=D)
        self.assertIn("溶图", err)                                # 业务对不上
        o, err = pd.place_order(self.a, "x", code=codes[0].lower(), orderer_openid="u1", orderer_name="阿花", day=D)
        self.assertEqual(err, "")
        self.assertIsNone(o["client_id"])
        _, err = pd.place_order(self.a, "x", code=codes[0], day=D)
        self.assertIn("已使用", err)
        _, err = pd.place_order(self.a, "x", code=codes[1], day="2026-12-01")
        self.assertIn("已过期", err)
        res, _ = pd.complete_order(o["id"], 2, day=D)              # 散单只记张数
        self.assertEqual((res["plan"], res["shortfall"], pd.order(o["id"])["charged"]), ([], 0, 2))

    def test_cancel_voids_code(self):
        codes, _ = pd.gen_codes(self.a, 1, "2026-11-30", D)
        o, _ = pd.place_order(self.a, "x", code=codes[0], day=D)
        self.assertEqual(pd.cancel_order(o["id"]), "")
        self.assertEqual(pd.code_state(pd.codes()[0], D), "已作废")

    # ---- 暂停 ----
    def test_pause_today_extends_everything(self):
        qid, _ = pd.add_quota(self.cid, self.a, 2, D, D)
        o = self.order()
        codes, _ = pd.gen_codes(self.a, 1, D, D)
        self.assertEqual(pd.add_pause(D, D), "")
        self.assertEqual(pd.quota(qid)["expires"], "2026-11-19")
        self.assertEqual(pd.order(o["id"])["deadline"], "2026-11-26")
        self.assertEqual(pd.codes()[0]["expires"], "2026-11-19")      # 兑换码也顺延
        _, err = pd.place_order(self.a, "x", cid=self.cid, day=D)
        self.assertIn("暂停", err)
        self.assertEqual(pd.apply_pause(D), {})                      # 只顺延一次
        self.assertIn("不能取消", pd.cancel_pause(D))
        self.assertEqual(pd.quota_reminders(D), [])
        self.assertEqual(pd.work_reminders(D), {})

    def test_pause_future_applies_on_day(self):
        qid, _ = pd.add_quota(self.cid, self.a, 2, "2026-11-25", D)
        pd.add_pause("2026-11-20", D)
        self.assertEqual(pd.quota(qid)["expires"], "2026-11-25")
        self.assertEqual(pd.due_pauses("2026-11-19"), [])
        self.assertEqual(pd.due_pauses("2026-11-20"), ["2026-11-20"])
        pd.apply_pause("2026-11-20")
        self.assertEqual(pd.quota(qid)["expires"], "2026-11-26")
        pd.add_pause("2026-11-22", D)
        self.assertEqual(pd.cancel_pause("2026-11-22"), "")

    def test_pause_skips_expired_and_done(self):
        q_old, _ = pd.add_quota(self.cid, self.a, 2, "2026-11-19", D)
        q_ok, _ = pd.add_quota(self.cid, self.a, 2, "2026-11-30", D)
        o = self.order()
        pd.complete_order(o["id"], 1, day=D)
        pd.add_pause("2026-11-20", D)
        pd.apply_pause("2026-11-20")
        self.assertEqual(pd.quota(q_old)["expires"], "2026-11-19")      # 过期的不复活
        self.assertEqual(pd.quota(q_ok)["expires"], "2026-12-01")
        self.assertEqual(pd.order(o["id"])["deadline"], "2026-11-25")   # 完成的单不动

    # ---- 补图、锁定、发送 ----
    def test_upload_lock_and_deliver(self):
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        self.assertTrue(pd.bind(pd.new_bind_code("client", self.cid), "g_c", "u_ming")[0])
        o = self.order()
        self.assertEqual(pd.check_upload(o["no"], "g_other")[1], "A-0001 不是这个群的单。")
        self.assertEqual(pd.check_upload(o["no"], "g_c")[1], "")
        pd.lock_uploads(o["no"], "g_c")
        self.assertIn("已锁定", pd.check_upload(o["no"], "g_c")[1])
        pd.unlock_uploads(o["id"])
        self.assertEqual(pd.check_upload(o["no"], "g_c")[1], "")
        oo, gid, at, name, err = pd.deliver_target(o["no"])
        self.assertEqual((gid, at, name, err), ("g_c", "u_ming", "小明", ""))
        pd.complete_order(o["id"], 1, day=D)
        self.assertIn("已出图", pd.check_upload(o["no"], "g_c")[1])
        self.assertIn("不能再发送", pd.deliver_target(o["no"])[4])

    def test_sandan_upload_and_deliver(self):
        pd.bind(pd.new_bind_code("sandan"), "g_s", "u_x")
        codes, _ = pd.gen_codes(self.a, 1, "2026-11-30", D)
        o, _ = pd.place_order(self.a, "x", code=codes[0], orderer_openid="u_hua", orderer_name="阿花", day=D)
        self.assertEqual(pd.check_upload(o["no"], "g_s")[1], "")
        self.assertEqual(pd.deliver_target(o["no"])[1:4], ("g_s", "u_hua", "阿花"))

    # ---- 绑定 ----
    def test_bind_codes(self):
        code = pd.new_bind_code("work", self.a)
        self.assertEqual(pd.bind("nope", "g", "u"), (False, ""))
        ok, msg = pd.bind(code.lower(), "g_work", "u_owner")
        self.assertTrue(ok)
        self.assertEqual(pd.business_by_group("g_work")["id"], self.a)
        self.assertEqual(pd.bind(code, "g_work", "u")[0], False)       # 一次性
        pd.bind(pd.new_bind_code("owner"), "g_work", "u_owner")
        self.assertTrue(pd.is_owner("u_owner"))
        self.assertFalse(pd.is_owner("u_other"))

    # ---- 提醒 ----
    def test_reminders(self):
        pd.bind(pd.new_bind_code("client", self.cid), "g_c", "u")
        pd.add_quota(self.cid, self.a, 3, "2026-11-23", D)        # 5 天后
        pd.add_quota(self.cid, self.b, 3, "2026-11-22", D)        # 4 天后：不提醒
        r = pd.quota_reminders(D)
        self.assertEqual([(x["days"], x["left"]) for x in r], [(5, 3)])
        self.assertIn("11/23 到期", pd.quota_reminder_text(r[0]["quota"], 3))
        o1 = self.order()                                          # DDL 11-25
        o2 = self.order(day="2026-11-10")                          # DDL 11-17：超期 1 天
        o3 = self.order()
        pd.record_delivery(o3["id"])                               # 发过图：只进待扣张数
        w = pd.work_reminders("2026-11-20")[self.a]
        self.assertEqual([o["no"] for o in w["due"][5]], [o1["no"]])
        self.assertEqual([(o["no"], n) for o, n in w["overdue"]], [(o2["no"], 3)])
        self.assertEqual([o["no"] for o in w["pending"]], [o3["no"]])
        text = pd.work_reminder_text(w)
        self.assertIn("5 天后到期：A-0001 小明", text)
        self.assertIn("超期 3 天", text)
        self.assertIn("待扣张数：A-0003", text)
        self.assertEqual(pd.work_reminders("2026-11-21").get(self.a, {}).get("due"), {})   # 4 天后：不催

    def test_pause_notice_text(self):
        pd.add_quota(self.cid, self.a, 3, "2026-11-30", D)
        self.assertIn("明天恢复", pd.pause_notice(self.cid, D))
        self.order()
        self.assertIn("在排的单", pd.pause_notice(self.cid, D))
        self.assertIn("兑换码", pd.pause_notice(None, D))


if __name__ == "__main__":
    unittest.main()
