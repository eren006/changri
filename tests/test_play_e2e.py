"""核心玩法：房间（私约/电话/官约/微信）、群池、时间线、待回、心愿、论坛，走真正的 NoneBot 事件分发。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from core import admins, pool, roles, rooms, season, wishes  # noqa: E402
from core.db import get_conn, init_db, set_config  # noqa: E402
from tests._linkup import links_up  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    from core import extensions
    extensions.load_all()

nonebot.load_plugin("plugins.wishes")          # 心愿默认关，不在 load_all 里；测试里直接加载
from nonebot.message import handle_event  # noqa: E402

SENT: list = []


def rng(back=1, fwd=5):
    t = time.time()
    return time.strftime("%m%d", time.localtime(t - back * 86400)) + "-" + time.strftime("%m%d", time.localtime(t + fwd * 86400))


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(message)

    async def send_to_group(self, group_openid, message, **kw):
        SENT.append(f"[{group_openid}] {message}")


TABLES = ("votes", "join_requests", "replies", "posts", "wishes", "room_members", "rooms", "pool_groups", "seasons", "entrance", "role_groups", "roles", "admins", "admin_codes", "config", "blocks")


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

    def say(self, text, member="M0", group="P0"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [m if isinstance(m, str) else "<图>" for m in SENT[before:]]

    def add_pool(self, n=1, real="123456"):
        for i in range(n):
            pool.register("B1", f"POOL{i}", real_gid=f"{real}{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)

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
        from plugins import relations as rel
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
        """分群前查群成员：群里还有别的玩家（绑过 QQ 的人）就先放一边，房间排队；人退光后自动放回池再分给房间。"""
        import asyncio
        from core import qqmanager, qqpool, rooms
        from plugins import play
        qqpool.add_rooms("B1", "100001", "A组")
        roles.bind_qq(roles.get_role_by_name("丙")["id"], "5550009")      # 丙不在这个房间里，却还留在 100001 群
        members = {"100001": {"5550009"}}

        async def fake(app_id, gid):
            return members[gid]
        old, qqmanager.group_members = qqmanager.group_members, fake
        try:
            out = self.say("私约 1400-1500 咖啡厅 乙")
            self.assertFalse(any("请申请入群" in o and "100001" in o for o in out), out)     # 有人的群不能分给房间
            st = lambda: [g for g in pool.all_groups() if g["real_gid"] == "100001"][0]["state"]      # noqa: E731
            self.assertEqual(st(), "cleaning")
            members["100001"] = set()                                    # 人退光了
            asyncio.run(play._sweep_cleaning())
            self.assertEqual(st(), "free")
            got = rooms.assign_waiting()
            self.assertEqual(len(got), 1)
            asyncio.run(play._ensure_clean(got[0]))
            self.assertTrue(rooms.get(got[0])["pool_id"])
        finally:
            qqmanager.group_members = old

    def test_listener_room_commands_in_qq_group(self):
        """房间群没有卡拉：监听的 QQ 号听到「结束私约」「修改时间线」等，按绑定的 QQ 认人，由 QQ 号在群里回。"""
        from core import qqmanager, qqpool
        from core.qqlink import GroupMsg
        from plugins import play
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

    def test_queue_when_pool_empty_then_assign(self):
        out = self.say("电话 1400-1500 乙")
        self.assertTrue(any("排队" in o for o in out))
        self.assertEqual(pool.counts()["waiting"], 1)
        self.add_pool(1)
        for rid in rooms.assign_waiting():
            self.assertTrue(rooms.get(rid)["pool_id"])
        self.assertEqual(pool.counts()["waiting"], 0)

    def test_phone_cap_and_daily_limit(self):
        set_config("phone_open_cap", "1")
        self.say("电话 1400-1500 乙")
        self.assertIn("还没结束", self.say("电话 1600-1700 丙")[0])
        set_config("limit_私约", "1")
        self.say("私约 1000-1100 咖啡厅 丙")
        self.assertIn("次数用完", self.say("私约 1200-1300 咖啡厅 丙")[0])

    def test_wechat_no_timeline(self):
        self.say("微信 乙")
        self.say("微信 丙")
        self.assertIn("时间线还是空的", self.say("时间线")[0])
        self.assertTrue(any("微信已发起" in o for o in self.say("微信 乙")))      # 微信不查时间撞

    def test_block_respected(self):
        from core import blocks
        blocks.block(roles.get_role_by_name("乙")["id"], roles.get_role_by_name("甲")["id"], False)
        self.assertIn("已拒绝", self.say("电话 1400-1500 乙")[0])
        blocks.block(roles.get_role_by_name("丙")["id"], roles.get_role_by_name("甲")["id"], True)
        self.assertIn("发起失败", self.say("电话 1400-1500 丙")[0])

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
        self.assertIn("已放进群池", self.say("设为群池 A组 111", member="ADMIN", group="X1")[0])
        self.assertIn("空闲 1", self.say("查看群池", member="ADMIN", group="ADM")[0])
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
        self.add_pool(1)
        self.say("私约 1400-1500 咖啡厅 乙")
        with get_conn() as c:
            c.execute("UPDATE rooms SET created_ts=created_ts-90000")
        self.assertEqual(len(rooms.expired()), 1)

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
        self.add_pool(2)
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
        self.add_pool(2)
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
        from core import forum
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
