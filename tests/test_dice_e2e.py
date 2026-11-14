"""走真正的 NoneBot 事件分发：前缀、认角色、切换规则、属性、长内容发图。需要整个进程里只初始化一次 NoneBot。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from core import roles  # noqa: E402
from core.db import get_conn, get_config, init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    from core import extensions
    extensions.load_all()

from nonebot.message import handle_event  # noqa: E402

SENT: list = []


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(message)


class DiceE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        roles.create_role("甲", "B1", "G1", "M1")
        self.n = 0

    def say(self, text, member="M1", group="G1"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [m if isinstance(m, str) else "<图>" for m in SENT[before:]]

    def test_prefix_optional_by_default_and_required_when_set(self):
        for text in ("/rd d20", ".rd d20", "。rd d20", "rd d20", "r d6"):               # 默认带不带前缀都行
            self.assertTrue(self.say(text)[0].startswith(("D20 =", "D6 =")), text)
        self.assertEqual(self.say("hello"), [])
        set_config("require_prefix", "1")                                              # 设置里勾上「必须带前缀」
        for text in ("rd d20", "r d6", "at 你好", "en 路上", "hello"):
            self.assertEqual(self.say(text), [], text)
        for p in ("/", ".", "。"):
            self.assertTrue(self.say(f"{p}rd d20")[0].startswith("D20 ="))

    def test_attrs_flow_and_rule_switch(self):
        self.assertIn("已记下", self.say("/录属性 力量60 SAN55")[0])
        self.assertIn("力量　60", self.say("/属性")[0])
        self.assertIn("甲的力量60", self.say("/ra 力量")[0])
        self.assertIn("还没有「敏捷」", self.say("/ra 敏捷")[0])
        self.assertIn("取自甲的 SAN", self.say("/sc 1/1d6")[0])
        self.assertIn("DND5e", self.say("/切换规则 dnd")[0])
        self.assertIn("DC12", self.say("/ra 力量 12")[0])
        self.assertIn("COC 的规矩", self.say("/sc 1/1d6")[0])
        self.assertIn("第1组", self.say("/dnd")[0])
        self.assertIn("COC7", self.say("/切换规则 coc")[0])
        self.assertIn("这是 DND 的指令", self.say("/at 长剑 +5 15 1d8")[0])

    def test_rule_is_per_group(self):
        roles.create_role("乙", "B1", "G2", "M2")
        self.say("/切换规则 dnd")
        self.assertIn("COC7", self.say("/切换规则", group="G2")[0])
        self.assertIn("DND5e", self.say("/切换规则")[0])

    def test_setcoc_ti_li_and_hidden_roll(self):
        self.assertTrue(self.say("/setcoc"))                                       # 说明比较长，会发成图
        self.assertIn("房规 3", self.say("/setcoc 3")[0])
        self.assertIn("房规只有", self.say("/setcoc 9")[0])
        self.assertEqual(get_config("coc_house:B1|G1"), "3")
        self.assertEqual(get_config("coc_house:B1|G2"), "")                        # 每个群各记各的
        self.assertIn("临时疯狂", self.say("/ti")[0])
        self.assertIn("总结疯狂", self.say("/li 甲")[0])
        out = self.say("/rh d20")                                                  # 甲有个人群 G1，结果私发，群里只有一句
        self.assertIn("暗骰", out[0])
        self.assertNotIn("D20 =", "".join(out))
        self.assertIn("发不出去", out[0])                                          # 测试里没有真机器人，投递失败时结果不能露在群里
        from plugins import dice as dice_plugin
        sent = []

        async def fake_push(role_id, text, send, at=True):
            sent.append(text)
            return True, "G1"
        orig = dice_plugin.messaging.push_to_role
        dice_plugin.messaging.push_to_role = fake_push
        try:
            out = self.say("/rh d20")
            self.assertIn("已私下发给 TA", out[0])
            self.assertIn("D20 =", sent[0])
            self.assertNotIn("D20 =", "".join(out))
            self.say("/rah 力量 60")
            self.assertIn("暗骰结果", sent[-1])
            self.assertIn("检定", sent[-1])
        finally:
            dice_plugin.messaging.push_to_role = orig
        self.say("/切换规则 dnd")
        self.assertIn("COC 的东西", self.say("/ti")[0])

    def test_no_role_gets_hint_but_explicit_value_works(self):
        self.assertIn("绑定角色", self.say("/录属性 力量60", member="M9")[0])
        self.assertIn("检定：D100=", self.say("/ra 侦查 70", member="M9")[0])
        self.assertIn("绑定角色", self.say("/ra 力量", member="M9")[0])

    def test_switch_off(self):
        set_config("dice_enabled", "0")
        self.assertEqual(self.say("/rd d20"), [])

    def test_long_output_goes_as_image(self):
        out = self.say("/骰子帮助")
        self.assertTrue(out and all(x == "<图>" for x in out))


if __name__ == "__main__":
    unittest.main()


class TrpgE2E(unittest.TestCase):
    """跑团模式走真正的事件分发：不带前缀的指令、角色卡、log 记录、恋综指令被屏蔽。"""

    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("trpg_log_lines", "trpg_logs", "trpg_attr_history", "trpg_active", "trpg_card_attrs", "trpg_cards",
                      "trpg_identities", "trpg_players", "role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        roles.create_role("甲", "B1", "G9", "M1")
        from core import trpg
        trpg.set_mode("G1", "trpg")
        self.n = 100

    say = DiceE2E.say

    def test_flow(self):
        from core import trpglog
        self.assertIn("力量：60", self.say("st 力量60 理智55")[0])
        self.assertIn("力量60", self.say("ra 力量")[0])                                   # 骰子读当前角色卡
        self.assertEqual(self.say("我的角色"), [])                                         # 恋综指令在跑团群里不响应
        self.assertIn("开始记录", self.say("log new 测试")[0])
        self.say("大家好")
        self.say("（场外话）")
        self.say("ra 力量")
        self.assertIn("没结束的 log", self.say("log new 再来一个")[0])       # 管理指令不带前缀也生效，并且不进 log
        texts = [(l["kind"], l["text"]) for l in trpglog.lines(trpglog.current("G1")["id"])]
        self.assertEqual(texts[0], ("say", "大家好"))
        self.assertEqual(texts[1][0], "say")                                              # 「ra 力量」这条指令本身
        self.assertEqual(texts[2][0], "dice")                                             # 它的骰点结果，和上一条配对
        self.assertEqual(len(texts), 3)
        self.assertIn("已结束", self.say("log end")[0])
        set_config("require_prefix", "1")
        self.assertEqual(self.say("st 力量10"), [])                                       # 打开必须带前缀后，不带的不响应
        self.assertIn("力量", self.say("/st 力量10")[0])
