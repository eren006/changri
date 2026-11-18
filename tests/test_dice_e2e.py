"""走真正的 NoneBot 事件分发：前缀、认角色、切换规则、属性、长内容发图。需要整个进程里只初始化一次 NoneBot。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent, MessageSegment  # noqa: E402
from nonebot.adapters.qq.exception import ActionFailed  # noqa: E402
from nonebot.drivers import Response  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import roles  # noqa: E402
from changri.core.db import get_conn, get_config, init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    from changri.core import extensions
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

    def test_usage_counts_by_command_word(self):
        """匿名上报按指令词计数：只记指令词（别名照实记），不记后面写的内容；不是指令的话不算。"""
        from changri.core import usage
        usage._take_count()
        self.say("/rd d20")
        self.say("r d6 打怪")
        self.say("rd 3d6")
        self.say("hello")
        n, by = usage._take_count()
        self.assertEqual((n, by), (3, {"rd": 2, "r": 1}))
        self.assertNotIn("打怪", str(by))

    def test_attrs_flow_and_rule_switch(self):
        self.assertIn("已记下", self.say("/录属性 力量60 SAN55")[0])
        self.assertIn("力量　60", self.say("/属性")[0])
        self.assertIn("甲的力量60", self.say("/ra 力量")[0])
        self.assertIn("还没有「敏捷」", self.say("/ra 敏捷")[0])
        self.assertIn("取自甲的 SAN", self.say("/sc 1/1d6")[0])
        self.assertIn("已写回", self.say("/sc 1/1d6")[0])
        self.assertIn("DND5e", self.say("/切换规则 dnd")[0])
        self.assertIn("DC12", self.say("/ra 力量 12")[0])
        self.assertIn("DND 里没有", self.say("/sc 1/1d6")[0])

    def test_sanity_and_growth_write_back(self):
        self.say("/录属性 SAN60 侦查5")
        out = self.say("/sc 5/5")[0]
        self.assertIn("已写回：甲的理智 60 → 55", out)
        self.assertIn("智力检定", out)                                          # 单次掉 5 点：提醒智力检定
        self.assertIn("SAN　55", self.say("/属性")[0])
        self.assertNotIn("已写回", self.say("/sc 1/1 40")[0])                   # 自己写的理智数值：不动角色
        self.assertIn("SAN　55", self.say("/属性")[0])
        with mock.patch("changri.core.dice.random.randint", lambda a, b: b):          # D100 掷 100、成长 +10
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
        from changri.plugins import dice as dice_plugin
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

    def test_fits_one_message_goes_as_text(self):
        out = self.say("/骰子帮助")                                                     # 目录几百字，一条发得完：发文字，不画图
        self.assertTrue(out and all(x != "<图>" for x in out))

    def test_help_is_a_menu_then_sections(self):
        set_config("long_as_image", "0")
        menu = self.say("/骰子帮助")[0]
        self.assertIn("1. 掷骰", menu)
        self.assertIn("← 当前规则", menu)
        self.assertLess(len(menu), 600)                                                  # 目录本身不长
        self.assertIn("【绿色三角洲】", self.say("/骰子帮助 5")[0])
        self.assertIn("【队伍】", self.say("/骰子帮助 team")[0])
        self.assertIn("没有「火星」", self.say("/骰子帮助 火星")[0])
        from changri.core import dice as core_dice
        for _, _, _, text, _ in core_dice.HELP_SECTIONS:                              # 每块都不长
            self.assertLess(len(text), 400, text[:10])


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
        import changri.plugins.dice as pd
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
        import changri.plugins.dice as pd
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
        from changri.core import trpg, trpglog
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
            for t in ("trpg_team_members", "trpg_teams", "trpg_growth_marks", "trpg_log_lines", "trpg_logs", "trpg_attr_history", "trpg_active", "trpg_card_attrs", "trpg_cards",
                      "trpg_identities", "trpg_players", "role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        set_config("dice_card", "0")        # 老测试断言的是纯文字；卡片的路径在 CardE2E 里测
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        roles.create_role("甲", "B1", "G9", "M1")
        from changri.core import trpg
        trpg.set_mode("G1", "trpg")
        self.n = 100

    say = DiceE2E.say

    def test_hidden_roll_in_trpg_group_ignores_romance_role(self):
        """跑团群里的暗骰只走单聊，不碰恋综的角色和个人群；失败提示里也不提绑定角色。"""
        from changri.plugins import dice as dice_plugin
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
        from changri.core import trpg
        self.say("st 理智60")
        out = self.say("sc 5/5")[0]
        self.assertIn("已写回", out)
        card = trpg.active_card("G1", trpg.player_for("M1", "G1", create=False))
        self.assertEqual(trpg.get_attr(card["id"], "SAN"), 55)
        self.assertTrue(any(h["source"] == "理智损失" and h["new_value"] == 55 for h in trpg.card_history(card["id"])))

    def test_growth_marks(self):
        """检定成功自动记成长标记（属性不记、失败不记、只说一次），/en+ /en- /en list，/en 一次掷完并写回、清空。"""
        from changri.core import trpg
        self.say("st 侦查50 力量60")
        with mock.patch("changri.core.dice.random.randint", lambda a, b: b):           # D100=99：失败
            self.assertNotIn("成长标记", self.say("ra 侦查")[0])
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 1):           # D100=11：成功
            self.assertIn("侦查记下成长标记", self.say("ra 侦查")[0])
            self.assertNotIn("成长标记", self.say("ra 侦查")[0])                        # 已经标过，不再说
            self.assertNotIn("成长标记", self.say("ra 力量")[0])                        # 属性不能成长
            self.assertNotIn("成长标记", self.say("ra 潜行 40")[0])                     # 卡上没有的技能不标
        self.assertIn("侦查", self.say("en list")[0])
        self.assertIn("卡上没有", self.say("en+ 开锁术")[0])                          # 卡上没有、也没有初始值
        self.say("st 图书馆40")
        out = self.say("en+ 图书馆，力量")[0]
        self.assertIn("记上成长标记：图书馆", out)
        self.assertIn("属性不能成长", out)
        self.assertIn("去掉成长标记：图书馆", self.say("en- 图书馆")[0])
        self.assertIn("成长标记：侦查", self.say("st")[0])
        with mock.patch("changri.core.dice.random.randint", lambda a, b: b):           # D100=100 必成长，+10
            out = self.say("en")[0]
        self.assertIn("成长检定 ×1", out)
        self.assertIn("50 → 60", out)
        card = trpg.active_card("G1", trpg.player_for("M1", "G1", create=False))
        self.assertEqual(trpg.get_attr(card["id"], "侦查"), 60)
        self.assertTrue(any(h["source"] == "技能成长" for h in trpg.card_history(card["id"])))
        self.assertIn("还没有成长标记", self.say("en")[0])
        self.assertIn("成长检定", self.say("en 侦查")[0])                                # 单项写法照旧
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 1):
            self.say("ra 侦查")
        self.assertIn("已清掉", self.say("en clr")[0])
        self.assertEqual(trpg.growth_marks(card["id"]), [])

    def test_coc_defaults_derived_and_caps(self):
        """没录的技能按初始值；生命/魔法/理智有上限；/st 显示上限、DB、体格；初始值技能也能记成长并写回。"""
        set_config("long_as_image", "0")                                                # 断言文字，不发长图
        from changri.core import trpg
        self.say("st 力量70 体质50 体型60 意志50")
        out = self.say("ra 侦查 25")[0]
        self.assertNotIn("初始值", out)                                                  # 自己写了数值
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 1):
            out = self.say("ra 侦查")[0]
        self.assertIn("侦查25，初始值", out)
        self.assertIn("记下成长标记", out)                                               # 初始值技能也能标
        self.assertIn("还没有「闪避」", self.say("ra 闪避")[0])                         # 闪避=敏捷/2，敏捷还没录
        self.say("st 敏捷60")
        self.assertIn("闪避30，初始值", self.say("ra 闪避")[0])
        self.assertIn("上限 11", self.say("st 生命20")[0])                             # (50+60)/10
        self.assertIn("10 +5 → 10（上限 10）", self.say("st 魔法+5")[0])               # 没录过：按初始值 意志/5 增减
        self.say("st 克苏鲁神话30")
        self.assertIn("上限 69", self.say("st 理智80")[0])
        shown = self.say("st")[0]
        for part in ("生命上限 11", "魔法上限 10", "理智上限 69", "DB +1D4", "体格 1"):
            self.assertIn(part, shown)
        with mock.patch("changri.core.dice.random.randint", lambda a, b: b):           # 成长必中 +10
            out = self.say("en")[0]
        self.assertIn("25 → 35", out)
        card = trpg.active_card("G1", trpg.player_for("M1", "G1", create=False))
        self.assertEqual(trpg.get_attr(card["id"], "侦查"), 35)

    def test_sanity_uses_pow_when_san_missing(self):
        self.say("st 意志55")
        out = self.say("sc 1/1")[0]
        self.assertIn("55", out)
        self.assertIn("已写回", out)

    def test_coc_generation(self):
        set_config("long_as_image", "0")                                                # 断言文字，不发长图
        out = self.say("coc 3")[0]
        self.assertIn("第3组", out)
        self.assertIn("DB", out)
        self.assertEqual(self.say("coc7规则真好玩"), [])                                 # 不是数字：当聊天，不回
        from changri.core.db import set_config as sc
        sc("dice_rule:B1|G1", "dnd")
        self.assertIn("COC 的指令", self.say("coc")[0])

    def test_team(self):
        set_config("long_as_image", "0")                                                # 断言文字，不发长图
        self.say("st 侦查60 聆听50")
        self.say("st 侦查40", member="M2")
        self.say("nn 小甲")
        self.assertIn("1 人", self.say("team 调查员 add")[0])
        self.assertIn("2 人", self.say("team 调查员 add", member="M2")[0])
        self.assertIn("调查员（2 人）", self.say("team list")[0])
        out = self.say("team 调查员 侦查 聆听")[0]
        self.assertLess(out.index("侦查 60"), out.index("侦查 40"))                     # 按第一项从高到低
        self.assertIn("聆听 20（初始）", out)
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 1):
            out = self.say("team 调查员 ra 侦查")[0]
        self.assertIn("小甲", out)
        self.assertEqual(out.count("检定"), 2)
        self.assertIn("移出 1 人", self.say("team 调查员 del", member="M2")[0])
        self.assertIn("解散", self.say("team 调查员 clear")[0])
        self.assertIn("没有「调查员」", self.say("team 调查员")[0])

    def test_tools(self):
        self.assertEqual(self.say("name 英 3")[0].count("、"), 2)
        self.assertTrue(self.say("who 甲 乙 丙")[0].startswith("随机顺序"))
        self.assertIn("正位", self.say("draw 塔罗 3")[0].replace("逆位", "正位"))
        self.assertIn("可以抽的牌堆", self.say("draw")[0])
        self.assertEqual(self.say("jrrp")[0], self.say("今日人品")[0])                  # 同一天固定

    def test_delta_green(self):
        """绿色三角洲：切换规则、建卡跟群规则、属性×5、初始值、两位相同判大成功/大失败、失败记成长、/en +1D4、理智与崩溃点、/dg。"""
        from changri.core import trpg
        set_config("long_as_image", "0")
        self.assertIn("绿色三角洲", self.say("切换规则 绿色三角洲")[0])
        self.say("st 力量12 体质10 意志11")
        card = trpg.active_card("G1", trpg.player_for("M1", "G1", create=False))
        self.assertEqual(card["rule"], "dg")                                              # 自动建的卡跟群规则
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 30):
            self.assertIn("力量12×5", self.say("ra 力量")[0])                              # 60 对 30：成功
            out = self.say("ra 搜索")[0]                                                   # 初始值 20，掷 30：失败
        self.assertIn("搜索20，初始值", out)
        self.assertIn("记下成长标记", out)                                               # 失败才记
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 11):
            self.assertIn("大成功", self.say("ra 枪械")[0])                               # 11 ≤ 20 且两位相同
            self.assertIn("大失败", self.say("ra 枪械-15")[0])                            # 11 > 5 且两位相同
        self.assertIn("没有奖励骰", self.say("rb 枪械")[0])
        self.assertIn("COC 的指令", self.say("ti")[0])
        shown = self.say("st")[0]
        for part in ("（×5 = 60）", "生命上限 11", "意志力上限 11", "理智上限 99", "崩溃点 44"):
            self.assertIn(part, shown)
        self.assertIn("上限 11", self.say("st 意志力20")[0])
        with mock.patch("changri.core.dice.random.randint", lambda a, b: b):
            out = self.say("en")[0]
        self.assertIn("+1D4(4)", out)
        self.assertIn("20 +1D4(4) → 24", out)
        self.assertEqual(trpg.get_attr(card["id"], "搜索"), 24)
        self.say("st 理智46")                                                             # 崩溃点 44
        with mock.patch("changri.core.dice.random.randint", lambda a, b: 99 if b == 100 else b):
            out = self.say("sc 0/1d4")[0]                                                 # 99 两位相同且失败：大失败，损失取最大 4
        self.assertIn("大失败", out)
        self.assertIn("46 → 42", out)
        self.assertIn("崩溃点重设为 42 - 11 = 31", out)
        self.assertEqual(trpg.get_attr(card["id"], "崩溃点"), 31)
        self.assertIn("第2组", self.say("dg 2")[0])
        self.assertEqual(self.say("dg 那个团"), [])
        self.assertIn("绿色三角洲", self.say("骰子帮助 dg")[0])

    def test_flow(self):
        from changri.core import trpglog
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
