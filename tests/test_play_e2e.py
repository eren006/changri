"""核心玩法：房间（约会/电话/官约/微信）、群池、时间线、待回、心愿、论坛，走真正的 NoneBot 事件分发。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import admins, pool, roles, rooms, season, wishes  # noqa: E402
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

nonebot.load_plugin("changri.plugins.wishes")
nonebot.load_plugin("changri.plugins.precall")          # 心愿默认关，不在 load_all 里；测试里直接加载
from nonebot.message import handle_event  # noqa: E402
from changri.core import qq_compat  # noqa: E402

qq_compat.apply()

SENT: list = []


def rng(back=1, fwd=5):
    t = time.time()
    return time.strftime("%m%d", time.localtime(t - back * 86400)) + "-" + time.strftime("%m%d", time.localtime(t + fwd * 86400))


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(message)

    async def send_to_group(self, group_openid, message, **kw):
        SENT.append(f"[{group_openid}] {message}")


TABLES = ("person_qq", "room_lines", "precall_calls", "precall_picks", "precall_entries", "precall_groups", "votes", "join_requests", "replies", "posts", "wishes", "room_members", "rooms", "pool_groups", "seasons", "entrance", "role_groups", "roles", "admins", "admin_codes", "config", "blocks")


@links_up
class PlayE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in TABLES:
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM sqlite_sequence")      # 编号从头算，测试里才能写死「001」
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        nonebot.get_bots()["B1"] = self.bot
        admins.claim(admins.make_code(), "B1", "ADMIN")
        set_config("qq_buttons", "0")         # 老测试断言的是纯文字；按钮的路径在 test_buttons.py 里测
        self.n = 0
        self.say(f"预订季度 甲季 {rng()}", member="ADMIN", group="ADM")
        self.say("开始季度 确认", member="ADMIN", group="ADM")
        self.say("设置天数 D1", member="ADMIN", group="ADM")
        for i, name in enumerate(["甲", "乙", "丙"]):          # 开季会清空角色，所以角色在开季之后建
            roles.create_role(name, "B1", f"P{i}", f"M{i}")

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def say(self, text, member="M0", group="P0", **extra):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group, **extra})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [m if isinstance(m, str) else "<图>" for m in SENT[before:]]

    def add_pool(self, n=1, real="123456"):
        for i in range(n):
            pool.register("B1", f"POOL{i}", real_gid=f"{real}{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)

    # ---------- 角色卡、背包、转账、兑换 ----------
    def _econ(self):
        from changri.core import rpg
        rpg.save_item("GOLD", "金币", kind="currency")
        rpg.save_item("SILVER", "银币", kind="currency")
        rpg.save_item("TEA", "热茶")
        ids = {r["name"]: r["id"] for r in get_conn().execute("SELECT id, name FROM roles")}
        rpg.adjust(ids["甲"], "GOLD", 200)
        rpg.adjust(ids["甲"], "TEA", 2)
        return rpg, ids

    def test_card_and_bag_are_images(self):
        self._econ()
        self.assertEqual(self.say("角色卡"), ["<图>"])
        self.assertEqual(self.say("名片"), ["<图>"])
        self.assertEqual(self.say("背包"), ["<图>"])
        self.assertIn("空", self.say("背包", member="M2", group="P2")[0])                  # 空背包还是文字

    def test_transfer_currency_and_items(self):
        rpg, ids = self._econ()
        out = self.say("转账 乙 金币 50")
        self.assertTrue(any("已赠送" in o for o in out))
        self.assertTrue(any("[P1]" in o and "金币×50" in o for o in out))                   # 乙的个人群收到通知
        self.assertEqual({x["item_code"]: x["count"] for x in rpg.inventory(ids["乙"])}["GOLD"], 50)
        self.assertIn("数量不足", self.say("转账 乙 金币 999")[0])
        self.assertTrue(any("已赠送" in o for o in self.say("赠送道具 乙 热茶 1")))
        from changri.core import blocks
        blocks.block(ids["乙"], ids["甲"], False)                                          # 乙拉黑了甲：转不过去
        self.assertIn("已拒绝你的联系", self.say("转账 乙 金币 10")[0])
        self.assertEqual({x["item_code"]: x["count"] for x in rpg.inventory(ids["乙"])}["GOLD"], 50)

    def test_exchange(self):
        rpg, ids = self._econ()
        self.assertIn("没有可以兑换", self.say("兑换")[0])
        ok, _ = rpg.save_exchange("GOLD", 100, "SILVER", 1)
        self.assertTrue(ok)
        self.assertIn("1. 金币×100 → 银币×1", self.say("兑换")[0])
        self.assertIn("已兑换", self.say("兑换 1")[0])
        inv = {x["item_code"]: x["count"] for x in rpg.inventory(ids["甲"])}
        self.assertEqual((inv["GOLD"], inv["SILVER"]), (100, 1))
        self.assertIn("只有 100 件", self.say("兑换 1 5")[0])                                    # 材料不够：什么都不动
        inv = {x["item_code"]: x["count"] for x in rpg.inventory(ids["甲"])}
        self.assertEqual((inv["GOLD"], inv["SILVER"]), (100, 1))
        self.assertIn("没有这条兑换", self.say("兑换 9")[0])

    # ---------- 房间 ----------
    def test_private_appointment_flow(self):
        self.add_pool(1)
        self.assertIn("用法", self.say("约会 1400-1500")[0])
        self.assertIn("用法", self.say("私约 1400-1500")[0])                     # 0.21 前叫私约，老叫法照样认
        self.assertIn("找不到角色", self.say("约会 1400-1500 咖啡厅 路人")[0])
        self.assertIn("时间格式", self.say("约会 下午 咖啡厅 乙")[0])
        self.assertIn("不能约自己", self.say("约会 1400-1500 咖啡厅 甲")[0])
        out = self.say("约会 1400-1500 咖啡厅 乙")
        self.assertTrue(any("已发起" in o and "R" in o and "1234560" in o for o in out))
        self.assertTrue(any("[P1]" in o and "向你发起了约会" in o for o in out))          # 对方个人群收到通知
        self.assertTrue(any("[POOL0]" in o and "参与者" in o for o in out))               # 房间群里发了说明
        # 时间撞了
        self.assertIn("撞了", self.say("约会 1430-1530 花园 丙")[0])
        # 时间线
        self.assertIn("14:00-15:00 约会 @咖啡厅 与 乙", self.say("时间线")[0])
        self.assertIn("只有管理员", self.say("时间线 乙")[0])
        self.assertIn("乙 的时间线", self.say("时间线 乙", member="ADMIN", group="ADM")[0])
        self.assertIn("找不到角色", self.say("删除时间线 路人 R1", member="ADMIN", group="ADM")[0])
        # 待回：还没进群
        self.assertIn("还没进群", self.say("我的待回")[0])
        # 在房间群里说话 = 到场
        self.say("大家好", member="M0", group="POOL0")
        self.assertNotIn("还没进群", self.say("我的待回")[0])
        # 结束
        out = self.say("结束约会", member="M1", group="POOL0")
        self.assertTrue(any("已经结束" in o and "请退群" in o for o in out))
        self.assertFalse(any("[P0]" in o and "已结束" in o for o in out))                       # 正常结束不再往个人群发通知
        self.assertTrue(any("管理员确认人都退了" in o for o in out))
        self.assertEqual(pool.counts()["cleaning"], 1)                                           # 官方机器人的群查不了人：先等人退群
        self.assertEqual(pool.counts()["free"], 0)
        set_config("pool_auto_free", "1")                                                        # 这个设置只管小号的群
        self.assertEqual(pool.counts()["free"], 0)
        self.assertIn("权限不足", self.say("放回备用群", member="M1", group="POOL0")[0])
        self.assertIn("不是官方机器人的备用群", self.say("放回备用群 999999", member="ADMIN", group="ADM")[0])
        self.assertIn("已放回备用群", self.say("放回备用群", member="ADMIN", group="POOL0")[0])       # 管理员确认人退光了
        self.assertEqual(pool.counts()["free"], 1)
        self.assertIn("已经是空闲的备用群", self.say("放回备用群 1234560", member="ADMIN", group="ADM")[0])

    def test_gongzhi_erbiao_submit_and_view(self):
        """提交公知/二表（覆盖）；查看公知出长图；别人看公知要「展示公知」开着；二表只有本人能看。"""
        self.assertIn("没有内容", self.say("提交公知")[0])
        self.assertIn("已提交", self.say("提交公知 我是甲，今年二十五")[0])
        self.assertIn("已提交", self.say("提交公知 改一版")[0])                     # 覆盖
        self.assertEqual(self.say("查看公知"), ["<图>"])                          # 自己的：一张长图
        self.assertIn("暂未开放", self.say("查看公知 甲", member="M1", group="P1")[0])
        set_config("gongzhi_public", "1")
        self.assertEqual(self.say("查看公知 甲", member="M1", group="P1"), ["<图>"])
        # 不带名字 = 所有人的；带名字 = 只看这一个人
        self.assertIn("已提交", self.say("提交公知 我是乙", member="M1", group="P1")[0])
        from unittest import mock
        from changri.plugins import relations as rel
        seen = []

        async def spy(matcher, sh, kind):
            seen.append(sh)
            await matcher.send("<图>")
        with mock.patch.object(rel, "_show", spy):
            self.say("查看公知", member="M1", group="P1")
            self.say("查看公知 甲", member="M1", group="P1")
        self.assertIsInstance(seen[0], list); self.assertEqual({x["name"] for x in seen[0]}, {"甲", "乙"})
        self.assertIsInstance(seen[1], dict); self.assertEqual(seen[1]["name"], "甲")
        set_config("gongzhi_public", "0")
        with mock.patch.object(rel, "_show", spy):
            self.say("查看公知", member="M1", group="P1")                 # 没开放：只看自己的
        self.assertEqual(seen[2]["name"], "乙")
        set_config("gongzhi_public", "1")
        self.say("提交二表 二表内容")
        self.assertIn("只有本人", self.say("查看二表 甲", member="M1", group="P1")[0])
        self.assertEqual(self.say("查看二表"), ["<图>"])
        self.assertIn("还没有提交", self.say("查看二表", member="M1", group="P1")[0])

    def test_allocation_skips_group_with_leftover_players(self):
        """分群前查群成员：群里还有别的玩家（绑过 QQ 的人）就先放一边；换不到干净的群就当没有空群，房间不建、不排队；人退光后自动放回池，再发起就能开。"""
        import asyncio
        from changri.core import qqmanager, qqpool, rooms
        from changri.plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        roles.bind_qq(roles.get_role_by_name("丙")["id"], "5550009")      # 丙不在这个房间里，却还留在 100001 群
        members = {"100001": {"5550009"}}

        async def fake(app_id, gid):
            return members[gid]
        old, qqmanager.group_members = qqmanager.group_members, fake
        try:
            out = self.say("约会 1400-1500 咖啡厅 乙")
            self.assertFalse(any("请申请入群" in o and "100001" in o for o in out), out)     # 有人的群不能分给房间
            self.assertTrue(any("备用群池暂时没有空闲的群" in o for o in out), out)            # 当没有空群：不排队
            self.assertEqual(pool.counts()["waiting"], 0)
            st = lambda: [g for g in pool.all_groups() if g["real_gid"] == "100001"][0]["state"]      # noqa: E731
            self.assertEqual(st(), "cleaning")
            members["100001"] = set()                                    # 人退光了
            asyncio.run(play._sweep_cleaning())
            self.assertEqual(st(), "free")
            out = self.say("约会 1400-1500 咖啡厅 乙")
            self.assertTrue(any("请申请入群" in o and "100001" in o for o in out), out)
        finally:
            qqmanager.group_members = old

    def test_listener_room_commands_in_qq_group(self):
        """房间群没有卡拉：监听的 QQ 号听到「结束约会」「修改时间线」等，按绑定的 QQ 认人，由 QQ 号在群里回。"""
        from changri.core import qqmanager, qqpool
        from changri.core.qqlink import GroupMsg
        from changri.plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        out = self.say("约会 1400-1500 咖啡厅 乙")
        self.assertTrue(any("请申请入群" in o and "100001" in o for o in out))
        roles.bind_qq(roles.get_role_by_name("甲")["id"], "555555")
        said = []

        async def fake_say(app, gid, text, ats):
            said.append((gid, text))
            return ""

        orig = qqmanager.room_say
        qqmanager.room_say = fake_say
        try:
            run = lambda text, uid: asyncio.new_event_loop().run_until_complete(play._listen_cmd("B1", GroupMsg("9", "100001", uid, "x", text, 1, "1")))   # noqa: E731
            self.assertFalse(run("你好", "555555"))                                  # 不是房间指令
            self.assertTrue(run("结束约会", "999999"))                               # 没绑定：提示先绑定
            self.assertIn("绑定QQ", said[-1][1])
            self.assertTrue(run("修改时间线 D1 1600-1700", "555555"))
            self.assertIn("申请把时间线改成", said[-1][1])
            self.assertTrue(run("结束约会", "555555"))
        finally:
            qqmanager.room_say = orig
        self.assertTrue(any("已经结束，请退群" in t for _, t in said))
        self.assertIsNone(rooms.room_of_group("B1", "qq:100001"))

    def test_no_queue_when_pool_empty(self):
        """备用群池没有空群：不排队，直接告诉玩家没开成，也不留下房间。"""
        out = self.say("电话 1400-1500 乙")
        self.assertTrue(any("备用群池暂时没有空闲的群" in o for o in out), out)
        self.assertEqual(pool.counts()["waiting"], 0)
        self.assertEqual(rooms.timeline(roles.get_role_by_name("甲")["id"]), [])
        self.add_pool(1)                                         # 补了群之后就能开
        self.assertTrue(any("电话已发起" in o for o in self.say("电话 1400-1500 乙")))

    def test_phone_cap_and_daily_limit(self):
        self.add_pool(4)
        set_config("phone_open_cap", "1")
        self.say("电话 1400-1500 乙")
        self.assertIn("还没结束", self.say("电话 1600-1700 丙")[0])
        set_config("limit_约会", "1")
        self.say("约会 1000-1100 咖啡厅 丙")
        self.assertIn("次数用完", self.say("约会 1200-1300 咖啡厅 丙")[0])

    def test_stakeout_solo_room(self):
        self.add_pool(1)
        self.assertIn("没有开放", self.say("踩点 1400-1500 大图书馆")[0])          # 默认关
        set_config("stakeout_enabled", "1")
        self.assertIn("用法", self.say("踩点 1400-1500")[0])
        out = self.say("踩点 1400-1500 大图书馆")
        self.assertTrue(any("踩点已发起" in o and "只有你一个人" in o and "1234560" in o for o in out))
        rid = rooms.open_rooms()[0]["id"]
        self.assertEqual([m["name"] for m in rooms.members(rid)], ["甲"])
        self.assertIn("撞了", self.say("约会 1430-1530 咖啡厅 乙")[0])            # 踩点占时间线
        a, b = (roles.get_role_by_name(n)["id"] for n in ("甲", "乙"))
        self.assertIn("不能带别人", rooms.create("踩点", b, [a], "1600-1700", "花园")[1])
        self.assertIn("要写地点", rooms.create("踩点", b, [], "1600-1700", "")[1])
        self.assertIn("踩点", self.say("我的数量")[0])
        out = self.say("结束踩点", member="M0", group="POOL0")
        self.assertTrue(any("已经结束" in o for o in out))
        set_config("limit_踩点", "1")
        self.assertIn("次数用完", self.say("踩点 1800-1900 花园")[0])
        set_config("stakeout_enabled", "0")
        self.assertIn("没有开放", rooms.create("踩点", b, [], "1600-1700", "花园")[1])

    def test_wechat_no_timeline(self):
        a, b, c = (roles.get_role_by_name(n)["id"] for n in ("甲", "乙", "丙"))
        self.assertTrue(rooms.create("微信", a, [b], "", "")[0])               # 网页多人群聊建的房间
        self.assertTrue(rooms.create("微信", a, [c], "", "")[0])
        self.assertIn("时间线还是空的", self.say("时间线")[0])
        self.assertTrue(rooms.create("微信", a, [b], "", "")[0])               # 微信不查时间撞
        self.assertIn("小手机", self.say("微信 乙")[0])                          # bot 端不再做，指向小手机

    def test_block_respected(self):
        from changri.core import blocks
        blocks.block(roles.get_role_by_name("乙")["id"], roles.get_role_by_name("甲")["id"], False)
        self.assertIn("已拒绝", self.say("电话 1400-1500 乙")[0])
        blocks.block(roles.get_role_by_name("丙")["id"], roles.get_role_by_name("甲")["id"], True)
        self.assertIn("发起失败", self.say("电话 1400-1500 丙")[0])

    def test_block_multi_invite_drops_blocked_only(self):
        """多人邀约：拉黑了发起人的人被剔掉，其余人照常成局；非静默的会告诉发起人，静默的不提；人都被剔光就失败。"""
        from changri.core import blocks, rooms
        self.add_pool(2)
        a, b, c = (roles.get_role_by_name(n)["id"] for n in ("甲", "乙", "丙"))
        blocks.block(b, a, False)
        out = self.say("约会 1400-1500 咖啡厅 乙/丙")
        self.assertTrue(any("约会已发起" in o for o in out), out)
        self.assertTrue(any("乙 已拒绝你的联系" in o for o in out), out)
        room = rooms.get(1)
        self.assertEqual({m["role_id"] for m in rooms.members(1)}, {a, c})            # 乙没进房间
        blocks.block(c, a, True)
        out = self.say("约会 1600-1700 咖啡厅 乙/丙")
        self.assertTrue(any("邀约对象都不可用" in o for o in out), out)                  # 两个都拉黑了：失败，不建群
        self.assertEqual(len(rooms.open_rooms()), 1)
        with get_conn() as cn:                                                        # 刚拉黑的要满 2 小时才能解除，测试里直接删
            cn.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (b, a))
        out = self.say("约会 1600-1700 咖啡厅 乙/丙")
        self.assertTrue(any("约会已发起" in o for o in out), out)
        self.assertFalse(any("已拒绝" in o for o in out), out)                        # 丙静默拉黑：悄悄剔掉，不留痕迹
        self.assertEqual({m["role_id"] for m in rooms.members(2)}, {a, b})
        self.assertIn("发起失败", self.say("约会 1800-1900 咖啡厅 丙")[0])              # 只邀丙一个人：单人邀约，直接失败

    def test_block_wechat_and_join(self):
        from changri.core import blocks
        a, b = (roles.get_role_by_name(n)["id"] for n in ("甲", "乙"))
        blocks.block(b, a, False)
        self.assertFalse(rooms.create("微信", a, [b], "", "")[0])

    def test_precall_commands(self):
        from changri.core import precall
        self.add_pool(1)
        set_config("ext:precall", "1")
        try:
            self.assertIn("编号是 #1", self.say("提交前置电话 喜欢下雨天")[0])
            self.say("提交前置电话 夜猫子", member="M1", group="P1")
            self.assertIn("还没有开放", self.say("查看前置电话")[0])                     # 都没分组
            self.assertIn("已把 2 条归到「A组」", self.say("前置电话分组 A组 1 2", member="ADMIN", group="ADM")[0])
            self.say("可选 A组", member="ADMIN", group="ADM")
            out = self.say("查看前置电话")[0]
            self.assertIn("#2", out)
            self.assertIn("夜猫子", out)
            self.assertIn("不能拨给自己", self.say("拨打前置电话 1")[0])
            out = self.say("拨打 2")
            self.assertTrue(any("已拨打 #2" in o for o in out), out)
            self.assertEqual(rooms.my_open(roles.get_role_by_name("乙")["id"])[0]["kind"], "前置电话")
            self.assertIn("已经通过话", self.say("拨打前置电话 2")[0])
        finally:
            set_config("ext:precall", "0")

    def test_official_and_cancel(self):
        self.add_pool(1)
        self.assertIn("权限不足", self.say("发起官约 1400-1500 后山 乙/丙")[0])
        out = self.say("发起官约 1400-1500 后山 乙/丙", member="ADMIN", group="ADM")
        self.assertTrue(any("官约已发起" in o for o in out))
        self.assertTrue(any("[P1]" in o and "官约" in o for o in out) and any("[P2]" in o for o in out))
        rid = rooms.timeline(roles.get_role_by_name("乙")["id"])[0]["id"]
        self.assertIn("已取消", self.say(f"取消官约 R{rid}", member="ADMIN", group="ADM")[-1])
        self.assertEqual(rooms.get(rid)["status"], "cancelled")
        self.assertIn("时间线还是空的", self.say("时间线", member="M1", group="P1")[0])

    def test_official_without_place_and_with_day(self):
        self.add_pool(4)
        out = self.say("发起官约 1400-1500 乙/丙", member="ADMIN", group="ADM")          # 不写地点
        self.assertTrue(any("官约已发起" in o for o in out), out)
        r1 = rooms.timeline(roles.get_role_by_name("乙")["id"])[0]
        self.assertEqual(r1["place"], "")
        out = self.say("发起官约 D5 1400-1500 无 乙/丙", member="ADMIN", group="ADM")    # 指定天、地点写「无」
        self.assertTrue(any("D5" in o for o in out), out)
        self.assertEqual({r["day"] for r in rooms.timeline(roles.get_role_by_name("乙")["id"])}, {1, 5})
        out = self.say("发起官电 D6 2000-2100 乙/丙", member="ADMIN", group="ADM")
        self.assertTrue(any("官电已发起" in o for o in out), out)

    def test_change_timeline_needs_everyone(self):
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        rid = rooms.timeline(roles.get_role_by_name("甲")["id"])[0]["id"]
        self.assertIn("申请", self.say("修改时间线 D1 1600-1700", member="M0", group="POOL0")[0])
        self.assertIn("同意时间线", self.say("我的待回", member="M1", group="P1")[0])
        out = self.say("同意时间线", member="M1", group="POOL0")
        self.assertTrue(any("时间线已改为 D1 16:00-17:00" in o for o in out))
        self.assertEqual(rooms.get(rid)["start_hm"], "16:00")
        # 拒绝
        self.say("修改时间线 1800-1900", member="M0", group="POOL0")
        self.assertTrue(any("已拒绝" in o for o in self.say("拒绝时间线", member="M1", group="POOL0")))
        self.assertEqual(rooms.get(rid)["start_hm"], "16:00")

    def test_sheet_submit_tells_backstage(self):
        """提交公知/二表：公告群发「已经上交」一句；后台群收一份带内容的；公知再交是「修改了公知」；二表再交不发。"""
        from changri.core import announce
        announce.set_backstage("B1", "BACK")
        announce.set_announce("B1", "ANN")
        out = self.say("提交二表 我的二表")
        self.assertTrue(any(o.startswith("[BACK]") and "甲 已经上交二表" in o and "我的二表" in o for o in out), out)
        self.assertFalse(any(o.startswith("[BACK]") for o in self.say("提交二表 改过")))
        self.assertTrue(any(o.startswith("[BACK]") and "甲 已经上交公知" in o for o in self.say("提交公知 公知一")))
        out = self.say("提交公知 公知二")
        self.assertTrue(any(o.startswith("[BACK]") and "甲 修改了公知" in o and "公知二" in o for o in out), out)
        anns = [o for o in SENT if o.startswith("[ANN]")]                     # 公告群只有那一句，不带内容
        self.assertEqual(len(anns), 3, anns)
        self.assertTrue(all("我的二表" not in o and "公知一" not in o for o in anns))

    def test_message_monitor(self):
        """消息监听：官方机器人收到的（认出是谁的个人群、谁说的）+ QQ 小号在群号组听到的（按绑定的 QQ 认人）；关掉就不记。"""
        from changri.core import msgmon, qqpool
        from changri.core.qqlink import GroupMsg
        from changri.plugins import msgmon as mon_plugin
        msgmon.clear()
        self.say("今天天气不错")
        got = msgmon.since()
        self.assertEqual((got[-1]["src"], got[-1]["group"], got[-1]["who"], got[-1]["text"]), ("official", "甲 的个人群", "甲", "今天天气不错"))
        qqpool.add_rooms("B1", "100001", "A组")
        roles.bind_qq(roles.get_role_by_name("乙")["id"], "666666")
        mon_plugin._on_listened("B1", GroupMsg("9", "100001", "666666", "乙的昵称", "小号听到的", 1, "1"))
        from changri.core.db import use_bot
        with use_bot("B1"):                     # 小号那路在机器人自己的工作区里记（线上官方那路也是）
            x = msgmon.since()[-1]
        self.assertEqual((x["src"], x["who"], x["text"]), ("qq", "乙（QQ 666666）", "小号听到的"))
        self.assertIn("100001", x["group"])
        set_config("msgmon_enabled", "0")
        n = len(msgmon.since())
        self.say("关了以后说的")
        self.assertEqual(len(msgmon.since()), n)

    def test_end_room_no_announce(self):
        """结束私约（官方机器人的群）：公告群什么都不发；清场提醒只发后台群，没设后台群就不发。"""
        from changri.core import announce
        self.add_pool(2)
        announce.set_announce("B1", "ANN")
        self.say("约会 1400-1500 咖啡厅 乙")
        self.say("约会 1600-1700 咖啡厅 乙")
        r1, r2 = sorted(r["id"] for r in rooms.timeline(roles.get_role_by_name("甲")["id"]))
        out = self.say(f"结束约会 R{r1}")
        self.assertTrue(out, out)
        self.assertFalse(any(o.startswith("[ANN]") for o in out), out)
        announce.set_backstage("B1", "BACK")
        out = self.say(f"结束约会 R{r2}")
        self.assertFalse(any(o.startswith("[ANN]") for o in out), out)
        self.assertTrue(any(o.startswith("[BACK]") and "在等人退群" in o for o in out), out)

    def test_create_role_receipt(self):
        """建角色的回执：列出接下来能填/能改的东西，并说明名字写错要找管理员删。"""
        out = self.say("创建新角色 丁", member="M9", group="P9")[0]
        for word in ("创建好了", "提交公知", "提交二表", "换绑码", "我的", "找管理员"):
            self.assertIn(word, out)
        self.assertNotIn("提交公知", self.say("创建新角色 戊", member="M9", group="P9")[0])   # 失败时只回原因

    def test_admin_delete_role_completely(self):
        """后台彻底删除角色：TA 发起的房间整场删、群放回池子；别人发起的房间只拿掉 TA；QQ 绑定解除；之后能用同一个号重建。"""
        self.add_pool(2)
        self.say("约会 1400-1500 咖啡厅 乙")                                   # 甲发起
        self.say("约会 1600-1700 公园 甲/丙", member="M1", group="P1")         # 乙发起，甲参加
        jia = roles.get_role_by_name("甲")["id"]
        own, other = sorted(r["id"] for r in rooms.timeline(jia))
        roles.bind_qq(jia, "555555")
        pid = rooms.get(own)["pool_id"]
        res = roles.delete_role(jia)
        self.assertEqual((res["name"], res["rooms"], res["joined"], res["qqs"]), ("甲", 1, 1, 1))
        self.assertIsNone(rooms.get(own))
        self.assertIsNotNone(rooms.get(other))
        self.assertEqual({m["name"] for m in rooms.members(other)}, {"乙", "丙"})
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT state FROM pool_groups WHERE id=?", (pid,)).fetchone()[0], "cleaning")
            self.assertIsNone(c.execute("SELECT 1 FROM person_qq WHERE qq='555555'").fetchone())
            self.assertIsNone(c.execute("SELECT 1 FROM role_groups WHERE role_id=?", (jia,)).fetchone())
        self.assertIsNone(roles.delete_role(jia))
        self.assertIn("创建好了", self.say("创建新角色 甲", member="M0", group="P0")[0])     # 同一个人、同一个群可以重建

    def test_change_timeline_receipts(self):
        """回执写清原时间→新时间、还在等谁；拒绝时申请人和其他人都收到通知。"""
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙/丙")
        rid = rooms.timeline(roles.get_role_by_name("甲")["id"])[0]["id"]
        out = self.say("修改时间线 D1 1600-1700", member="M0", group="POOL0")
        self.assertIn("原来 D1 14:00-15:00", out[0])
        self.assertIn("乙、丙", out[0])
        out = self.say("同意时间线", member="M1", group="POOL0")
        self.assertTrue(any("还在等：丙" in o for o in out), out)
        out = self.say("拒绝时间线", member="M2", group="POOL0")
        self.assertTrue(any("时间线保持 D1 14:00-15:00" in o and "已拒绝 甲 的申请" in o for o in out), out)
        self.assertTrue(any(o.startswith("[P0]") and "拒绝了你" in o for o in out), out)       # 申请人
        self.assertTrue(any(o.startswith("[P1]") and "拒绝了 甲 " in o for o in out), out)     # 同意过的人也知道作废了
        self.assertEqual(rooms.get(rid)["start_hm"], "14:00")
        self.assertIn("没有待处理", self.say("同意时间线", member="M1", group="POOL0")[0])

    def test_change_timeline_from_personal_group(self):
        """在个人群带房间号也能改：申请推到对方的个人群，对方在自己的个人群带房间号同意就生效。"""
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        rid = rooms.timeline(roles.get_role_by_name("甲")["id"])[0]["id"]
        self.assertIn("房间号", self.say("修改时间线 D1 1600-1700")[0])                 # 个人群不带房间号：提示带上
        self.assertIn("不是这个房间", self.say(f"修改时间线 R{rid} D1 1600-1700", member="M2", group="P2")[0])
        out = self.say(f"修改时间线 R{rid} D1 1600-1700")
        self.assertTrue(any("已通知乙" in o for o in out), out)
        self.assertTrue(any(o.startswith("[P1]") and f"同意时间线 R{rid}" in o for o in out), out)    # 乙的个人群收到申请
        self.assertIn(f"同意时间线 R{rid}", self.say("我的待回", member="M1", group="P1")[0])
        out = self.say(f"同意时间线 R{rid}", member="M1", group="P1")
        self.assertTrue(any("时间线已改为 D1 16:00-17:00" in o for o in out), out)
        self.assertEqual(rooms.get(rid)["start_hm"], "16:00")

    def test_not_playing_blocked(self):
        self.say("结束季度", member="ADMIN", group="ADM")
        self.assertIn("不是进行中", self.say("约会 1400-1500 咖啡厅 乙")[0])

    # ---------- 群池 ----------
    def test_pool_admin_commands_and_conflict(self):
        self.assertIn("权限不足", self.say("设为群池 A组 111", group="X1")[0])
        self.assertIn("已放进备用群池", self.say("设为备用群池 A组 111", member="ADMIN", group="X1")[0])
        self.assertIn("空闲 1", self.say("查看备用群池", member="ADMIN", group="ADM")[0])
        # 个人群不能放进池
        pool.note_group("B1", "P0")
        r = pool.set_in_pool(pool.is_pool_group("B1", "P0")["id"], True)
        self.assertFalse(r[0])
        self.assertIn("已移出", self.say("取消群池", member="ADMIN", group="X1")[0])

    def test_set_backup_takes_group_from_qq(self):
        """「设为备用群 群号」：群号必填；原来在小号群号组里的群挪到官方机器人名下，小号不再管，也不会再分给房间。"""
        from changri.core import handover, qqpool
        with get_conn() as c:
            c.execute("DELETE FROM qq_groups")
        self.assertIn("用法", self.say("设为备用群", member="ADMIN", group="X1")[0])
        self.assertIn("权限不足", self.say("设为备用群 234567", group="X1")[0])
        self.assertEqual(qqpool.join_set("B1", "234567", "A组"), "")
        self.assertEqual(handover.owner("B1", "234567"), "qq")
        out = self.say("设为备用群 234567", member="ADMIN", group="X1")[0]
        self.assertIn("已设为官方机器人的备用群", out)
        self.assertIn("已经从那边移出", out)
        self.assertIn("请群主马上把群名改成「长日备用」", out)          # 测试里小号没连着，改不了名，要提醒群主
        self.assertEqual(handover.owner("B1", "234567"), "official")
        self.assertFalse(qqpool.in_pool("234567"))                       # 小号不再听这个群
        self.assertEqual(pool.is_pool_group("B1", "X1")["set_name"], "A组")  # 沿用原来的组名
        self.assertIn("现在归官方机器人管", qqpool.join_set("B1", "234567", "B组"))
        self.assertEqual(pool.counts()["free"], 1)                       # 两行只算一个空群
        self.say("约会 1400-1500 咖啡厅 乙")
        self.assertEqual(pool.get(rooms.get(1)["pool_id"])["group_openid"], "X1")
        self.assertIn("还在用", handover.check_to_qq("B1", "234567"))        # 占用中不能转回去

    def test_official_room_all_here(self):
        """官方机器人自己的房间群：参与者在群里说话就算到场，人齐了由官方机器人在群里发回执。"""
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        rid = rooms.get(1)["id"]
        self.say("（推门进来）", member="M0", group="POOL0")
        self.assertFalse(any("人都到齐了" in m for m in SENT))
        self.say("（坐下）", member="M1", group="POOL0")
        self.assertTrue(any(m.startswith("[POOL0]") and f"人都到齐了：R{rid}" in m for m in SENT), SENT[-3:])

    def test_official_group_to_qq(self):
        """「转为小号群」：一开始就是官方备用群的，小号在群里才转；转完以后后台按钮也能来回换。"""
        from changri.core import handover, qqmanager, qqpool
        with get_conn() as c:
            c.execute("DELETE FROM qq_groups"); c.execute("DELETE FROM qq_links")
        self.say("设为备用群 456789", member="ADMIN", group="X3")
        self.assertIn("权限不足", self.say("转为小号群", group="X3")[0])
        self.assertIn("还没有连 QQ 小号", self.say("转为小号群", member="ADMIN", group="X3")[0])
        lid, _ = qqpool.add_link("小号", "ws://127.0.0.1:3001")
        def _clean():
            with get_conn() as c:
                c.execute("DELETE FROM qq_groups"); c.execute("DELETE FROM qq_links")
        self.addCleanup(_clean)
        in_groups, renamed = [], []

        async def fake_list(app_id, link_id, max_age=60):
            return [{"group_id": g} for g in in_groups]

        async def fake_rename(app_id, gid, name):
            renamed.append((gid, name))
            return ""
        old = (qqmanager.group_list, qqmanager.rename)
        qqmanager.group_list, qqmanager.rename = fake_list, fake_rename
        self.addCleanup(lambda: (setattr(qqmanager, "group_list", old[0]), setattr(qqmanager, "rename", old[1])))
        self.assertIn("小号不在群 456789 里", self.say("转为小号群", member="ADMIN", group="X3")[0])
        self.assertEqual(handover.owner("B1", "456789"), "official")
        in_groups.append(456789)
        out = self.say("转为小号群 B组", member="ADMIN", group="X3")[0]
        self.assertIn("已交给 QQ 小号管（组「B组」）", out)
        self.assertIn("小号已把群名改成「长日备用」", out)
        self.assertEqual(renamed, [("456789", "长日备用")])
        self.assertEqual(handover.owner("B1", "456789"), "qq")
        self.assertTrue(qqpool.in_pool("456789"))
        self.assertTrue(pool.is_pool_group("B1", "X3")["moved"])          # 官方那行留着，以后能一键转回去
        self.assertIn("不是官方机器人的备用群", self.say("转为小号群", member="ADMIN", group="X3")[0])
        ok, why = handover.to_official("B1", "X3", "456789")             # 后台「转到官方备用群」
        self.assertTrue(ok, why)
        self.assertEqual(handover.owner("B1", "456789"), "official")
        self.assertIn("已交给 QQ 小号管（组「B组」）", self.say("转为小号群", member="ADMIN", group="X3")[0])   # 沿用原来的组

    def test_old_set_pool_with_gid_goes_through_handover(self):
        self.assertIn("已设为官方机器人的备用群", self.say("设为备用群池 A组 345678", member="ADMIN", group="X2")[0])
        self.assertEqual(pool.is_pool_group("B1", "X2")["real_gid"], "345678")

    def test_wipe_frees_pool(self):
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        self.assertEqual(pool.counts()["occupied"], 1)
        self.say("结束季度", member="ADMIN", group="ADM")
        self.say("收尾 确认", member="ADMIN", group="ADM")
        self.assertEqual(pool.counts()["free"], 1)
        with get_conn() as c:                                                    # 新季度的 R 号从 1 重新开始
            self.assertIsNone(c.execute("SELECT seq FROM sqlite_sequence WHERE name='rooms'").fetchone())
            c.execute("INSERT INTO rooms(kind, created_ts) VALUES('约会', 0)")
            self.assertEqual(c.execute("SELECT MAX(id) FROM rooms").fetchone()[0], 1)

    def test_expire(self):
        """到时间强制结群：默认关；打开后到点结束，文案是「结戏限时已到」；提前 4 小时 @ 参与者提醒收尾，只提醒一次。"""
        import asyncio
        from changri.core import qqmanager, qqpool
        from changri.plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        self.say("约会 1400-1500 咖啡厅 乙")
        roles.bind_qq(roles.get_role_by_name("甲")["id"], "555555")
        rid = rooms.open_rooms()[0]["id"]
        with get_conn() as c:
            c.execute("UPDATE rooms SET created_ts=created_ts-90000")
        self.assertEqual(rooms.expired(), [])                                    # 默认关：开再久也不结束
        set_config("room_expire_enabled", "1")
        self.assertEqual(len(rooms.expired()), 1)
        with get_conn() as c:
            c.execute("UPDATE rooms SET created_ts=?", (int(time.time()) - 21 * 3600,))    # 24 小时限时，还剩 3 小时
        self.assertEqual(rooms.expired(), [])
        said = []

        async def fake_say(app, gid, text, ats):
            said.append((gid, text, ats))
            return ""

        from changri.core import announce
        announce.set_announce("B1", "ANN")
        old_say = qqmanager.room_say
        qqmanager.room_say = fake_say
        try:
            asyncio.run(play._warn_expiring())
            asyncio.run(play._warn_expiring())                                 # 第二次不再提醒
            self.assertEqual(len(said), 1)
            self.assertEqual(said[0][0], "100001")
            self.assertIn("还有 4 小时", said[0][1])
            self.assertEqual(said[0][2], ["555555"])                           # @ 绑了 QQ 的参与者
            asyncio.run(play._finish_room(rid, "ended", "", timeout=True))
            self.assertIn("结戏限时已到~请及时离开！", said[-1][1])
            self.assertFalse(any(str(o).startswith("[ANN]") for o in SENT))      # 结束房间不往公告群发任何东西
        finally:
            qqmanager.room_say = old_say

    def test_qq_outage_moves_rooms_to_phone(self):
        """拉群 QQ 号掉线：通知所有玩家暂停 QQ 群、自动打开小手机并发激活码；掉线的房间在小手机上能写、记进同一份复盘；
        两个号都掉线时只通知一次；全部恢复后小手机关回去、通知大家回 QQ 群，小手机上又只能看。"""
        import asyncio
        import json
        from changri.core import outage, phonecodes, phoneserver, qqpool, roomlog
        from changri.core.db import get_config
        from changri.phone.app import create_phone_app
        from changri.plugins import qqwatch
        qqpool.add_rooms("B1", "100001", "A组")
        self.say("约会 1400-1500 咖啡厅 乙")
        rid = rooms.open_rooms()[0]["id"]
        set_config("phone_base_url", "http://9.9.9.9:5092")
        set_config("phone_net_last", json.dumps({"state": "ok"}))
        self.assertFalse(outage.enabled())                                       # 默认关
        set_config("qqdown_phone_fallback", "1")
        self.assertEqual(outage.ready(), [])
        notes = []

        async def fake_notify(text):
            notes.append(text)
        old_notify, old_start = qqwatch._notify, phoneserver.start
        qqwatch._notify, phoneserver.start = fake_notify, lambda *a: ""
        try:
            SENT.clear()
            asyncio.run(qqwatch._fallback_on(1))
            self.assertEqual(get_config("phone_enabled"), "1")                     # 自动打开了小手机
            to_players = [m for m in SENT if "掉线" in str(m)]
            self.assertEqual(len(to_players), 3)                                     # 甲乙丙都收到
            code = phonecodes.code_for_role(roles.get_role_by_name("甲")["id"])
            self.assertTrue(any(code in str(m) and "http://9.9.9.9:5092/p" in str(m) for m in to_players))
            self.assertIn("已自动打开小手机", notes[-1])
            self.assertTrue(outage.room_down(rooms.get(rid)))

            phone = create_phone_app("k" * 32).test_client()
            phone.get("/p")
            with phone.session_transaction() as sess:
                sess["csrf"] = "tok"
            phone.post("/p", data={"code": code, "_csrf": "tok"})
            self.assertIn("拉群用的 QQ 号掉线了", phone.get("/p/me").get_data(as_text=True))     # 页顶横幅
            page = phone.get(f"/p/me/room/{rid}").get_data(as_text=True)
            self.assertIn("先在这里接着写", page)
            r = phone.post(f"/p/me/room/{rid}/say", data={"text": "甲：在小手机上接着写", "_csrf": "tok"})
            self.assertTrue(r.get_json()["ok"], r.get_json())
            self.assertIn("甲：在小手机上接着写", [ln["text"] for ln in roomlog.lines(rid)])

            SENT.clear()
            asyncio.run(qqwatch._fallback_on(2))                                     # 第二个号也掉了：不再重复通知
            self.assertEqual([m for m in SENT if "掉线" in str(m)], [])
            asyncio.run(qqwatch._fallback_off(1))                                    # 只恢复了一个：还在小手机上
            self.assertTrue(outage.active())
            self.assertEqual(SENT, [])
            asyncio.run(qqwatch._fallback_off(2))
            self.assertFalse(outage.active())
            self.assertEqual(get_config("phone_enabled"), "0")                     # 自动开的，关回去
            self.assertEqual(len([m for m in SENT if "已经恢复" in str(m)]), 3)
            self.assertIn("已关回去", notes[-1])
            r = phone.post(f"/p/me/room/{rid}/say", data={"text": "甲：恢复后", "_csrf": "tok"})
            self.assertNotEqual(r.status_code, 200)                                  # 小手机关了 / 房间又回 QQ 群
        finally:
            qqwatch._notify, phoneserver.start = old_notify, old_start

    def test_backfill_in_official_group_from_quoted_forward(self):
        """卡拉在的房间群：回复合并转发发「补录 R号」，QQ 把引用的记录作为文字带过来（日志里抓到的真实格式）。
        已有的跳过，缺的按顺序插在已有发言之间；戏文开头是参与者名字的记在角色名下。"""
        from changri.core import roomlog
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        room = rooms.open_rooms()[0]
        rid, grp = room["id"], pool.get(room["pool_id"])["group_openid"]
        jia = roles.get_role_by_name("甲")["id"]
        with get_conn() as c:
            for ts, text in ((1000, "甲：你来了啊"), (2000, "甲：那走吧")):
                c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars, msg_id) VALUES(?,?,'',?,'甲',?,1,4,'')",
                          (rid, ts, jia, text))
        quoted = ("=== 消息 1 ===\n[消息内容] [群聊的聊天记录]\n[消息类型] 引用消息\n[关联消息]\n"
                  "--- 第1条 ---\n    [消息内容] 甲：你来了啊\n    [发送者] 阿甲\n"
                  "--- 第2条 ---\n    [消息内容] 乙：路上堵车\n    第二行\n    [发送者] 阿乙\n"
                  "--- 第3条 ---\n    [消息内容] 好吧\n    [发送者] 阿甲\n"
                  "--- 第4条 ---\n    [消息内容] 甲：那走吧\n    [发送者] 阿甲\n")
        extra = {"message_type": 103, "msg_elements": [{"content": quoted}]}          # 没有 message_type / msg_idx：适配器补丁后才收得到
        self.assertIn("用法", self.say("补录 R99", group=grp, **extra)[0])
        self.assertIn("只有", self.say(f"补录 R{rid}", member="M2", group=grp, **extra)[0])       # 丙不是参与者
        out = self.say(f"补录 R{rid}", group=grp, **extra)
        self.assertIn("补进 2 条", out[0])
        self.assertIn("2 条跳过", out[0])
        lines = roomlog.lines(rid)
        self.assertEqual([ln["text"] for ln in lines], ["甲：你来了啊", "乙：路上堵车\n第二行", "好吧", "甲：那走吧"])
        self.assertTrue(1000 < lines[1]["ts"] < lines[2]["ts"] < 2000)                 # 夹在两条已有的中间
        self.assertEqual(lines[1]["role_id"], roles.get_role_by_name("乙")["id"])        # 戏文写了「乙：」
        self.assertIn("补进 0 条", self.say(f"补录 R{rid}", group=grp, **extra)[0])      # 再补一次：全部跳过
        self.assertIn("没读到", self.say(f"补录 R{rid}", group=grp)[0])                   # 没回复转发

    def test_official_bot_records_room_lines(self):
        """卡拉在房间群里（大家开着「接收全部消息」）：群里的发言记进复盘，戏文按参与者名算字数；
        指令被别的功能接走、不记；别的群不记；房间结束后不记。"""
        from changri.core import roomlog
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        room = rooms.open_rooms()[0]
        rid, grp = room["id"], pool.get(room["pool_id"])["group_openid"]
        jia, yi = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        self.say("甲：你来了啊", group=grp)
        self.say("路上有点堵", member="M1", group=grp)
        self.say("乙 坐吧，喝点什么", member="M1", group=grp)
        self.say("随便聊聊")                                                      # 甲的个人群：不是房间群，不记
        self.say("我的待回", group=grp)                                           # 指令：被指令接走，不记
        self.say("乙：来了", member="M1", group=grp)
        ls = roomlog.lines(rid)
        self.assertEqual([ln["text"] for ln in ls], ["甲：你来了啊", "路上有点堵", "乙 坐吧，喝点什么", "乙：来了"])
        self.assertEqual([ln["role_id"] for ln in ls], [jia, yi, yi, yi])
        self.assertEqual([(ln["is_rp"], ln["chars"]) for ln in ls], [(1, 4), (0, 0), (1, 7), (1, 2)])
        self.assertTrue(ls[0]["qq"].startswith(roomlog.OFFICIAL))
        self.assertIn("甲：你来了啊", roomlog.review_text(rid))
        self.say("结束约会", group=grp)
        self.assertEqual(len(roomlog.lines(rid)), 4)                              # 「结束约会」不进复盘
        self.assertEqual({x["name"]: x["chars"] for x in roomlog.stats(rid) if x["rp"]}, {"甲": 4, "乙": 9})
        self.say("甲：结束后再说的", group=grp)                                   # 房间结束了：不记
        self.assertEqual(len(roomlog.lines(rid)), 4)

    def test_backfill_from_personal_group_for_qq_only_room(self):
        """补录只由卡拉做：小号的房间群卡拉不在，参与者在自己的群里回复合并转发发「补录 R号」就能补。"""
        from changri.core import qqpool, roomlog
        qqpool.add_rooms("B1", "100001", "A组")
        self.say("约会 1400-1500 咖啡厅 乙")
        rid = rooms.open_rooms()[0]["id"]
        quoted = "--- 第1条 ---\n    [消息内容] 甲：你来了啊\n    [发送者] 阿甲\n--- 第2条 ---\n    [消息内容] 乙：路上堵车\n    [发送者] 阿乙\n"
        extra = {"message_type": 103, "msg_elements": [{"content": quoted}]}
        self.assertIn("只有", self.say(f"补录 R{rid}", member="M2", group="P2", **extra)[0])         # 丙不是参与者
        self.assertIn("补进 2 条", self.say(f"补录 R{rid}", group="P0", **extra)[0])               # 甲在自己的群里补
        self.assertEqual([ln["text"] for ln in roomlog.lines(rid)], ["甲：你来了啊", "乙：路上堵车"])
        season.end()
        self.assertIn("没有进行中的季度", self.say(f"补录 R{rid}", group="P0", **extra)[0])      # 只补进进行中的季度

    # ---------- 申请加入 ----------
    def test_join_flow(self):
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        self.assertIn("找不到角色", self.say("申请加入 路人", member="M2", group="P2")[0])
        self.assertIn("没有可以加入", self.say("申请加入 丙", member="M2", group="P2")[0])
        self.assertIn("你已经在这个房间", self.say("申请加入 甲", member="M1", group="P1")[0])
        out = self.say("申请加入 甲 14:00", member="M2", group="P2")
        self.assertTrue(any("申请已发出" in o for o in out))
        self.assertTrue(any("[P0]" in o and "想加入" in o for o in out) and any("[P1]" in o and "想加入" in o for o in out))
        self.assertIn("已经申请过", self.say("申请加入 甲", member="M2", group="P2")[0])
        qid = rooms.pending_requests(roles.get_role_by_name("甲")["id"])[0]["id"]
        self.assertIn("加入申请", self.say("加入请求")[0]); self.assertIn("想加入", self.say("我的待回")[0])
        self.assertIn("不是发给你", self.say(f"同意加入 {qid}", member="M2", group="P2")[0])
        out = self.say(f"同意加入 {qid}")
        self.assertTrue(any("[P2]" in o and "同意了你加入" in o for o in out))
        self.assertTrue(any("[P2]" in o and "请申请入群" in o and "1234560" in o and "@" not in o for o in out))      # 群号单独一条纯文字
        self.assertEqual(len(rooms.members(rooms.timeline(roles.get_role_by_name("丙")["id"])[0]["id"])), 3)
        self.assertIn("没有这条", self.say(f"同意加入 {qid}")[0])

    def test_join_conflict_and_reject(self):
        self.add_pool(4)
        roles.create_role("丁", "B1", "P3", "M3")
        self.say("约会 1400-1500 咖啡厅 乙")
        self.say("约会 1430-1530 花园 丁", member="M2", group="P2")        # 丙 14:30-15:30 已有安排
        out = self.say("申请加入 甲", member="M2", group="P2")
        self.assertIn("已经有安排", out[0])
        self.say("约会 1000-1100 花园 乙", member="M0", group="P0")
        self.assertTrue(any("申请已发出" in o for o in self.say("申请加入 乙 10:00", member="M2", group="P2")))
        qid = rooms.pending_requests(roles.get_role_by_name("乙")["id"])[0]["id"]
        out = self.say(f"拒绝加入 {qid}", member="M1", group="P1")
        self.assertTrue(any("被拒绝" in o and "[P2]" in o for o in out))

    # ---------- 官电 / 计划官约 / 我的数量 / 删帖 ----------
    def test_official_phone_and_plans(self):
        self.add_pool(4)
        self.assertIn("权限不足", self.say("发起官电 2000-2100 乙/丙")[0])
        out = self.say("发起官电 2000-2100 乙/丙", member="ADMIN", group="ADM")
        self.assertTrue(any("官电已发起" in o for o in out) and any("[P1]" in o and "官电" in o for o in out))
        self.assertIn("还没有计划", self.say("计划官约", member="ADMIN", group="ADM")[0])
        self.say("计划官约 1000-1100 后山 乙/丙", member="ADMIN", group="ADM")
        self.say("计划官约 1000-1100 河边 甲/乙", member="ADMIN", group="ADM")       # 乙在 10:00 撞了
        self.assertIn("共 3 条", self.say("计划官约 1200-1300 河边 甲/乙", member="ADMIN", group="ADM")[0])
        out = "\n".join(self.say("执行官约", member="ADMIN", group="ADM"))
        self.assertIn("已发起 2 场", out); self.assertIn("失败 1 场", out)
        self.assertIn("河边", self.say("计划官约", member="ADMIN", group="ADM")[0])                                  # 失败的留着
        self.assertIn("已清空", self.say("计划官约 清空", member="ADMIN", group="ADM")[0])

    def test_my_count(self):
        set_config("limit_约会", "3")
        self.add_pool(1)
        self.say("约会 1400-1500 咖啡厅 乙")
        out = self.say("我的数量")[0]
        self.assertIn("约会 1/3", out); self.assertIn("电话 0（不限）", out)

    def test_forum_delete(self):
        from changri.core import forum
        self.say("发帖 路人甲 一楼内容")
        self.say("回复帖子 001 二楼内容", member="M1", group="P1")
        self.assertIn("权限不足", self.say("删楼层 001 2")[0])
        self.assertIn("已删除", self.say("删楼层 001 2", member="ADMIN", group="ADM")[0])
        view = self.say("查看帖子 001")[0]
        self.assertIn("该楼层已被删除", view); self.assertNotIn("二楼内容", view)
        self.assertIn("用法", self.say("删楼层 001 1", member="ADMIN", group="ADM")[0])
        self.assertIn("已删除帖子", self.say("删帖 001", member="ADMIN", group="ADM")[0])
        self.assertIn("没有这个帖子", self.say("查看帖子 001")[0])

    # ---------- 心愿 ----------
    def test_wishes(self):
        self.add_pool(1)
        self.assertIn("用法", self.say("挂心愿 1400-1500 花园")[0])
        self.assertIn("已挂出", self.say("挂心愿 1400-1500 花园 一起散步")[0])
        w1 = wishes.list_open()[0]["id"]
        self.assertIn("花园", self.say("看心愿", member="M1", group="P1")[0])
        self.assertIn("不能摘自己", self.say(f"摘心愿 {w1}")[0])
        out = self.say(f"摘心愿 {w1}", member="M1", group="P1")
        self.assertTrue(any("摘了心愿" in o for o in out) and any("[P0]" in o and "被 乙 摘走" in o for o in out))
        self.assertIn("没有开放", self.say("看心愿")[0])
        self.assertIn("已经被摘走", self.say(f"摘心愿 {w1}", member="M2", group="P2")[0])
        self.assertIn("14:00-15:00 心愿", self.say("时间线")[0])
        self.say("悬赏心愿 1600-1700 图书馆 陪我看书 | 滋补汤 1")
        self.assertIn("悬赏：滋补汤 1", self.say("看心愿")[0])
        self.assertIn("已撤回", self.say(f"撤心愿 {wishes.list_open()[0]['id']}")[0])
        self.assertIn("用法", self.say("悬赏心愿 1600-1700 图书馆 陪我看书")[0])

    def test_wish_limits(self):
        set_config("limit_挂心愿", "1")
        self.say("挂心愿 1000-1100 花园 散步")
        self.assertIn("次数用完", self.say("挂心愿 1200-1300 花园 散步")[0])

    # ---------- 论坛 ----------
    def test_forum(self):
        self.assertIn("贴号 001", self.say("发帖 路人甲 今天天气真好！")[0])
        self.assertIn("贴号 002", self.say("发帖 没署名的帖子")[0])
        self.assertIn("甲：没署名", self.say("查看帖子")[0])
        self.assertIn("你在 2 楼", self.say("回复帖子 001 你说得对！", member="M1", group="P1")[0])
        self.assertIn("你在 3 楼", self.say("回复帖子 001 引用2 同意", member="M2", group="P2")[0])
        self.assertIn("没有第 9 楼", self.say("回复帖子 001 引用9 嗯", member="M2", group="P2")[0])
        self.assertIn("你在 4 楼", self.say("回复帖子 001 路人乙 我也觉得", member="M1", group="P1")[0])
        view = self.say("查看帖子 001")[0]
        self.assertIn("路人乙", view); self.assertIn("引用 乙：你说得对", view)
        self.assertIn("👍1", self.say("点赞 001", member="M1", group="P1")[0])
        self.assertIn("已经投过", self.say("点赞 001", member="M1", group="P1")[0])
        self.assertIn("👎1", self.say("点踩 001", member="M1", group="P1")[0])      # 改票：赞变踩
        self.assertIn("2 楼：👍1", self.say("点赞楼层 001 2", member="M2", group="P2")[0])
        self.assertIn("直接用", self.say("点赞楼层 001 1")[0])
        self.assertIn("没有这个帖子", self.say("查看帖子 099")[0])
        self.assertIn("没有这个帖子", self.say("回复帖子 099 嗯")[0])

    def test_pending_providers(self):
        self.say("拉线 乙 小时候是邻居", member="M0", group="P0")
        out = self.say("我的待回", member="M1", group="P1")[0]
        self.assertIn("关系线", out)
        self.assertIn("没有要回", self.say("我的待回", member="M2", group="P2")[0])


if __name__ == "__main__":
    unittest.main()
