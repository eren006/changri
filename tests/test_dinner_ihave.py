"""晚餐小游戏：我有你没有（规则 + 走真正的 NoneBot 分发看回执和 @），顺带送甜点的个人群限制。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "ihave.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import dinner, dinner_ihave as I, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    from changri.core import extensions
    extensions.load_all()
nonebot.load_plugin("changri.plugins.dinner")      # 晚餐默认关，load_all 不加载它

from nonebot.message import handle_event  # noqa: E402

SENT: list = []


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(message)


def _clean():
    init_db()
    with get_conn() as c:
        for t in ("role_groups", "roles", "config", "admins"):
            c.execute(f"DELETE FROM {t}")


class IHaveRules(unittest.TestCase):
    def setUp(self):
        _clean()
        dinner.start(4, "无菜", "B", "G")
        for i, n in enumerate(["甲", "乙", "丙"], 1):
            dinner.sit(n, i)

    def test_start_checks(self):
        self.assertIn("入座", I.start("丁", False, "B", "G")[1])
        self.assertIn("那个群", I.start("甲", False, "B", "别的群")[1])
        self.assertTrue(I.start("丁", True, "B", "G")[0])               # 管理员没入座也能开
        self.assertIn("已经有一局", I.start("甲", False, "B", "G")[1])
        self.assertEqual(I.active()["fingers"], {"甲": 10, "乙": 10, "丙": 10})

    def test_round_flow_edit_vote_and_make_up(self):
        I.start("甲", False, "B", "G")
        self.assertIn("轮到 甲", I.speak("乙", "我去过冰岛")[1])
        self.assertIn("还没发言", I.vote("乙", True))
        self.assertTrue(I.speak("甲", "我去过冰岛")[0])
        self.assertIn("（改了）", I.speak("甲", "我去过南极")[1])
        self.assertIn("出题", I.vote("甲", True))
        self.assertEqual(I.vote("乙", False), "")
        self.assertEqual(I.vote("乙", True), "")                          # 下一轮前能改，以最后一次为准
        self.assertIn("只有管理员", I.next_round("乙", False)[1])
        ok, r = I.next_round("甲", False)                                # 出题的人自己能「下一轮」
        self.assertTrue(ok)
        self.assertIn("折了手指：乙", r["text"])
        self.assertIn("还没表态：丙", r["text"])
        self.assertEqual([n for n, _ in r["at"]], ["丙", "乙"])           # @ 没表态的 + 下一个出题人
        g = I.active()
        self.assertEqual((g["fingers"]["乙"], g["fingers"]["丙"]), (9, 10))
        self.assertIn("不用补", I.make_up("甲", True))
        self.assertEqual(I.make_up("丙", True), "")
        self.assertIn("不用补", I.make_up("丙", True))                    # 只能补一次
        self.assertEqual(I.active()["fingers"]["丙"], 9)

    def test_out_at_zero_and_skipped_as_speaker(self):
        I.start("甲", False, "B", "G")
        g = I.active(); g["fingers"]["乙"] = 1; I._save(g)
        I.speak("甲", "我养过猫")
        I.vote("乙", True)
        I.vote("丙", False)
        ok, r = I.next_round("", True)
        self.assertIn("出局：乙", r["text"])
        self.assertIn("请 丙 发言", r["text"])                           # 乙出局，不再出题
        self.assertIn("出局", I.vote("乙", True))

    def test_ends_after_everyone_spoke_with_final_stats(self):
        I.start("甲", False, "B", "G")
        for sp in ("甲", "乙"):
            I.speak(sp, "我做过一件事")
            ok, r = I.next_round(sp, False)
            self.assertFalse(r["ended"])
        I.speak("丙", "我也做过")
        I.vote("甲", True)
        ok, r = I.next_round("丙", False)
        self.assertTrue(r["ended"])
        self.assertIn("所有人都出完题", r["text"])
        self.assertIn("最终统计", r["text"])
        self.assertIn("甲：9 根", r["text"])
        self.assertIn("没表态的（按没折算）：乙", r["text"])
        self.assertIsNone(I.active())

    def test_no_speech_is_skipped_and_status_hides_choice(self):
        I.start("甲", False, "B", "G")
        ok, r = I.next_round("", True)
        self.assertIn("没发言，跳过", r["text"])
        I.speak("乙", "我爬过山")
        I.vote("甲", True)
        st = I.status()
        self.assertIn("已表态（1）：甲", st)
        self.assertIn("还没表态（1）：丙", st)

    def test_dinner_end_clears_and_admin_end(self):
        I.start("甲", False, "B", "G")
        self.assertIn("提前结束", I.end()[1])
        I.start("甲", False, "B", "G")
        dinner.stop()
        self.assertIsNone(I.active())


class IHaveE2E(unittest.TestCase):
    def setUp(self):
        _clean()
        set_config("ext:dinner", "1")
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        roles.create_role("甲", "B1", "P1", "M1")
        roles.create_role("乙", "B1", "P2", "M2")
        roles.create_role("丙", "B1", "P3", "M3")
        self.n = 0
        dinner.start(3, "无菜", "B1", "DIN")
        for i, n in enumerate(["甲", "乙", "丙"], 1):
            dinner.sit(n, i)

    def say(self, text, member="M1", group="DIN"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-19T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [str(m) for m in SENT[before:]]

    def test_silent_votes_and_at_on_next_round(self):
        self.assertEqual(self.say("发言 随便说说"), [])                      # 没开局：「发言」当没听见
        self.assertIn("开局", self.say("开始我有你没有", "M2")[0])
        self.assertEqual(self.say("发言 我去过冰岛", group="别的群"), [])     # 别的群里不算指令
        self.assertEqual(self.say("不折腾了", "M2", group="别的群"), [])
        self.assertIn("🗣️ 甲：我去过冰岛", self.say("发言 我去过冰岛")[0])
        self.assertEqual(self.say("折手指", "M2"), [])                        # 表态不回执
        self.assertIn("出题", self.say("折手指", "M1")[0])                   # 出错照样说
        out = self.say("下一轮")
        self.assertEqual(len(out), 1)
        self.assertIn('<qqbot-at-user id="M2" />', out[0])                  # 下一个出题人：在晚餐群发过指令，真 @
        self.assertIn("@丙", out[0])                                         # 没在这个群发过指令：写名字
        self.assertIn("还没表态：丙", out[0])
        self.assertEqual(self.say("补折", "M3"), [])
        self.assertEqual(I.active()["fingers"]["丙"], 9)
        self.assertIn("第 2 轮", self.say("我有你没有状态", "M3")[0])
        self.assertIn("只有管理员能用", self.say("结束我有你没有", "M2")[0])

    def test_dessert_commands_only_in_own_group(self):
        self.assertIn("个人群", self.say("领取甜点")[0])                     # 在晚餐群发：拒绝
        self.assertIn("领到了三道甜点", self.say("领取甜点", group="P1")[0])
        self.assertIn("个人群", self.say("我的甜点", group="P2")[0])         # 别人的个人群也不行


if __name__ == "__main__":
    unittest.main()
