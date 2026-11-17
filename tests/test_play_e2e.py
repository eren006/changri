"""核心玩法：房间（私约/电话/官约/微信）、群池、时间线、待回、心愿、论坛，走真正的 NoneBot 事件分发。"""
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
        self.assertIn("用法", self.say("私约 1400-1500")[0])
        self.assertIn("找不到角色", self.say("私约 1400-1500 咖啡厅 路人")[0])
        self.assertIn("时间格式", self.say("私约 下午 咖啡厅 乙")[0])
        self.assertIn("不能约自己", self.say("私约 1400-1500 咖啡厅 甲")[0])
        out = self.say("私约 1400-1500 咖啡厅 乙")
        self.assertTrue(any("已发起" in o and "R" in o and "1234560" in o for o in out))
        self.assertTrue(any("[P1]" in o and "向你发起了私约" in o for o in out))          # 对方个人群收到通知
        self.assertTrue(any("[POOL0]" in o and "参与者" in o for o in out))               # 房间群里发了说明
        # 时间撞了
        self.assertIn("撞了", self.say("私约 1430-1530 花园 丙")[0])
        # 时间线
        self.assertIn("14:00-15:00 私约 @咖啡厅 与 乙", self.say("时间线")[0])
        self.assertIn("只有管理员", self.say("时间线 乙")[0])
        self.assertIn("乙 的时间线", self.say("时间线 乙", member="ADMIN", group="ADM")[0])
        self.assertIn("找不到角色", self.say("删除时间线 路人 R1", member="ADMIN", group="ADM")[0])
        # 待回：还没进群
        self.assertIn("还没进群", self.say("我的待回")[0])
        # 在房间群里说话 = 到场
        self.say("大家好", member="M0", group="POOL0")
        self.assertNotIn("还没进群", self.say("我的待回")[0])
        # 结束
        out = self.say("结束私约", member="M1", group="POOL0")
        self.assertTrue(any("已经结束" in o and "请退群" in o for o in out))
        self.assertFalse(any("[P0]" in o and "已结束" in o for o in out))                       # 正常结束不再往个人群发通知
        self.assertEqual(pool.counts()["cleaning"], 0)                                           # 结束后群直接放回池
        self.assertEqual(pool.counts()["free"], 1)

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
            out = self.say("私约 1400-1500 咖啡厅 乙")
            self.assertFalse(any("请申请入群" in o and "100001" in o for o in out), out)     # 有人的群不能分给房间
            self.assertTrue(any("备用群池暂时没有空闲的群" in o for o in out), out)            # 当没有空群：不排队
            self.assertEqual(pool.counts()["waiting"], 0)
            st = lambda: [g for g in pool.all_groups() if g["real_gid"] == "100001"][0]["state"]      # noqa: E731
            self.assertEqual(st(), "cleaning")
            members["100001"] = set()                                    # 人退光了
            asyncio.run(play._sweep_cleaning())
            self.assertEqual(st(), "free")
            out = self.say("私约 1400-1500 咖啡厅 乙")
            self.assertTrue(any("请申请入群" in o and "100001" in o for o in out), out)
        finally:
            qqmanager.group_members = old

    def test_listener_room_commands_in_qq_group(self):
        """房间群没有卡拉：监听的 QQ 号听到「结束私约」「修改时间线」等，按绑定的 QQ 认人，由 QQ 号在群里回。"""
        from changri.core import qqmanager, qqpool
        from changri.core.qqlink import GroupMsg
        from changri.plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        out = self.say("私约 1400-1500 咖啡厅 乙")
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
            self.assertTrue(run("结束私约", "999999"))                               # 没绑定：提示先绑定
            self.assertIn("绑定QQ", said[-1][1])
            self.assertTrue(run("修改时间线 D1 1600-1700", "555555"))
            self.assertIn("申请把时间线改成", said[-1][1])
            self.assertTrue(run("结束私约", "555555"))
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
        set_config("limit_私约", "1")
        self.say("私约 1000-1100 咖啡厅 丙")
        self.assertIn("次数用完", self.say("私约 1200-1300 咖啡厅 丙")[0])

    def test_stakeout_solo_room(self):
        self.add_pool(1)
        self.assertIn("没有开放", self.say("踩点 1400-1500 大图书馆")[0])          # 默认关
        set_config("stakeout_enabled", "1")
        self.assertIn("用法", self.say("踩点 1400-1500")[0])
        out = self.say("踩点 1400-1500 大图书馆")
        self.assertTrue(any("踩点已发起" in o and "只有你一个人" in o and "1234560" in o for o in out))
        rid = rooms.open_rooms()[0]["id"]
        self.assertEqual([m["name"] for m in rooms.members(rid)], ["甲"])
        self.assertIn("撞了", self.say("私约 1430-1530 咖啡厅 乙")[0])            # 踩点占时间线
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
        out = self.say("私约 1400-1500 咖啡厅 乙/丙")
        self.assertTrue(any("私约已发起" in o for o in out), out)
        self.assertTrue(any("乙 已拒绝你的联系" in o for o in out), out)
        room = rooms.get(1)
        self.assertEqual({m["role_id"] for m in rooms.members(1)}, {a, c})            # 乙没进房间
        blocks.block(c, a, True)
        out = self.say("私约 1600-1700 咖啡厅 乙/丙")
        self.assertTrue(any("邀约对象都不可用" in o for o in out), out)                  # 两个都拉黑了：失败，不建群
        self.assertEqual(len(rooms.open_rooms()), 1)
        with get_conn() as cn:                                                        # 刚拉黑的要满 2 小时才能解除，测试里直接删
            cn.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (b, a))
        out = self.say("私约 1600-1700 咖啡厅 乙/丙")
        self.assertTrue(any("私约已发起" in o for o in out), out)
        self.assertFalse(any("已拒绝" in o for o in out), out)                        # 丙静默拉黑：悄悄剔掉，不留痕迹
        self.assertEqual({m["role_id"] for m in rooms.members(2)}, {a, b})
        self.assertIn("发起失败", self.say("私约 1800-1900 咖啡厅 丙")[0])              # 只邀丙一个人：单人邀约，直接失败

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
        self.say("私约 1400-1500 咖啡厅 乙")
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

    def test_not_playing_blocked(self):
        self.say("结束季度", member="ADMIN", group="ADM")
        self.assertIn("不是进行中", self.say("私约 1400-1500 咖啡厅 乙")[0])

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

    def test_wipe_frees_pool(self):
        self.add_pool(1)
        self.say("私约 1400-1500 咖啡厅 乙")
        self.assertEqual(pool.counts()["occupied"], 1)
        self.say("结束季度", member="ADMIN", group="ADM")
        self.say("收尾 确认", member="ADMIN", group="ADM")
        self.assertEqual(pool.counts()["free"], 1)

    def test_expire(self):
        """到时间强制结群：默认关；打开后到点结束，文案是「结戏限时已到」；提前 4 小时 @ 参与者提醒收尾，只提醒一次。"""
        import asyncio
        from changri.core import qqmanager, qqpool
        from changri.plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        self.say("私约 1400-1500 咖啡厅 乙")
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
        said, told = [], []

        async def fake_say(app, gid, text, ats):
            said.append((gid, text, ats))
            return ""

        async def fake_announce(text):
            told.append(text)
        old_say, old_ann = qqmanager.room_say, play._tell_announce
        qqmanager.room_say, play._tell_announce = fake_say, fake_announce
        try:
            asyncio.run(play._warn_expiring())
            asyncio.run(play._warn_expiring())                                 # 第二次不再提醒
            self.assertEqual(len(said), 1)
            self.assertEqual(said[0][0], "100001")
            self.assertIn("还有 4 小时", said[0][1])
            self.assertEqual(said[0][2], ["555555"])                           # @ 绑了 QQ 的参与者
            asyncio.run(play._finish_room(rid, "ended", "", timeout=True))
            self.assertIn("结戏限时已到~请及时离开！", said[-1][1])
            self.assertEqual(told[-1], f"R{rid}已经到了自动结戏时间，群号 100001 已放回群池，请退群！")
        finally:
            qqmanager.room_say, play._tell_announce = old_say, old_ann

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
        self.say("私约 1400-1500 咖啡厅 乙")
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

    def test_backfill_from_merged_forward(self):
        """合并转发补录：回复转发发「补录 R号」，已有的跳过、缺的按原时间插回去；只有参与者能补；结束了的房间也能补；关掉后不处理。"""
        import asyncio
        from changri.core import qqmanager, qqpool, roomlog
        from changri.core.qqlink import GroupMsg
        from changri.plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        self.say("私约 1400-1500 咖啡厅 乙")
        room = rooms.open_rooms()[0]
        rid, t0 = room["id"], int(room["created_ts"])
        roles.bind_qq(roles.get_role_by_name("甲")["id"], "555555")
        roles.bind_qq(roles.get_role_by_name("乙")["id"], "666666")
        with get_conn() as c:                                                        # QQ 号掉线前记下来的一句
            c.execute("INSERT INTO room_lines(room_id, ts, qq, role_id, name, text, is_rp, chars, msg_id) VALUES(?,?,?,?,?,?,1,4,'')",
                      (rid, t0 + 60, "555555", roles.get_role_by_name("甲")["id"], "甲", "甲：你来了啊"))
        forward = [{"ts": t0 + 60, "qq": "555555", "nickname": "阿甲", "text": "甲：你来了啊"},       # 已经有：跳过
                   {"ts": t0 + 120, "qq": "666666", "nickname": "阿乙", "text": "乙：路上堵车"},
                   {"ts": t0 + 90, "qq": "555555", "nickname": "阿甲", "text": "甲 坐吧"},
                   {"ts": t0 - 9999, "qq": "666666", "nickname": "阿乙", "text": "很久以前的别的戏"},   # 比房间还早：不算
                   {"ts": t0 + 999999, "qq": "666666", "nickname": "阿乙", "text": "以后别的场次在这个群说的"}]
        said, asked = [], []

        async def fake_say(app, gid, text, ats):
            said.append(text)
            return ""

        async def fake_read(app, gid, reply_id="", forward_id=""):
            asked.append((reply_id, forward_id))
            return list(forward), ""
        old = qqmanager.room_say, qqmanager.read_forward
        qqmanager.room_say, qqmanager.read_forward = fake_say, fake_read
        run = lambda text, uid, **kw: asyncio.new_event_loop().run_until_complete(play._listen_backfill("B1", GroupMsg("9", "100001", uid, "x", text, 1, "7", **kw)))   # noqa: E731
        try:
            self.assertFalse(run("随便聊聊", "555555"))
            self.assertTrue(run("补录 R" + str(rid), "999999", reply_id="42"))
            self.assertIn("只有", said[-1])                                          # 不是参与者
            self.assertTrue(run("补录", "666666"))                                   # 没回复、也没刚转发过
            self.assertIn("先把聊天记录合并转发", said[-1])
            self.assertTrue(run("", "666666", forward_ids=["F1"]))                  # 刚转发了一条（本身不进复盘）
            self.assertTrue(run(f"补录 R{rid}", "666666"))
            self.assertEqual(asked[-1], ("", "F1"))
            self.assertIn("补进 2 条", said[-1])
            self.assertIn("1 条跳过", said[-1])
            texts = [ln["text"] for ln in roomlog.lines(rid)]
            self.assertEqual(texts, ["甲：你来了啊", "甲 坐吧", "乙：路上堵车"])     # 按时间排好
            lines = roomlog.lines(rid)
            self.assertEqual(lines[2]["name"], "乙")                                  # 认出是参与者，记在角色名下
            self.assertTrue(lines[2]["is_rp"])
            self.assertTrue(run(f"补录 R{rid}", "555555", reply_id="42"))           # 再补一次：全部跳过
            self.assertEqual(asked[-1], ("42", ""))
            self.assertIn("补进 0 条", said[-1])
            self.assertTrue(run(f"@阿乙 补录 R{rid}", "555555", reply_id="42"))     # QQ 里「回复」会自动在开头 @ 原发送人
            self.assertIn("补录好了", said[-1])
            asyncio.run(play._finish_room(rid, "ended", ""))
            self.assertTrue(run(f"补录 R{rid}", "555555", reply_id="42"))           # 结束了也能补
            self.assertIn("补录好了", said[-1])
            self.assertNotIn("以后别的场次在这个群说的", [ln["text"] for ln in roomlog.lines(rid)])   # 房间结束后很久的：别的场次，不补
            set_config("qq_backfill_enabled", "0")
            self.assertTrue(run(f"补录 R{rid}", "555555", reply_id="42"))
            self.assertIn("没有打开", said[-1])
        finally:
            qqmanager.room_say, qqmanager.read_forward = old

    def test_backfill_in_official_group_from_quoted_forward(self):
        """卡拉在的房间群：回复合并转发发「补录 R号」，QQ 把引用的记录作为文字带过来（日志里抓到的真实格式）。
        已有的跳过，缺的按顺序插在已有发言之间；戏文开头是参与者名字的记在角色名下。"""
        from changri.core import roomlog
        self.add_pool(1)
        self.say("私约 1400-1500 咖啡厅 乙")
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

    # ---------- 申请加入 ----------
    def test_join_flow(self):
        self.add_pool(1)
        self.say("私约 1400-1500 咖啡厅 乙")
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
        self.say("私约 1400-1500 咖啡厅 乙")
        self.say("私约 1430-1530 花园 丁", member="M2", group="P2")        # 丙 14:30-15:30 已有安排
        out = self.say("申请加入 甲", member="M2", group="P2")
        self.assertIn("已经有安排", out[0])
        self.say("私约 1000-1100 花园 乙", member="M0", group="P0")
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
        set_config("limit_私约", "3")
        self.add_pool(1)
        self.say("私约 1400-1500 咖啡厅 乙")
        out = self.say("我的数量")[0]
        self.assertIn("私约 1/3", out); self.assertIn("电话 0（不限）", out)

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
