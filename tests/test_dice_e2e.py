"""走真正的 NoneBot 事件分发：前缀、认角色、切换规则、属性、长内容发图。需要整个进程里只初始化一次 NoneBot。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent, MessageSegment  # noqa: E402
from nonebot.adapters.qq.exception import ActionFailed  # noqa: E402
from nonebot.drivers import Response  # noqa: E402
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
        set_config("dice_card", "0")        # 老测试断言的是纯文字；卡片的路径在 CardE2E 里测
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
        self.assertIn("已写回", self.say("/sc 1/1d6")[0])
        self.assertIn("DND5e", self.say("/切换规则 dnd")[0])
        self.assertIn("DC12", self.say("/ra 力量 12")[0])
        self.assertIn("COC 的规矩", self.say("/sc 1/1d6")[0])

    def test_sanity_and_growth_write_back(self):
        self.say("/录属性 SAN60 侦查5")
        out = self.say("/sc 5/5")[0]
        self.assertIn("已写回：甲的理智 60 → 55", out)
        self.assertIn("智力检定", out)                                          # 单次掉 5 点：提醒智力检定
        self.assertIn("SAN　55", self.say("/属性")[0])
        self.assertNotIn("已写回", self.say("/sc 1/1 40")[0])                   # 自己写的理智数值：不动角色
        self.assertIn("SAN　55", self.say("/属性")[0])
        with mock.patch("core.dice.random.randint", lambda a, b: b):          # D100 掷 100、成长 +10
            out = self.say("/en 侦查")[0]
        self.assertIn("已写回：甲的侦查 5 → 15", out)
        self.assertIn("侦查　15", self.say("/属性")[0])
        self.assertIn("还没有「游泳」", self.say("/en 游泳")[0])                  # 读不到就提示，不报格式错
        self.assertIn("格式", self.say("/en")[0])

    def test_attr_alias_and_modifier_via_command(self):
        self.say("/录属性 力量60")
        self.assertIn("甲的STR60", self.say("/ra STR")[0])                       # 恋综角色存的是「力量」，写 STR 也读得到
        self.assertIn("甲的力量60+10", self.say("/ra 力量+10")[0])
        self.assertIn("连续检定 ×3", self.say("/ra 3#力量")[0])

    def test_rav_and_initiative_persist(self):
        out = self.say("/rav 力量 60 vs 力量 50")[0]
        self.assertIn("对抗检定", out)
        self.assertRegex(out, "胜出|平局")
        self.say("/切换规则 dnd")
        self.assertIn("COC 的指令", self.say("/rav 力量 60 vs 力量 50")[0])
        self.assertIn("阿尔", self.say("/ri 阿尔 +2")[0])
        self.assertIn("阿尔", get_config("initiative:B1|G1", ""))              # 先攻表落库，重启不丢
        out = self.say("/ri 贝塔")[0]
        self.assertTrue("阿尔" in out and "贝塔" in out)
        self.assertIn("清掉", self.say("/ri 清空")[0])
        self.assertIn("空的", self.say("/ri")[0])
        self.assertIn("vs DC15", self.say("/ra 优势 劣势 运动+2 15")[0])         # DND 里优势劣势抵消、修正连写
        self.assertNotIn("（优势", self.say("/ra 优势 劣势 运动+2 15")[0])
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
        self.assertIn("恐惧症状：1D100=", self.say("/恐惧症状")[0])
        self.assertIn("狂躁症状：1D100=", self.say("/狂躁症状")[0])
        self.assertIn("总结疯狂", self.say("/li 甲")[0])
        out = self.say("/rh d20")                                                  # 甲有个人群 G1，结果私发，群里只有一句
        self.assertIn("暗骰", out[0])
        self.assertNotIn("D20 =", "".join(out))
        self.assertIn("发不出去", out[0])                                          # 测试里没有真机器人，投递失败时结果不能露在群里
        self.assertIn("白名单", out[0])                                            # 并且告诉他怎么办：把 KP 加进机器人白名单
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
        self.assertIn("COC 的指令", self.say("/ti")[0])
        self.assertIn("COC 的指令", self.say("/恐惧症状")[0])

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


class CardBot(FakeBot):
    """reject=True 时，凡是 Markdown 都当作 QQ 拒绝（没开权限）。"""
    reject = False

    async def send(self, event, message, **kw):
        segs = [message] if isinstance(message, MessageSegment) else (list(message) if not isinstance(message, str) else [])
        if self.reject and any(sg.type == "markdown" for sg in segs):
            raise ActionFailed(Response(400, content=b"{}"))
        SENT.append(message)


def _show(m) -> str:
    if isinstance(m, str):
        return m
    segs = [m] if isinstance(m, MessageSegment) else list(m)
    for sg in segs:
        if sg.type == "markdown":
            return "[MD]" + sg.data["markdown"].content
    return "<图>"


class CardE2E(unittest.TestCase):
    """检定结果发 Markdown 小卡片：能发就发卡片，QQ 拒绝就退回文字（并暂停重试），开关能关。"""

    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        import plugins.dice as pd
        pd._md_off_until = 0.0
        SENT.clear()
        self.bot = CardBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        self.bot.reject = False
        roles.create_role("甲", "B1", "G1", "M1")
        self.n = 200

    def say(self, text, member="M1", group="G1"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"c{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [_show(m) for m in SENT[before:]]

    def test_card_is_sent_by_default(self):
        out = self.say("/ra 侦查 70")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("[MD]### "), out)
        self.assertIn("D100", out[0])
        self.assertTrue(self.say("/rd 2d6")[0].startswith("[MD]### "))
        self.assertTrue(self.say("/sc 1/1d6 60")[0].startswith("[MD]### "))
        self.assertIn("DND5e", self.say("/切换规则 dnd")[0])                       # 没有卡片的回复照常发文字
        self.assertTrue(self.say("/ra 运动 +2 15")[0].startswith("[MD]### "))
        self.assertTrue(self.say("/at 长剑 +5 15 1d8+3")[0].startswith("[MD]### "))

    def test_switch_off(self):
        set_config("dice_card", "0")
        out = self.say("/ra 侦查 70")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("侦查 检定：D100="), out)

    def test_falls_back_when_qq_rejects(self):
        import plugins.dice as pd
        self.bot.reject = True
        out = self.say("/ra 侦查 70")
        self.assertEqual(len(out), 1)                                              # 只有一条：退回的纯文字
        self.assertTrue(out[0].startswith("侦查 检定：D100="), out)
        self.assertGreater(pd._md_off_until, 0)
        self.bot.reject = False
        self.assertTrue(self.say("/ra 侦查 70")[0].startswith("侦查 检定："))       # 冷却期内不再试 Markdown
        pd._md_off_until = 0.0
        self.assertTrue(self.say("/ra 侦查 70")[0].startswith("[MD]### "))         # 冷却过了又会试

    def test_log_records_plain_text_not_card(self):
        from core import trpg, trpglog
        for t in ("trpg_log_lines", "trpg_logs", "trpg_active", "trpg_card_attrs", "trpg_cards", "trpg_identities", "trpg_players"):
            with get_conn() as c:
                c.execute(f"DELETE FROM {t}")
        trpg.set_mode("G1", "trpg")
        self.say("log new 卡片")
        self.say("ra 侦查 70")
        kinds = [(l["kind"], l["text"]) for l in trpglog.lines(trpglog.current("G1")["id"])]
        dice_lines = [t for k, t in kinds if k == "dice"]
        self.assertEqual(len(dice_lines), 1)
        self.assertTrue(dice_lines[0].startswith("侦查 检定：D100="), dice_lines)   # log 里是文字，不是 Markdown


class TrpgE2E(unittest.TestCase):
    """跑团模式走真正的事件分发：不带前缀的指令、角色卡、log 记录、恋综指令被屏蔽。"""

    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("trpg_log_lines", "trpg_logs", "trpg_attr_history", "trpg_active", "trpg_card_attrs", "trpg_cards",
                      "trpg_identities", "trpg_players", "role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("dice_card", "0")        # 老测试断言的是纯文字；卡片的路径在 CardE2E 里测
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        roles.create_role("甲", "B1", "G9", "M1")
        from core import trpg
        trpg.set_mode("G1", "trpg")
        self.n = 100

    say = DiceE2E.say

    def test_hidden_roll_in_trpg_group_ignores_romance_role(self):
        """跑团群里的暗骰只走单聊，不碰恋综的角色和个人群；失败提示里也不提绑定角色。"""
        from plugins import dice as dice_plugin
        pushed = []

        async def fake_push(role_id, text, send, at=True):
            pushed.append(text)
            return True, "G9"
        orig = dice_plugin.messaging.push_to_role
        dice_plugin.messaging.push_to_role = fake_push
        try:
            self.say("st 力量60")
            out = self.say("rh d20")                                  # M1 在恋综里有角色「甲」和个人群，但这是跑团群
            self.assertEqual(pushed, [])                              # 没有往个人群发
            self.assertIn("白名单", out[0])
            self.assertNotIn("个人群", out[0])
            self.assertNotIn("绑定角色", out[0])
            self.assertNotIn("D20 =", "".join(out))                   # 结果也没露在群里
            self.assertNotIn("甲", out[0])                            # 称呼不用恋综的角色名
        finally:
            dice_plugin.messaging.push_to_role = orig

    def test_sanity_write_back_to_card(self):
        from core import trpg
        self.say("st 理智60")
        out = self.say("sc 5/5")[0]
        self.assertIn("已写回", out)
        card = trpg.active_card("G1", trpg.player_for("M1", "G1", create=False))
        self.assertEqual(trpg.get_attr(card["id"], "SAN"), 55)
        self.assertTrue(any(h["source"] == "理智损失" and h["new_value"] == 55 for h in trpg.card_history(card["id"])))

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
