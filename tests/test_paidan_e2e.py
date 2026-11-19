"""排单群指令：走真正的 NoneBot 事件分发。绑定、下单、补图（带图 / 回复图）、锁定、发送、发送图包、我的额度、散单、定时任务。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "pde.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core.db import get_conn, get_config, init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    nonebot.load_plugin("changri.plugins.paidan")

from changri.core import qq_compat  # noqa: E402
qq_compat.apply()          # 机器人启动时也会打：回复消息的 msg_elements 可以没有 message_type / msg_idx

from nonebot.message import handle_event  # noqa: E402

from changri.core import paidan as pd  # noqa: E402
from changri.plugins import paidan as plug  # noqa: E402

REPLY: list = []          # 回复到发消息的那个群
GROUP: list = []          # 主动发到别的群：(群, 内容)
DM: list = []             # 私聊 owner


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        REPLY.append(str(message) if not isinstance(message, str) else message)

    async def send_to_group(self, group_openid, message=None, **kw):
        GROUP.append((group_openid, "<图>" if "file_image" in repr(message) or "image" in type(message).__name__.lower() else str(message or kw)))
        return type("R", (), {"id": "m1"})()

    async def send_to_c2c(self, openid, message=None, **kw):
        DM.append((openid, message))


def _img(n=1):
    return [{"content_type": "image/png", "url": f"http://img/{i}.png", "filename": "a.png", "height": 1, "width": 1, "size": 1} for i in range(n)]


class PaidanE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("pd_charges", "pd_codes", "pd_orders", "pd_quota_extends", "pd_quotas", "pd_slots",
                      "pd_clients", "pd_businesses", "pd_pauses", "pd_bind_codes", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("qq_buttons", "0")                     # 测纯文字回复
        from changri.core import extensions
        extensions.set_enabled("paidan", True)
        for x in (REPLY, GROUP, DM):
            x.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        self.n = 0
        self.today = pd.today()
        self.a, _ = pd.save_business("A", "溶图", 7, 2, "名称：\nQQ号：")
        self.cid, _ = pd.create_client("小明")
        pd.add_quota(self.cid, self.a, 3, pd.add_days(self.today, 30))
        self.patch = mock.patch.object(plug, "_fetch", side_effect=lambda urls: [b"x"] * len(urls))       # 自动变成 AsyncMock
        self.patch.start()
        self.say(f"绑定 {pd.new_bind_code('work', self.a)}", "OWNER", "G_WORK")
        self.say(f"绑定 {pd.new_bind_code('owner')}", "OWNER", "G_WORK")
        self.say(f"绑定 {pd.new_bind_code('client', self.cid)}", "MING", "G_MING")
        self.say(f"绑定 {pd.new_bind_code('sandan')}", "OWNER", "G_SAN")
        for x in (REPLY, GROUP, DM):
            x.clear()

    def tearDown(self):
        self.patch.stop()

    def say(self, text, member="MING", group="G_MING", attachments=None, quoted=None):
        self.n += 1
        data = {"id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-18T10:00:00+08:00",
                "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
                "group_id": group, "group_openid": group}
        if attachments:
            data["attachments"] = attachments
        if quoted:
            data["msg_elements"] = [{"content": "", "attachments": quoted}]
        before = len(REPLY)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, GroupMessageCreateEvent.model_validate(data)))
        return REPLY[before:]

    def order(self):
        out = self.say("【A下单】\n名称：猫猫\nQQ号：123456789")
        self.assertIn("下单成功，单号 A-0001", out[0])
        return pd.order_by_no("A-0001")

    def test_bind(self):
        self.assertEqual(pd.business_by_group("G_WORK")["id"], self.a)
        self.assertTrue(pd.is_owner("OWNER"))
        self.assertEqual(pd.client(self.cid)["member_openid"], "MING")
        self.assertTrue(pd.is_sandan_group("G_SAN"))
        self.assertEqual(self.say("绑定 ZZZZZZ"), [])                     # 不认得的码不回话（留给别的功能）

    def test_order_flow(self):
        out = self.say("下单")
        self.assertIn("【A下单】\n名称：\nQQ号：", out[0])
        self.assertIn("QQ号", self.say("【A下单】\n名称：猫猫")[0])           # 没写 QQ 号交不了
        o = self.order()
        self.assertEqual(o["qq"], "123456789")
        self.assertTrue(any(g == "G_WORK" and "新单 A-0001" in t for g, t in GROUP), GROUP)
        self.assertEqual(DM[0][0], "OWNER")
        self.assertTrue(DM[0][1].startswith("【123456789/小明 已排单-A-0001-DDL"), DM[0][1])
        self.say("【A下单】\n名称：狗狗\nQQ号：123456789")
        self.assertIn("单口满了", self.say("【A下单】\n名称：兔兔\nQQ号：123456789")[0])

    def test_upload_lock_deliver(self):
        o = self.order()
        GROUP.clear()
        self.assertIn("收到 2 张图", self.say("A-0001", attachments=_img(2))[0])
        self.assertEqual(sum(1 for g, t in GROUP if g == "G_WORK" and t == "<图>"), 2)
        self.assertIn("收到 1 张图", self.say("A-1", quoted=_img(1))[0])          # 回复图片写单号
        self.assertEqual(pd.order(o["id"])["images_in"], 3)
        self.assertEqual(self.say("A-0001", group="G_OTHER", attachments=_img(1)), [])   # 没绑定的群不理
        self.assertIn("已锁定", self.say("已上传所有图 A-0001")[0])
        self.assertTrue(any("图已传齐，共 3 张" in t for g, t in GROUP))
        self.assertIn("已锁定", self.say("A-0001", attachments=_img(1))[0])

        GROUP.clear()
        self.assertIn("只有 owner", self.say("发送 A-0001", "MING", "G_WORK", attachments=_img(1))[0])
        self.assertIn("没看到图", self.say("发送 A-0001", "OWNER", "G_WORK")[0])
        self.assertIn("已发送 1 张给 小明", self.say("发送 A-0001", "OWNER", "G_WORK", quoted=_img(1))[0])
        self.assertTrue(any(g == "G_MING" and t == "<图>" for g, t in GROUP))
        self.assertEqual(pd.order(o["id"])["images_out"], 1)
        pd.complete_order(o["id"], 1)
        self.assertIn("已出图", self.say("A-0001", attachments=_img(1))[0])
        self.assertIn("不能再发送", self.say("发送 A-0001", "OWNER", "G_WORK", attachments=_img(1))[0])

    def test_add_note(self):
        o = self.order()
        GROUP.clear()
        self.assertIn("已补充", self.say("补充 A-0001 背景换成蓝色")[0])
        self.assertIn("已补充", self.say("补充需求 A-1：再加一只猫")[0])
        content = pd.order(o["id"])["content"]
        self.assertIn("】背景换成蓝色", content)
        self.assertIn("】再加一只猫", content)
        self.assertTrue(any(g == "G_WORK" and "A-0001 · 小明 补充需求：" in t and "背景换成蓝色" in t and 'id="OWNER"' in t for g, t in GROUP), GROUP)
        self.assertIn("用法", self.say("补充 背景换成蓝色")[0])
        self.assertEqual(self.say("补充一下剧情", "X", "G_ROOM"), [])          # 别的群不接

    def test_mine_and_bundle(self):
        self.order()
        out = self.say("我的额度")[0]
        self.assertIn("溶图：共 3 张", out)
        self.assertIn("在排：A-0001", out)
        pd.set_client_gid(self.cid, "987654321")
        self.assertIn("已通知 owner", self.say("发送图包")[0])
        self.assertIn("群号：987654321", DM[-1][1])
        self.assertIn("已经通知过", self.say("发送图包")[0])

    def test_sandan(self):
        codes, _ = pd.gen_codes(self.a, 1, pd.add_days(self.today, 5))
        self.assertIn("兑换码", self.say("下单", "HUA", "G_SAN")[0])
        self.assertIn("兑换码", self.say("【A下单】\n名称：鸟\nQQ号：55556666", "HUA", "G_SAN")[0])
        out = self.say(f"【A下单】\n名称：鸟\nQQ号：55556666\n兑换码：{codes[0]}", "HUA", "G_SAN")
        self.assertIn("下单成功", out[0])
        self.assertIn("兑换码：有：" + codes[0], DM[-1][1])
        self.assertIn("已使用", self.say(f"【A下单】\n名称：鸟\nQQ号：55556666\n兑换码：{codes[0]}", "HUA", "G_SAN")[0])
        self.say("A-0001", "SOMEONE", "G_SAN", attachments=_img(1))             # 谁都能补
        GROUP.clear()
        self.say("发送 A-0001", "OWNER", "G_WORK", attachments=_img(1))
        self.assertTrue(any(g == "G_SAN" for g, t in GROUP))

    def test_no_conflict_outside_paidan_groups(self):
        """别的群里「下单」「发送」开头的普通发言（比如戏里的台词）不能被排单接走：
        排单的指令根本不匹配，后面的复盘记录、自定义回复照常收到。"""
        from nonebot.matcher import matchers
        seen = []
        probe = __import__("nonebot").on_message(priority=30, block=False)

        @probe.handle()
        async def _(event: GroupMessageCreateEvent):
            seen.append(event.get_plaintext().strip())
        try:
            for t in ("下单了一杯奶茶", "发送 A-0001", "我的额度好像不够", "已上传所有图 A-0001", "发送图包", "补充一下设定"):
                self.assertEqual(self.say(t, "X", "G_ROOM"), [], t)
            self.assertEqual(len(seen), 6, seen)
            seen.clear()
            self.say("发送 A-0001", "MING", "G_MING")                      # 客户群里的「发送」也不是排单的（只认工作群）
            self.assertEqual(len(seen), 1)
            from changri.core import extensions
            extensions.set_enabled("paidan", False)                       # 关掉：哪里都不响应
            seen.clear()
            self.assertEqual(self.say("下单"), [])
            self.assertEqual(self.say("我的额度"), [])
            self.assertEqual(len(seen), 2)
        finally:
            matchers[probe.priority].remove(probe)

    def test_paused_and_archived(self):
        pd.add_pause(self.today)
        self.assertIn("暂停", self.say("下单")[0])
        self.assertIn("暂停", self.say("【A下单】\n名称：猫\nQQ号：123456")[0])

    def test_tick(self):
        o = self.order()
        tomorrow = pd.add_days(self.today, 1)
        pd.add_pause(tomorrow)
        DM.clear()
        GROUP.clear()
        t0 = time.strptime(tomorrow + " 00:00", "%Y-%m-%d %H:%M")
        did = asyncio.new_event_loop().run_until_complete(plug.tick(self.bot, t0))
        self.assertTrue(any("生效" for d in did) and "暂停通知" in did, did)
        self.assertEqual(pd.order(o["id"])["deadline"], pd.add_days(self.today, 8))
        self.assertTrue(any(g == "G_MING" and "暂停一天" in t for g, t in GROUP))
        self.assertEqual(asyncio.new_event_loop().run_until_complete(plug.tick(self.bot, t0)), [])   # 不重复
        # 暂停那天 12 点：标记过了但不发提醒
        t12 = time.strptime(tomorrow + " 12:00", "%Y-%m-%d %H:%M")
        GROUP.clear()
        asyncio.new_event_loop().run_until_complete(plug.tick(self.bot, t12))
        self.assertEqual(GROUP, [])
        # DDL 当天 12 点：工作群催单 @owner
        ddl = pd.order(o["id"])["deadline"]
        t = time.strptime(ddl + " 12:01", "%Y-%m-%d %H:%M")
        did = asyncio.new_event_loop().run_until_complete(plug.tick(self.bot, t))
        self.assertIn("每日提醒", did)
        self.assertTrue(any(g == "G_WORK" and "今日到期：A-0001" in t for g, t in GROUP), GROUP)
        self.assertEqual(asyncio.new_event_loop().run_until_complete(plug.tick(self.bot, t)), [])     # 一天一次


if __name__ == "__main__":
    unittest.main()
