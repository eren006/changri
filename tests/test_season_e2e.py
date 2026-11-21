"""季度与出场的指令，走真正的 NoneBot 事件分发。"""
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

from changri.core import admins, entrance, roles, season  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402

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


def rng(back=1, fwd=5):
    t = time.time()
    return time.strftime("%m%d", time.localtime(t - back * 86400)) + "-" + time.strftime("%m%d", time.localtime(t + fwd * 86400))


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(message)

    allow_md = False

    async def send_to_group(self, group_openid, message, **kw):
        if str(message).startswith("<markdown:"):
            if not self.allow_md:
                raise RuntimeError("no markdown permission")
            SENT.append(f"[{group_openid}] MD:{message}")
            return
        SENT.append(f"[{group_openid}] {message}")


class SeasonE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("seasons", "entrance", "role_groups", "roles", "admins", "admin_codes", "config"):
                c.execute(f"DELETE FROM {t}")
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        nonebot.get_bots()["B1"] = self.bot
        admins.claim(admins.make_code(), "B1", "ADMIN")
        self.n = 0

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def say(self, text, member="ADMIN", group="G1"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [m if isinstance(m, str) else "<图>" for m in SENT[before:]]

    def test_admin_only(self):
        for t in ("开始季度", "结束季度", "收尾", "季度状态", "设置天数 D1", "开始出场", "设为戏群"):
            self.assertIn("只有管理员能用", self.say(t, member="PLAYER")[0], t)

    def test_season_commands(self):
        self.assertIn("没有季度", self.say("季度状态")[0])
        self.assertIn("预订", self.say(f"预订季度 甲季 {rng()}")[0])
        self.assertIn("没有叫", self.say("开始季度 乙季")[0])
        self.assertIn("档期格式不对", self.say("预订季度 乙季 abc")[0])
        self.assertIn("补戏到", self.say(f"预订季度 丙季 {rng(-20, 22)} 1230 阿林")[0])
        out = self.say("开始季度")[0]
        self.assertIn("开始了", out)
        self.assertEqual(season.running()["name"], "甲季")
        self.assertIn("已经有进行中", self.say("开始季度")[0])
        self.assertIn("D5", self.say("设置天数 d5")[0])
        self.assertEqual(season.current_day(), 5)

    def test_end_and_wipe(self):
        roles.create_role("甲", "B1", "G1", "M1")
        self.say(f"预订季度 甲季 {rng()}")
        self.assertIn("没清空", self.say("开始季度")[0])
        self.assertIn("开始了", self.say("开始季度 确认")[0])
        self.assertIn("还在进行中", self.say("收尾")[0])
        self.assertIn("已结束", self.say("结束季度")[0])
        self.assertIn("确认请发", self.say("收尾")[0])
        self.assertIn("已收尾", self.say("收尾 确认")[0])
        self.assertEqual(season.phase(), "none")

    def test_entrance_flow(self):
        self.say(f"预订季度 甲季 {rng()}")
        self.say("开始季度")
        for i, n in enumerate(["甲", "乙", "阿林"]):
            roles.create_role(n, "B1", f"P{i}", f"M{i}")
        self.assertIn("已设为 NPC", self.say("设为NPC 阿林")[0])
        self.assertIn("共 2 人", self.say("抽取出场顺序")[0])
        self.assertIn("还没有戏群", self.say("开始出场")[0])
        self.assertIn("已把这个群设为戏群", self.say("设为戏群", group="STAGE")[0])
        out = self.say("开始出场")
        self.assertTrue(any("出场开始" in o for o in out))
        cur = entrance.current()
        self.assertFalse(any("[STAGE]" in o and "轮到" in o for o in out))           # 戏群不提醒
        mem = {"甲": "M0", "乙": "M1"}
        grp = {"甲": "P0", "乙": "P1"}
        self.assertTrue(any(f"[{grp[cur['name']]}]" in o and "轮到你出场" in o and f"@{cur['name']} " in o for o in out))   # 没有 markdown 权限：退回纯文字「@名字」
        # 别人替你交不行
        other = [n for n in mem if n != cur["name"]][0]
        self.assertIn("不符", self.say(f"提交出场 {cur['name']} 我不是本人", member=mem[other], group=grp[other])[0])
        out = self.say(f"提交出场 {cur['name']} 本人出场啦", member=mem[cur["name"]], group=grp[cur["name"]])
        self.assertTrue(any("本人出场啦" in o and "[STAGE]" in o for o in out))
        nxt = entrance.current()
        self.assertTrue(any(f"[{grp[nxt['name']]}]" in o and "轮到你出场" in o for o in out))
        # 戏群里本人直接发「角色名 内容」也行，别人发的不算
        stranger = [n for n in mem if n != nxt["name"]][0]
        self.say(f"{nxt['name']} 我不是本人", member=mem[stranger], group="STAGE")
        self.assertEqual(entrance.current()["name"], nxt["name"])
        out = self.say(f"{nxt['name']} 直接在戏群发", member=mem[nxt["name"]], group="STAGE")
        self.assertFalse(any("直接在戏群发" in o for o in out))                       # 玩家自己发的，机器人不重发
        self.assertTrue(any("[STAGE]" in o and "全部完成" in o for o in out))
        self.assertIn("已全部完成", self.say("查看出场状态")[0])

    def test_at_uses_markdown_when_allowed(self):
        import changri.core.qqsend as L
        self.say(f"预订季度 甲季 {rng()}")
        self.say("开始季度")
        roles.create_role("甲", "B1", "P0", "M0")
        self.say("设为戏群", group="STAGE")
        self.say("抽取出场顺序")
        FakeBot.allow_md = True
        L._md_off_until = 0.0
        try:
            out = self.say("开始出场")
        finally:
            FakeBot.allow_md = False
            L._md_off_until = 0.0
        self.assertTrue(any("[P0]" in o and '<qqbot-at-user id="M0" />' in o and "轮到你出场" in o for o in out))
        # 被拒一次后一小时内不再试 markdown，直接纯文字
        self.say("跳过出场")


if __name__ == "__main__":
    unittest.main()
