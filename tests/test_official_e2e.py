"""官约 / 官电：邀请的说法、给管理员的回执按实际送达说、提前排到以后几天的先只占时间线，走真正的 NoneBot 事件分发。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "o.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import admins, announce, overdue, pool, roles, rooms, season  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from tests._linkup import links_up  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    from changri.core import extensions
    extensions.load_all()

from nonebot.message import handle_event  # noqa: E402
from changri.core import qq_compat  # noqa: E402

qq_compat.apply()
from changri.plugins import play  # noqa: E402

SENT: list = []
BLOCKED: set = set()          # 这些群没开「主动在群聊内发言」


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(str(message))

    async def send_to_group(self, group_openid, message, **kw):
        if group_openid in BLOCKED:
            raise RuntimeError("主动消息失败, 无权限")
        SENT.append(f"[{group_openid}] {message}")


def rng(back=1, fwd=5):
    t = time.time()
    return time.strftime("%m%d", time.localtime(t - back * 86400)) + "-" + time.strftime("%m%d", time.localtime(t + fwd * 86400))


def at(hh, mm=0):
    lt = time.localtime()
    return time.struct_time((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))


TABLES = ("person_qq", "room_lines", "join_requests", "room_members", "rooms", "pool_groups", "seasons", "role_groups", "roles", "admins", "admin_codes", "config")


@links_up
class OfficialE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in TABLES:
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM sqlite_sequence")
        SENT.clear()
        BLOCKED.clear()
        play._defer_waiting_told.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        nonebot.get_bots()["B1"] = self.bot
        admins.claim(admins.make_code(), "B1", "ADMIN")
        set_config("qq_buttons", "0")
        self.n = 0
        self.say(f"预订季度 甲季 {rng()}", member="ADMIN", group="ADM")
        self.say("开始季度 确认", member="ADMIN", group="ADM")
        self.say("设置天数 D1", member="ADMIN", group="ADM")
        for i, name in enumerate(["甲", "乙", "丙"]):
            roles.create_role(name, "B1", f"P{i}", f"M{i}")
        for i in range(3):
            pool.register("B1", f"POOL{i}", real_gid=f"12345{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)
        announce.set_backstage("B1", "STAFF")

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def say(self, text, member="ADMIN", group="ADM"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return SENT[before:]

    def run_async(self, coro):
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(coro)
        return SENT[before:]

    # ---------- ① 邀请的说法 ----------
    def test_official_invite_has_no_change_or_refuse_lines(self):
        out = self.say("发起官约 1400-1500 后山 甲/乙")
        inv = next(o for o in out if o.startswith("[P1]") and "官约" in o)
        self.assertNotIn("修改时间线", inv)
        self.assertNotIn("拒绝约会", inv)
        self.assertIn("时间由管理员安排，来不了请联系管理员", inv)
        out = self.say("发起官电 1600-1700 甲/乙")
        inv = next(o for o in out if o.startswith("[P1]") and "官电" in o)
        self.assertNotIn("修改时间线", inv)
        self.assertIn("来不了请联系管理员", inv)

    def test_date_invite_keeps_decline_but_wish_does_not(self):
        out = self.say("约会 1400-1500 咖啡厅 乙", member="M0", group="P0")
        inv = next(o for o in out if o.startswith("[P1]") and "向你发起了约会" in o)
        self.assertIn("不想参加 ➜ 拒绝约会 R1", inv)                       # 约会能拒（整场取消）
        room = rooms.get(1)
        wish = dict(room) | {"kind": "心愿"}
        text = play._room_text(wish)
        self.assertIn("修改时间线 R1", text)
        self.assertNotIn("不想参加", text)                                    # 心愿不能拒，不写

    # ---------- ④ 回执按实际送达说 ----------
    def test_receipt_counts_only_delivered_and_names_missed(self):
        BLOCKED.add("P1")
        reply = self.say("发起官约 1400-1500 后山 甲/乙/丙")[-1]
        self.assertIn("已通知 2 人", reply)
        self.assertIn("没发到：乙（可能是 TA 的个人群没开主动发言）", reply)
        self.assertTrue(rooms.get(1))                                         # 官约照样开着

    def test_receipt_all_delivered_has_no_warning(self):
        reply = self.say("发起官约 1400-1500 后山 甲/乙")[-1]
        self.assertIn("已通知 2 人", reply)
        self.assertNotIn("没发到", reply)

    def test_run_plan_summary_names_missed(self):
        BLOCKED.update({"P0", "P2"})
        self.say("计划官约 1400-1500 后山 甲/乙")
        self.say("计划官约 1600-1700 后山 乙/丙")
        out = "\n".join(self.say("执行官约"))
        self.assertIn("已发起 2 场", out)
        self.assertIn("没发到：甲（可能是 TA 的个人群没开主动发言）", out)
        self.assertIn("没发到：丙（可能是 TA 的个人群没开主动发言）", out)

    # ---------- ③ 提前排到以后几天：先只占时间线，到那天再开房 ----------
    def test_future_official_only_takes_timeline(self):
        set_config("room_expire_enabled", "1")
        set_config("ext:overdue", "1")
        out = self.say("发起官约 D3 2000-2100 后山 甲/乙")
        reply = out[-1]
        self.assertIn("官约已排好", reply)
        self.assertIn("D3 当天 08:00 左右再分群、发邀请", reply)
        self.assertIn("已发预告给 2 人", reply)
        self.assertTrue(any(o.startswith("[P1]") and "官约预告" in o and "来不了请联系管理员" in o for o in out))
        self.assertFalse(any("请申请入群" in o for o in out))                 # 还没发群号
        self.assertFalse(any(o.startswith("[POOL") for o in out))
        r = rooms.get(1)
        self.assertIsNone(r["pool_id"])
        self.assertTrue(rooms.is_deferred(r))
        self.assertEqual(pool.counts()["occupied"], 0)
        # 占着时间线：乙那时候约不了别的
        self.assertEqual(rooms.conflict(roles.get_role_by_name("乙")["id"], 3, "20:30", "21:30")["id"], 1)
        self.assertIn("D3 08:00 左右发群号", self.say("我的待回", member="M1", group="P1")[-1])
        # 隔了很久：不会被强制结群、不提醒收尾、不催回复、不算进今天的统计
        with get_conn() as c:
            c.execute("UPDATE rooms SET created_ts=? WHERE id=1", (int(time.time()) - 30 * 3600,))
        self.assertEqual(rooms.expired(), [])
        self.assertEqual(rooms.expiring_soon(), [])
        self.assertEqual(overdue.due(), [])
        self.assertIn("官约 0 场", season.day_summary_line())
        self.assertEqual(rooms.deferred_due(at(12)), [])                      # 还是 D1
        self.assertEqual(rooms.assign_waiting(), [])                          # 普通的补群不管它

    def test_future_official_opens_on_the_day(self):
        set_config("room_expire_enabled", "1")
        self.say("发起官约 D3 2000-2100 后山 甲/乙")
        self.say("设置天数 D3")
        self.assertEqual(rooms.deferred_due(at(0, 1)), [])                    # 换天时不发，免得半夜叫人
        self.assertEqual(len(rooms.deferred_due(at(8))), 1)
        rooms.DEFER_OPEN_HM, old = "00:00", rooms.DEFER_OPEN_HM                # 跑测试的钟点不定：开房时间改成 0 点
        try:
            out = self.run_async(play._tick_deferred())
        finally:
            rooms.DEFER_OPEN_HM = old
        r = rooms.get(1)
        self.assertIsNotNone(r["pool_id"])
        self.assertFalse(rooms.is_deferred(r))
        self.assertTrue(any(o.startswith("[P0]") and "🏢 官约" in o for o in out))
        self.assertTrue(any(o.startswith("[P1]") and "请申请入群" in o for o in out))
        self.assertTrue(any(o.startswith("[POOL") and "R1 官约" in o for o in out))
        self.assertIn("官约 1 场", season.day_summary_line())                  # 算在开房那天
        self.assertEqual(rooms.expired(), [])                                 # 结戏限时从开房算
        self.assertGreater(r["created_ts"], int(time.time()) - 60)

    def test_early_official_opens_two_hours_before(self):
        self.say("发起官约 D2 0600-0700 后山 甲/乙")
        r = rooms.get(1)
        self.assertEqual(rooms.defer_open_hm(r), "04:00")
        self.say("设置天数 D2")
        self.assertEqual(rooms.deferred_due(at(3, 59)), [])
        self.assertEqual(len(rooms.deferred_due(at(4))), 1)
        self.say("设置天数 D3")                                                # 天数已经过了：直接开
        self.assertEqual(len(rooms.deferred_due(at(0, 1))), 1)

    def test_future_official_waits_when_pool_empty_and_tells_backstage_once(self):
        self.say("发起官约 D2 1400-1500 后山 甲/乙")
        with get_conn() as c:
            c.execute("UPDATE pool_groups SET state='occupied'")
        self.say("设置天数 D2")
        rooms.DEFER_OPEN_HM, old = "00:00", rooms.DEFER_OPEN_HM
        try:
            out = self.run_async(play._tick_deferred())
            self.assertTrue(any(o.startswith("[STAFF]") and "没有空群" in o for o in out), out)
            self.assertFalse(any(o.startswith("[STAFF]") for o in self.run_async(play._tick_deferred())))    # 只提醒一次
            self.assertTrue(rooms.is_deferred(rooms.get(1)))
            with get_conn() as c:
                c.execute("UPDATE pool_groups SET state='free'")
            out = self.run_async(play._tick_deferred())
            self.assertTrue(any(o.startswith("[P1]") and "请申请入群" in o for o in out))
        finally:
            rooms.DEFER_OPEN_HM = old

    def test_future_official_missed_invites_go_to_backstage(self):
        self.say("发起官约 D2 1400-1500 后山 甲/乙")
        self.say("设置天数 D2")
        BLOCKED.add("P1")
        rooms.DEFER_OPEN_HM, old = "00:00", rooms.DEFER_OPEN_HM
        try:
            out = self.run_async(play._tick_deferred())
        finally:
            rooms.DEFER_OPEN_HM = old
        self.assertTrue(any(o.startswith("[STAFF]") and "没发到：乙（可能是 TA 的个人群没开主动发言）" in o for o in out), out)

    def test_cancel_future_official(self):
        self.say("发起官约 D3 2000-2100 后山 甲/乙")
        out = self.say("取消官约 R1")
        self.assertIn("已取消", out[-1])
        self.assertTrue(any(o.startswith("[P1]") and "已经取消" in o and "请退出" not in o for o in out))     # 没群就不叫人退群
        self.assertEqual(rooms.deferred_due(at(23)), [])

    def test_today_official_unchanged(self):
        out = self.say("发起官约 D1 1400-1500 后山 甲/乙")
        self.assertIn("官约已发起", out[-1])
        self.assertIsNotNone(rooms.get(1)["pool_id"])


if __name__ == "__main__":
    unittest.main()
