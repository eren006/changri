"""列表下面的按钮：排版（行数上限、前缀、只填入不发送）、「格式」菜单只列开着的功能、查看关系线/公知出名字按钮、
QQ 拒绝时退回原来的发法、后台开关能关。走真正的 NoneBot 事件分发。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "e.db"))
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent, MessageSegment  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402
from nonebot.adapters.qq.exception import ActionFailed  # noqa: E402
from nonebot.drivers import Response  # noqa: E402

from changri.core import buttons, formats, relations, roles, sheets  # noqa: E402
from changri.core.buttons import Btn  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

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
PUSHED: list = []       # 主动发到群的：(群, 消息)


class BtnBot(QBot):
    reject = False      # True：凡是 Markdown 都当作 QQ 拒绝（没开权限）

    async def send(self, event, message, **kw):
        segs = [message] if isinstance(message, MessageSegment) else (list(message) if not isinstance(message, str) else [])
        if self.reject and any(sg.type == "markdown" for sg in segs):
            raise ActionFailed(Response(400, content=b"{}"))
        SENT.append(message)

    async def send_to_group(self, group_openid, message, **kw):
        segs = [message] if isinstance(message, MessageSegment) else (list(message) if not isinstance(message, str) else [])
        if self.reject and any(sg.type == "markdown" for sg in segs):
            raise ActionFailed(Response(400, content=b"{}"))
        PUSHED.append((group_openid, message))
        from types import SimpleNamespace
        return SimpleNamespace(id=f"P{len(PUSHED)}")


def _parts(m) -> tuple[str, list]:
    """(文字或 Markdown 正文, 按钮列表[(label, data, enter)])；图片记成 <图>。"""
    if isinstance(m, str):
        return m, []
    segs = [m] if isinstance(m, MessageSegment) else list(m)
    text, btns = "<图>", []
    for sg in segs:
        if sg.type == "markdown":
            text = "[MD]" + sg.data["markdown"].content
        elif sg.type == "keyboard":
            for row in sg.data["keyboard"].content.rows:
                btns += [(b.render_data.label, b.action.data, b.action.enter) for b in row.buttons]
        elif sg.type == "text":
            text = sg.data["text"]
    return text, btns


class LayoutTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")

    def test_rows_capped_and_overflow_counted(self):
        rows, more = buttons.layout([Btn(str(i), f"x {i}") for i in range(30)], per_row=4)
        self.assertEqual(len(rows), buttons.MAX_ROWS)
        self.assertTrue(all(len(r) <= 4 for r in rows))
        self.assertEqual(more, 30 - 20)
        md, _kb = buttons.build("标题\n正文", [Btn(str(i), "x") for i in range(30)], per_row=4)
        self.assertIn("还有 10 个", md)

    def test_keyboard_fields(self):
        kb = buttons.keyboard([[Btn("张三", "查看关系线 张三"), Btn("摘", "摘心愿 3", enter=False), Btn("拒", "x", style="secondary")]])
        b1, b2, b3 = kb["content"]["rows"][0]["buttons"]
        self.assertEqual(b1["render_data"]["style"], 1)                      # 主要 = 蓝
        self.assertEqual(b3["render_data"]["style"], 0)                      # 次要 = 灰
        self.assertEqual(b1["action"]["type"], 2)
        self.assertEqual(b1["action"]["data"], "查看关系线 张三")
        self.assertTrue(b1["action"]["enter"])
        self.assertFalse(b2["action"]["enter"])                              # 只填进输入框
        self.assertEqual(b1["action"]["permission"]["type"], 2)              # 所有人可点，权限由指令自己判断

    def test_prefix_added_when_required(self):
        set_config("require_prefix", "1")
        kb = buttons.keyboard([[Btn("a", "查看关系线 张三")]])
        self.assertEqual(kb["content"]["rows"][0]["buttons"][0]["action"]["data"], "/查看关系线 张三")

    def test_markdown_escapes_player_text(self):
        md = buttons.markdown("📚 列表\n- *粗* <b>")
        self.assertTrue(md.startswith("**📚 列表**"))
        self.assertNotIn("*粗*", md)
        self.assertNotIn("<b>", md)

    def test_label_has_no_emoji(self):
        kb = buttons.keyboard([[Btn("↩️ 回短信", "x"), Btn("🎁回礼", "y")]])
        self.assertEqual([b["render_data"]["label"] for b in kb["content"]["rows"][0]["buttons"]], ["回短信", "回礼"])

    def test_long_label_cut(self):
        kb = buttons.keyboard([[Btn("一二三四五六七八九十一二三四", "x")]])
        self.assertLessEqual(len(kb["content"]["rows"][0]["buttons"][0]["render_data"]["label"]), buttons.LABEL_MAX)

    def test_formats_hide_disabled(self):
        self.assertIn("心动信", formats.available())
        set_config("ext:lovemail", "0")
        self.assertNotIn("心动信", formats.available())
        self.assertEqual(formats.template("心动信")[0], "off")
        self.assertNotIn("踩点", formats.available())                       # 踩点默认关
        set_config("ext:stakeout", "1")
        self.assertIn("踩点", formats.available())
        self.assertEqual(formats.template("火星")[0], "unknown")


class ButtonsE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("messages", "sheets", "rel_details", "relations", "role_groups", "roles", "config"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except Exception:  # noqa: BLE001 表名随版本可能不同
                    pass
        import changri.core.qqsend as qs
        qs._btn_off_until = 0.0
        SENT.clear(); PUSHED.clear()
        self.bot = BtnBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        self.bot.reject = False
        nonebot.get_bots()["B1"] = self.bot
        roles.create_role("甲", "B1", "G1", "M1")
        roles.create_role("乙", "B1", "G2", "M2")
        roles.create_role("丙", "B1", "G3", "M3")
        self.n = 0

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def say(self, text, member="M1", group="G1"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"b{self.n}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return [_parts(m) for m in SENT[before:]]

    def test_format_menu_only_enabled(self):
        out = self.say("格式")
        self.assertEqual(len(out), 1)
        labels = [b[0] for b in out[0][1]]
        self.assertIn("短信", labels)
        self.assertIn("心动信", labels)
        self.assertNotIn("踩点", labels)
        self.assertIn(("短信", "【短信】\n对象：\n内容：", False), out[0][1])      # 点了 = 空白格式填进输入框，不发出
        self.assertIn(("撤回", "格式撤回", True), out[0][1])                        # 撤回没有可填的：点了看说明
        set_config("ext:lovemail", "0")
        labels = [b[0] for b in self.say("格式")[0][1]]
        self.assertNotIn("心动信", labels)
        self.assertIn("没有开放", self.say("格式心动信")[0][0])

    def test_template_has_fill_button(self):
        text, btns = self.say("格式私约")[0]                 # 老叫法照样认，给的是新名字的模板
        self.assertIn("约会 时间 地点", text)
        self.assertIn("结束约会", text)
        self.assertEqual(btns, [("填入输入框", "约会 ", False)])
        text, btns = self.say("格式撤回")[0]
        self.assertIn("撤回", text)
        self.assertEqual(btns, [])                       # 撤回没有可以填的开头，不出按钮

    def test_filled_forms_parse(self):
        """填进输入框的空白格式，玩家在冒号后面写完直接发，要能被认出来。"""
        from changri.core import forms, lovemail
        f = forms.match_social(formats.fill("短信").replace("对象：", "对象：乙").replace("内容：", "内容：你好"))
        self.assertEqual((f["kind"], f["to"], f["content"], f["bad"]), ("sms", "乙", "你好", False))
        f = forms.match_social(formats.fill("送礼").replace("对象：", "对象：乙").replace("礼物：", "礼物：玫瑰"))
        self.assertEqual((f["kind"], f["to"], f["gift"], f["bad"]), ("gift", "乙", "玫瑰", False))
        body = formats.fill("心动信").split("\n", 1)[1].replace("【发送对象】", "【发送对象】乙").replace("【内容】", "【内容】晚安")
        self.assertEqual(lovemail.parse_post(body), ("乙", "晚安", "匿名"))

    def test_button_message_with_at_bot_still_works(self):
        """群里点按钮，QQ 会在内容前面自动加「@机器人」，没法关。机器人收到后要能去掉这个 @，照常认指令和竖着写的表单。"""
        def at_say(text):
            self.n += 1
            ev = GroupMessageCreateEvent.model_validate({
                "id": f"a{self.n}", "content": "<@BOTX> " + text, "timestamp": "2026-11-13T00:00:00+08:00",
                "author": {"id": "M1", "member_openid": "M1", "union_openid": "M1", "bot": False},
                "group_id": "G1", "group_openid": "G1",
                "mentions": [{"scope": "single", "bot": True, "id": "BOTX", "is_you": True, "member_openid": "BOTX", "username": "卡拉"}]})
            before = len(SENT)
            asyncio.new_event_loop().run_until_complete(self.bot.handle_event(ev))     # 走适配器：真实收消息时在这里去掉开头的 @
            return [_parts(m) for m in SENT[before:]]
        from nonebot.adapters.qq.models import User
        self.bot.self_info = User(id="BOTX", username="卡拉", bot=True)              # 连上 QQ 后才有，这里补上
        set_config("sms_cooldown_sec", "0")
        self.assertIn("格式短信", at_say("格式")[0][0])                                   # 直接发出的按钮
        out = at_say(formats.fill("短信").replace("对象：", "对象：乙").replace("内容：", "内容：按钮发的"))
        self.assertTrue(out, "表单没被认出来")                                            # 填进输入框的表单，补完再发
        with get_conn() as c:
            row = c.execute("SELECT kind, content FROM messages ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row["kind"], row["content"]), ("sms", "按钮发的"))

    def pushed_to(self, group):
        return [_parts(m) for g, m in PUSHED if g == group]

    def test_sms_arrives_with_reply_button(self):
        set_config("sms_cooldown_sec", "0")
        self.say("短信 乙 今晚有空吗")
        text, btns = self.pushed_to("G2")[-1]
        self.assertIn("今晚有空吗", text)
        self.assertIn('qqbot-at-user id="M2"', text)                          # 开头照样 @ 收件人
        self.assertEqual(btns, [("回短信", "短信 甲 ", False)])           # 名字填好、内容空着，只填进输入框

    def test_anonymous_sms_has_no_reply_button(self):
        """自定义署名 / 匿名的短信不挂回复按钮：按钮里是真名，挂了就暴露发件人。"""
        set_config("sms_cooldown_sec", "0"); set_config("allow_custom_sign", "1")
        self.say("神秘人短信 乙 猜猜我是谁")
        got = self.pushed_to("G2")
        self.assertTrue(got)
        self.assertTrue(all(b == [] for _t, b in got), got)
        self.assertFalse(any("甲" in t for t, _b in got), got)

    def test_gift_reply_buttons(self):
        from changri.core import messaging
        m = {"kind": "gift", "from_name": "甲", "sign": ""}
        self.assertEqual([(b.label, b.cmd) for b in messaging.reply_buttons(m)], [("回礼", "送礼 甲 "), ("回短信", "短信 甲 ")])
        self.assertEqual(messaging.reply_buttons({"kind": "gift", "from_name": "甲", "sign": "匿名"}), [])
        self.assertEqual(len(messaging.reply_buttons({"kind": "sms", "from_name": "甲", "sign": "甲"})), 1)     # 署名就是本人名字：照常挂

    def test_relation_detail_notice_buttons(self):
        self.say("拉线 乙 那天雨很大")
        text, btns = self.pushed_to("G2")[-1]
        self.assertIn("那天雨很大", text)
        self.assertEqual([(b[0], b[1], b[2]) for b in btns], [("回一段", "拉线 甲 ", False), ("看全部", "查看关系线 甲", True)])

    def test_push_falls_back_to_text_when_rejected(self):
        set_config("sms_cooldown_sec", "0")
        self.bot.reject = True
        self.say("短信 乙 你好")
        got = self.pushed_to("G2")
        self.assertTrue(any("你好" in t and not b for t, b in got), got)    # 没按钮权限：照常收到文字

    def test_mine_menu(self):
        text, btns = self.say("我的")[0]
        self.assertIn("「甲」", text)
        labels = [b[0] for b in btns]
        for x in ("时间线", "待回", "数量", "统计", "图鉴"):
            self.assertIn(x, labels)
        self.assertIn(("时间线", "时间线", True), btns)
        set_config("ext:rpg", "0")
        self.assertNotIn("角色卡", [b[0] for b in self.say("我的")[0][1]])     # 关掉的功能不出现
        self.assertEqual(self.say("我的角色")[0][0][:3], "角色：")              # 原来的「我的角色」不受影响
        self.assertEqual(self.say("我的不存在的东西"), [])                       # 打错的不理

    def test_relation_list_has_name_buttons(self):
        a, b, c = (roles.get_role_by_name(n) for n in ("甲", "乙", "丙"))
        relations.add_detail(a["id"], b["id"], "乙", "第一次见面")
        relations.add_detail(a["id"], c["id"], "丙", "雨天借伞")
        text, btns = self.say("查看关系线")[0]
        self.assertIn("关系线列表", text)
        self.assertEqual({(x[0], x[1]) for x in btns}, {("乙", "查看关系线 乙"), ("丙", "查看关系线 丙")})

    def test_gongzhi_list_buttons_then_single(self):
        set_config("gongzhi_public", "1")
        for n in ("甲", "乙"):
            sheets.submit(roles.get_role_by_name(n)["id"], "gongzhi", f"我是{n}", [])
        text, btns = self.say("查看公知")[0]
        self.assertIn("2 位", text)
        self.assertEqual([x[1] for x in btns], ["查看公知 甲", "查看公知 乙"])
        self.assertEqual(self.say("查看公知 乙"), [("<图>", [])])          # 点名字：只发这一个人的

    def test_gongzhi_falls_back_to_long_image(self):
        set_config("gongzhi_public", "1")
        for n in ("甲", "乙"):
            sheets.submit(roles.get_role_by_name(n)["id"], "gongzhi", f"我是{n}", [])
        self.bot.reject = True
        self.assertEqual(self.say("查看公知"), [("<图>", [])])              # 发不了按钮：照旧拼成一张长图

    def test_rejected_falls_back_to_text_and_pauses(self):
        self.bot.reject = True
        out = self.say("格式")
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0][0].startswith("[MD]"))
        self.assertIn("格式短信", out[0][0])
        self.bot.reject = False
        self.assertFalse(self.say("格式")[0][0].startswith("[MD]"))         # 一小时内不再试

    def test_switch_off(self):
        set_config("qq_buttons", "0")
        text, btns = self.say("格式")[0]
        self.assertEqual(btns, [])
        self.assertIn("格式短信", text)


if __name__ == "__main__":
    unittest.main()
