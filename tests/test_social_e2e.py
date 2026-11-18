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
        set_config("qq_buttons", "0")         # 老测试断言的是纯文字；按钮的路径在 test_buttons.py 里测
        self.n = 0

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def test_profile_edit(self):
        """修改性别/年龄/皮相/签名/简称：单条、多行一起改；默认皮相跟性别走；签名 12 小时冷却；简称不能撞别人的名字。"""
        self.assertIn("皮相：刘亦菲", self.say("我的角色")[0])
        out = self.say("修改性别 男")[0]
        self.assertIn("性别已改为：男", out)
        self.assertIn("皮相：亨利卡维尔", self.say("我的角色")[0])                 # 没自己填过皮相：跟着性别换默认
        self.assertIn("只能填", self.say("修改性别 其他")[0])
        out = self.say("修改年龄 20\n修改皮相 某明星\n修改简称 小甲")[0]
        self.assertIn("年龄已改为：20", out)
        self.assertIn("皮相已改为：某明星", out)
        self.assertIn("简称已设为「小甲」", out)
        self.assertIn("开头", self.say("修改年龄 21\n随便一行")[0])
        self.assertIn("签名已改为", self.say("修改签名 你好世界")[0])
        self.assertIn("12 小时", self.say("修改签名 再改")[0])
        self.assertIn("别人的名字", self.say("修改简称 乙")[0])
        self.assertIn("别人的名字", self.say("修改简称 小甲", member="MB", group="GB")[0])
        self.assertIn("只能是", self.say("修改简称 a b")[0])
        info = self.say("我的角色")[0]
        for w in ("男", "20", "某明星", "小甲", "你好世界"):
            self.assertIn(w, info)
        # 简称能代替本名：乙用简称给甲发短信
        out = self.say("短信 小甲 用简称找你", member="MB", group="GB")
        self.assertTrue(any("[GA]" in o and "用简称找你" in o for o in out), out)
        self.assertEqual(roles.get_role_by_name("小甲")["name"], "甲")
        ok, msg = roles.create_role("小甲", "B1", "GC", "MC")                     # 新角色不能用别人的简称当名字
        self.assertFalse(ok)
        self.assertIn("简称已清除", self.say("修改简称 甲")[0])
        self.assertIsNone(roles.get_role_by_name("小甲"))

    def test_ping_random_line(self):
        """「/ping 的回复」写好几行：每次随机回一行；空着回「收到。」"""
        set_config("hello_text", "在！\n\n  来了  \n")
        seen = {self.say("ping")[0] for _ in range(40)}
        self.assertEqual(seen, {"在！", "来了"})
        set_config("hello_text", "")
        self.assertEqual(self.say("ping")[0], "收到。")

    def test_look_wall_and_roster(self):
        """「查看皮相墙」「玩家名单」随时看最新的；改档案不往公告群发。"""
        from changri.core import announce
        announce.set_announce("B1", "ANN")
        out = self.say("修改皮相 某明星\n修改年龄 30")
        self.assertFalse(any(o.startswith("[ANN]") for o in out))
        self.say("修改性别 男\n修改简称 小乙", member="MB", group="GB")
        wall = self.say("查看皮相墙")[0]
        self.assertLess(wall.index("男生"), wall.index("乙｜亨利卡维尔"))
        self.assertLess(wall.index("女生"), wall.index("甲｜某明星"))
        self.assertIn("⬜ 甲", wall)
        names = self.say("玩家名单")[0]
        self.assertIn("小乙（全名：乙）", names)
        self.assertIn("女 / 30岁 / 皮相：某明星", names)

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
