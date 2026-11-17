"""短信、送礼、撤回、礼品店、图鉴、欢迎语，走真正的 NoneBot 事件分发。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupAddRobotEvent, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import admins, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    from changri.core import extensions
    extensions.load_all()

nonebot.load_plugin("changri.plugins.giftshop")      # 礼品店是可选扩展，这里要它的指令加载进来（别的测试先初始化过也一样）

from nonebot.message import handle_event  # noqa: E402

SENT: list = []
DELETED: list = []


class FakeBot(QBot):
    n = 0
    fail_delete = False

    async def send(self, event, message, **kw):
        SENT.append(str(message))

    async def send_to_group(self, group_openid, message, **kw):
        FakeBot.n += 1
        SENT.append(f"[{group_openid}] {message}")
        return SimpleNamespace(id=f"M{FakeBot.n}")

    async def _request(self, request):
        if FakeBot.fail_delete:
            raise RuntimeError("超过时限")
        DELETED.append(str(request.url))


class SocialE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("gift_shop", "seasons", "entrance", "messages", "blocks", "role_attrs", "role_groups", "roles", "admins", "admin_codes", "config"):
                c.execute(f"DELETE FROM {t}")
        SENT.clear(); DELETED.clear()
        FakeBot.fail_delete = False
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        nonebot.get_bots()["B1"] = self.bot
        roles.create_role("甲", "B1", "GA", "MA")
        roles.create_role("乙", "B1", "GB", "MB")
        set_config("sms_cooldown_sec", "0"); set_config("gift_cooldown_sec", "0")
        self.n = 0

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def say(self, text, member="MA", group="GA"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [m for m in SENT[before:]]

    def test_sms_forms(self):
        out = self.say("短信 乙 你好呀")
        self.assertTrue(any("[GB]" in o and "甲 给你发来一条短信" in o and "你好呀" in o for o in out))
        self.assertTrue(any("已送达「乙」" in o and "撤回" in o for o in out))
        out = self.say("【短信】\n对象：乙\n内容：第一行\n第二行")
        self.assertTrue(any("[GB]" in o and "第一行\n第二行" in o for o in out))
        out = self.say("/短信 乙 带前缀")
        self.assertTrue(any("[GB]" in o and "带前缀" in o for o in out))
        out = self.say("甲短信 乙 署名就是自己")
        self.assertTrue(any("[GB]" in o and "甲 给你发来" in o for o in out))
        self.assertIn("没有叫「丙」", self.say("短信 丙 你好")[0])
        self.assertIn("不能发给自己", self.say("短信 甲 你好")[0])
        self.assertIn("短信 收信人 内容", self.say("短信")[0])                  # 用法提示
        self.assertEqual(self.say("我昨天发短信给你了"), [])                     # 聊天里碰巧带了字，不理
        self.assertEqual(self.say("神秘人短信 乙 你好"), [])                      # 没开放自定义署名，不理

    def test_custom_sign(self):
        set_config("allow_custom_sign", "1")
        out = self.say("神秘人短信 乙 你猜我是谁")
        self.assertTrue(any("[GB]" in o and "神秘人 给你发来一条短信" in o for o in out))
        out = self.say("送礼 乙 玫瑰\n".replace("\n", ""))
        self.assertTrue(any("[GB]" in o and "甲 送了你「玫瑰」" in o for o in out))

    def test_gift_forms_and_album(self):
        self.say("送礼 乙 一束玫瑰 生日快乐")
        self.say("【送礼】\n对象：乙\n礼物：一束玫瑰")
        out = self.say("送礼物 乙 巧克力")
        self.assertTrue(any("巧克力" in o for o in out))
        self.assertIn("送礼 对象 礼物", self.say("送礼 乙")[0])
        album = self.say("我的图鉴", member="MB", group="GB")
        self.assertTrue(any("一束玫瑰 ×2" in o and "巧克力 ×1" in o for o in album))
        self.assertIn("还是空的", self.say("我的图鉴")[0])

    def test_catalog_and_only_preset(self):
        self.assertIn("还没有上架", self.say("礼品店")[0])
        set_config("gift_catalog", "玫瑰、巧克力")           # 升级前的老格式：第一次用时搬进礼品店表，编号 1、2
        shop = self.say("礼品店")[0]
        self.assertIn("1. 玫瑰", shop)
        self.assertIn("2. 巧克力", shop)
        self.assertTrue(any("[GB]" in o for o in self.say("送礼 乙 随便什么")))
        self.assertTrue(any("玫瑰" in o and "[GB]" in o for o in self.say("送礼 乙 1")))      # 写编号 = 送对应的礼物
        set_config("gift_only_preset", "1")
        self.assertIn("只能送礼品店里的礼物", self.say("送礼 乙 随便什么")[0])
        self.assertIn("只能送礼品店里的礼物", self.say("送礼 乙 玫瑰")[0])                    # 开了「只能送礼品店」：名字也不行，只认编号
        self.assertIn("没有 9 号", self.say("送礼 乙 9")[0])
        self.assertTrue(any("巧克力" in o and "[GB]" in o for o in self.say("送礼 乙 2")))

    def test_daily_limit_and_recall_refund(self):
        set_config("sms_daily_limit", "2")
        self.say("短信 乙 一")
        self.say("短信 乙 二")
        self.assertIn("次数用完了（2/2）", self.say("短信 乙 三")[0])
        self.assertIn("短信 2/2", self.say("我的数量")[0])
        out = self.say("撤回")
        self.assertIn("已撤回发给「乙」的短信", out[0])
        self.assertEqual(len(DELETED), 1)
        self.assertIn("/v2/groups/GB/messages/", DELETED[0])
        self.assertTrue(any("[GB]" in o for o in self.say("短信 乙 三")))         # 次数返还，能重发

    def test_recall_failures(self):
        self.assertIn("没有可以撤回的", self.say("撤回")[0])
        self.say("短信 乙 你好")
        FakeBot.fail_delete = True
        self.assertIn("撤回失败", self.say("撤回")[0])
        FakeBot.fail_delete = False
        self.assertIn("已撤回", self.say("撤回")[0])                              # 失败没有误标成已撤回
        with get_conn() as c:                                                      # 超过 2 分钟不能撤回
            c.execute("UPDATE messages SET delivered_ts=?", (int(time.time()) - 300,))
        self.say("短信 乙 又一条")
        with get_conn() as c:
            c.execute("UPDATE messages SET delivered_ts=? WHERE content='又一条'", (int(time.time()) - 300,))
        self.assertIn("没有可以撤回的", self.say("撤回")[0])

    def test_format_templates(self):
        self.assertIn("【短信】", self.say("格式短信")[0])
        self.assertIn("【送礼】", self.say("格式送礼")[0])
        self.assertIn("撤回", self.say("格式撤回")[0])
        self.assertIn("格式短信", self.say("格式")[0])

    def test_welcome_and_unclaimed(self):
        ev = GroupAddRobotEvent.model_validate({"id": "x", "timestamp": "2026-11-13T00:00:00+08:00", "group_openid": "NEWG", "op_member_openid": "MA"})
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        self.assertTrue(any("[NEWG]" in o and "创建新角色" in o and "认群" in o for o in SENT))
        admins.claim(admins.make_code(), "B1", "ADMIN")


if __name__ == "__main__":
    unittest.main()
