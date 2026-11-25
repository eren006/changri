"""长期群模式：走真正的 NoneBot 事件分发（切模式、切长期群、注册、面板、赠送、字数统计、调整、ra 读属性、恋综指令屏蔽），
再用设置平台的测试客户端把后台页面都打开一遍。"""
import asyncio
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "lte.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import longterm as lt, qq_compat, webusers  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    qq_compat.apply()
    set_config("ext:longterm", "1")          # 个性化模块，默认关：测试里打开再加载
    from changri.core import extensions
    extensions.load_all()

from nonebot.message import handle_event  # noqa: E402

SENT: list = []
FWD = ("=== 消息 1 ===\n[消息内容] [群聊的聊天记录]\n[消息类型] 引用消息\n[关联消息]\n"
       "--- 第1条 ---\n    [消息内容] 今天天气真好啊\n    [发送者] 阿青\n"
       "--- 第2条 ---\n    [消息内容] 是啊\n    [发送者] 小白QQ\n"
       "--- 第3条 ---\n    [消息内容] 一起出去走走吧\n    [发送者] 阿青\n")


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(message)


class LongtermE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("lt_groups", "config", "admins", "web_users"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO admins(bot_id, member_openid, note, created_ts) VALUES ('B1','ADM','',?)", (int(time.time()),))
        set_config("ext:longterm", "1")
        set_config("dice_card", "0")
        SENT.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        self.n = 0
        self.gid, _ = lt.create_group("春日")
        lt.update_group(self.gid, chars_per_step=10, coins_per_step=2, reg_fields="角色", count_review=False)
        lt.set_attr_def(self.gid, "侦查", 60, 0, 99)

    def say(self, text, member="M1", group="G1", **extra):
        self.n += 1
        data = {"id": f"e{self.n}", "content": " " + text, "timestamp": "2026-11-21T00:00:00+08:00",
                "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
                "group_id": group, "group_openid": group, **extra}
        ev = GroupMessageCreateEvent.model_validate(data)
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [m if isinstance(m, str) else str(m) for m in SENT[before:]]

    def _setup_group(self):
        self.assertIn("切换模式需要", self.say("/模式 长期群")[0])
        out = self.say("/模式 长期群", member="ADM")[0]
        self.assertIn("长期群模式", out)
        self.assertIn("/切换长期群", out)
        self.assertIn("还没接到长期群", self.say("/注册 阿青")[0])
        self.assertIn("机器人管理员", self.say("/切换长期群 春日")[0])
        self.assertIn("没有叫「秋」", self.say("/切换长期群 秋", member="ADM")[0])
        self.assertIn("接到长期群「春日」", self.say("/切换长期群 春日", member="ADM")[0])

    def test_full_flow(self):
        self._setup_group()
        out = self.say("/注册 阿青\n角色：剑客")[0]
        self.assertIn("注册好了", out)
        self.assertIn("角色：剑客", out)
        self.assertIn("侦查 60", out)
        self.assertIn("还没填：角色", self.say("/注册 小白", member="M2")[0])
        self.assertIn("侦查 60", self.say("/我的")[0])
        self.assertIn("侦查：60", self.say("/属性")[0])
        self.assertIn("侦查", self.say("/ra 侦查")[0])                                    # 检定读长期群属性
        self.assertRegex(self.say("/rt 100 侦查")[0], r"D100=\d+/60")
        self.assertIn("改好了", self.say("/st 侦查70")[0])
        self.assertIn("没有「飞行」", self.say("/st 飞行3")[0])
        lt.update_group(self.gid, self_attrs=False)
        self.assertIn("自己改不了", self.say("/st 侦查80")[0])
        # 字数统计
        self.assertIn("没读到", self.say("/字数统计")[0])
        out = self.say("/字数统计", message_type=103, msg_elements=[{"content": FWD}])[0]
        self.assertIn("阿青 +14 字，换到 2", out)
        self.assertIn("小白QQ 2 字", out)
        out = self.say("/字数统计", message_type=103, msg_elements=[{"content": FWD}])[0]
        self.assertIn("3 条以前统计过", out)
        self.assertIn("1. 阿青　14 字", self.say("/字数榜")[0])
        # 管理员调整 + 赠送
        self.assertIn("机器人管理员", self.say("/调整 阿青 金币+10")[0])
        out = self.say("/调整 阿青 金币+10 苹果+3 侦查-5", member="ADM")[0]
        self.assertIn("金币：2 → 12", out)
        self.assertIn("苹果：0 → 3", out)
        self.assertIn("侦查：70 → 65", out)
        self.assertIn("苹果 ×3", self.say("/背包")[0])
        self.assertIn("送给「小白」苹果×2", self.say("/赠送 小白 苹果 2")[0])
        self.assertIn("送给「小白」5 金币", self.say("/赠送 小白 金币 5")[0])
        self.assertIn("没有「梨」", self.say("/赠送 小白 梨")[0])
        out = self.say("/背包", member="M2")[0]
        self.assertIn("苹果 ×2", out)
        self.assertIn("5 金币", out)
        self.assertIn("苹果 ×1", self.say("/背包 阿青", member="M2")[0])                    # 看别人
        # 商城
        lt.save_item_def(self.gid, "灵药", "", "侦查+1", on_sale=True, price=4, stock=-1)
        self.assertIn("灵药　4 金币", self.say("/商城")[0])
        self.assertIn("买到 灵药×1", self.say("/购买 灵药")[0])
        self.assertIn("侦查：65 → 66", self.say("/使用 灵药")[0])
        # 聊天里碰巧以指令词开头：不当指令
        for chat in ("调整一下状态", "使用说明在哪", "购买力下降了", "注册表"):
            self.assertEqual(self.say(chat), [], chat)
        # 恋综指令在长期群里不响应
        self.assertEqual(self.say("/创建新角色 某某"), [])
        # 切回恋综：长期群指令不响应
        self.say("/模式 恋综", member="ADM")
        self.assertNotIn("苹果", "".join(self.say("/背包")))

    def test_notes(self):
        """条目：玩家自己加栏目、写字段，都不是必填；别人能看；只有管理员能 @ 人替别人改。"""
        self._setup_group()
        self.say("/注册 阿青")
        self.say("/注册 小白", member="M2")
        self.assertIn("还没有条目", self.say("/条目")[0])
        out = self.say("/写条目 魔杖 木材：冬青 杖芯：凤凰羽毛")[0]
        self.assertIn("【魔杖】", out)
        self.assertIn("木材：冬青", out)
        self.assertIn("杖芯：凤凰羽毛", out)
        self.assertIn("长度：11 英寸", self.say("/写条目 魔杖\n长度：11 英寸")[0])              # 接着写、换行写
        self.assertIn("【宠物】（还没写内容）", self.say("/写条目 宠物")[0])                  # 只加一栏
        self.assertIn("名字：小灰", self.say("/写条目 宠物 名字 小灰")[0])                    # 不带冒号也行
        out = self.say("/条目")[0]
        self.assertIn("【魔杖】", out)
        self.assertIn("【宠物】", out)
        self.assertNotIn("【宠物】", self.say("/条目 魔杖")[0])                              # 只看一栏
        self.assertIn("木材：冬青", self.say("/条目 阿青", member="M2")[0])                  # 别人能看
        self.assertIn("📒 条目：魔杖、宠物", self.say("/我的")[0])
        # 删
        self.assertIn("已删掉「魔杖」里的「长度」", self.say("/删条目 魔杖 长度")[0])
        self.assertIn("已删掉「宠物」这一栏", self.say("/删条目 宠物")[0])
        self.assertIn("没有「宠物」", self.say("/删条目 宠物")[0])
        # @ 别人改：玩家不行，管理员可以
        at = lambda who: {"mentions": [{"scope": "single", "bot": False, "is_you": False, "id": who, "member_openid": who, "username": who}]}
        self.assertIn("只能改自己的", self.say("/写条目 魔杖 木材：柳木", member="M2", **at("M1"))[0])
        self.assertIn("木材：柳木", self.say("/写条目 魔杖 木材：柳木", member="ADM", **at("M1"))[0])
        self.assertIn("木材：柳木", self.say("/条目 魔杖", member="M2", **at("M1"))[0])
        self.assertIn("用法", self.say("/写条目")[0])
        self.assertEqual(self.say("条目很多"), [])                                          # 聊天不当指令

    def test_admin_pages(self):
        self._setup_group()
        self.say("/注册 阿青\n角色：剑客")
        self.say("/字数统计", message_type=103, msg_elements=[{"content": FWD}])
        m = lt.member_by_name(self.gid, "阿青")
        sid = lt.submissions(self.gid)[0]["id"]
        webusers.create_user("boss", "longenough1", "owner")
        from changri.admin import create_app
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        self.assertIn("春日", c.get("/longterm").get_data(as_text=True))
        html = c.get(f"/longterm/{self.gid}").get_data(as_text=True)                 # 总览：动态
        for s in ("阿青 注册了", f"字数统计 #{sid}", "动态"):
            self.assertIn(s, html)
        for tab, want in (("players", ("阿青", "剑客", "批量发放")), ("words", ("小白QQ", f"#{sid}")),
                          ("items", ("登记新道具", "添加配方")), ("settings", ("侦查", "签到奖励"))):
            html = c.get(f"/longterm/{self.gid}?tab={tab}").get_data(as_text=True)
            for s in want:
                self.assertIn(s, html, (tab, s))
        self.assertIn("今天天气真好啊", c.get(f"/longterm/sub/{sid}").get_data(as_text=True))
        page = f"/longterm/member/{m['id']}"
        self.assertIn("今天天气真好啊", c.get(page).get_data(as_text=True))           # 记在名下的发言能追溯
        t = tok(page)
        c.post(f"{page}/coins", data={"_csrf": t, "how": "set", "amount": "100", "note": "活动奖励"})
        c.post(f"{page}/adjust", data={"_csrf": t, "text": "药水+2 侦查=50"})
        m = lt.get_member(m["id"])
        self.assertEqual(m["coins"], 100)
        self.assertEqual(lt.items(m["id"]), [("药水", 2)])
        self.assertEqual(lt.get_attr(m["id"], "侦查"), 50)
        self.assertIn("活动奖励", c.get(page).get_data(as_text=True))
        g = f"/longterm/{self.gid}"
        t = tok(g)
        c.post(f"{g}/settings", data={"_csrf": t, "name": "春日", "currency": "银两", "chars_per_step": "500", "coins_per_step": "1",
                                       "reg_fields": "角色\nCP", "self_attrs": "1"})
        g2 = lt.get_group(self.gid)
        self.assertEqual((g2["currency"], g2["chars_per_step"], g2["self_attrs"], g2["count_admin_only"]), ("银两", 500, 1, 0))
        c.post(f"{g}/claim", data={"_csrf": t, "name": "小白QQ", "member_id": str(m["id"])})
        self.assertEqual(lt.list_unclaimed(self.gid), [])
        c.post(f"{g}/delete", data={"_csrf": t, "confirm": "不对"})
        self.assertIsNotNone(lt.get_group(self.gid))
        c.post(f"{g}/delete", data={"_csrf": t, "confirm": "春日"})
        self.assertIsNone(lt.get_group(self.gid))


if __name__ == "__main__":
    unittest.main()


class LongtermReviewE2E(LongtermE2E):
    """要批准（默认）：玩家交上来不入账，后台批准 / 驳回；签到、限购、发放、合成、帮助。"""
    def setUp(self):
        super().setUp()
        lt.update_group(self.gid, count_review=True, checkin_on=True, checkin_rewards="金币 3-5\n灵药 1")

    def test_full_flow(self):          # 父类的流程按「不要批准」写的，这里不重复跑
        pass

    def test_admin_pages(self):        # 同上
        pass

    def test_review_checkin_shop_grant_craft_help(self):
        self._setup_group()
        self.say("/注册 阿青\n角色：剑客")
        self.say("/注册 小白", member="M2")
        out = self.say("/字数统计", message_type=103, msg_elements=[{"content": FWD}])[0]
        self.assertIn("等管理员在后台批准后入账", out)
        m = lt.member_by_name(self.gid, "阿青")
        self.assertEqual(m["chars"], 0)
        self.assertIn("另有 14 字等批准", self.say("/我的")[0])
        self.assertIn("3 条以前统计过", self.say("/字数统计", message_type=103, msg_elements=[{"content": FWD}])[0])
        sid = lt.pending_submissions(self.gid)[0]["id"]
        self.assertTrue(lt.approve(sid)[0])
        self.assertFalse(lt.approve(sid)[0])                                            # 不能批两次
        self.assertEqual(lt.get_member(m["id"])["chars"], 14)
        # 驳回：放开去重，可以重新交
        fwd2 = FWD.replace("今天天气真好啊", "明天下雨吗")
        self.say("/字数统计", message_type=103, msg_elements=[{"content": fwd2}])
        sid2 = lt.pending_submissions(self.gid)[0]["id"]
        self.assertTrue(lt.reject(sid2)[0])
        self.assertIn("收到了", self.say("/字数统计", message_type=103, msg_elements=[{"content": fwd2}])[0])
        # 签到
        out = self.say("/签到")[0]
        self.assertIn("签到成功", out)
        self.assertIn("灵药+1", out)
        self.assertIn("今天已经签过", self.say("/签到")[0])
        # 限购
        lt.save_item_def(self.gid, "宝箱", on_sale=True, price=1, limit_total=3, limit_daily=2)
        self.say("/发放 全员 金币+20", member="ADM")
        self.assertEqual(lt.get_member(lt.member_by_name(self.gid, "小白")["id"])["coins"], 20)
        self.assertIn("买到", self.say("/购买 宝箱 2")[0])
        self.assertIn("每人每天最多买 2", self.say("/购买 宝箱")[0])
        # 发放给几个人 + 合成
        out = self.say("/发放 阿青、小白、路人 碎片+3", member="ADM")[0]
        self.assertIn("发放给 2 人", out)
        self.assertIn("没找到：路人", out)
        lt.save_recipe(self.gid, "宝剑", "碎片×3 金币×5", "宝剑×1")
        self.assertIn("宝剑：碎片×3 金币×5 → 宝剑×1", self.say("/合成表")[0])
        self.assertIn("得到 宝剑×1", self.say("/合成 宝剑")[0])
        self.assertIn("材料不够", self.say("/合成 宝剑")[0])
        # 帮助：管理员多一节
        self.assertIn("/签到", self.say("/长期群帮助")[0])
        self.assertNotIn("【管理员】", self.say("/帮助")[0])
        self.assertIn("【管理员】", self.say("/帮助", member="ADM")[0])
        # 改名
        self.assertIn("用法", self.say("/改名")[0])
        self.assertIn("改名为「阿青青」", self.say("/改名 阿青青")[0])
        self.assertIn("已经有人用了", self.say("/改名 小白")[0])
        self.say("/改名 阿青")
        # KPI
        lt.update_group(self.gid, kpi_chars=10, kpi_paras=2)
        self.assertIn("段数 2/2", self.say("/KPI")[0])
        self.assertIn("✅ 达标", self.say("/kpi")[0])
        self.assertIn("还没达标", self.say("/KPI 小白")[0])
        # 动态记全了
        kinds = {e["kind"] for e in lt.events(self.gid, limit=200)}
        self.assertTrue({"注册", "字数", "签到", "商城", "管理", "合成"} <= kinds, kinds)
        texts = " / ".join(e["text"] for e in lt.events(self.gid, limit=200))
        for want in ("等批准", f"批准了 #{sid}", f"驳回了 #{sid2}", "买了 宝箱×2", "合成 宝剑×1", "管理员在群里发放"):
            self.assertIn(want, texts)

    def test_mode_off_when_module_off(self):
        self._setup_group()
        from changri.core import trpg
        self.assertEqual(trpg.get_mode("G1"), "longterm")
        set_config("ext:longterm", "0")
        self.assertEqual(trpg.get_mode("G1"), "romance")                               # 关了按恋综走，数据留着
        self.assertIn("没打开", self.say("/模式 长期群", member="ADM")[0])
        set_config("ext:longterm", "1")
        self.assertEqual(trpg.get_mode("G1"), "longterm")
