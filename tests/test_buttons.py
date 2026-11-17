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


class BtnBot(QBot):
    reject = False      # True：凡是 Markdown 都当作 QQ 拒绝（没开权限）

    async def send(self, event, message, **kw):
        segs = [message] if isinstance(message, MessageSegment) else (list(message) if not isinstance(message, str) else [])
        if self.reject and any(sg.type == "markdown" for sg in segs):
            raise ActionFailed(Response(400, content=b"{}"))
        SENT.append(message)


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

    def test_long_label_cut(self):
        kb = buttons.keyboard([[Btn("一二三四五六七八九十一二三四", "x")]])
        self.assertLessEqual(len(kb["content"]["rows"][0]["buttons"][0]["render_data"]["label"]), buttons.LABEL_MAX)

    def test_formats_hide_disabled(self):
        self.assertIn("心动信", formats.available())
        set_config("ext:lovemail", "0")
        self.assertNotIn("心动信", formats.available())
        self.assertEqual(formats.template("心动信")[0], "off")
        self.assertNotIn("踩点", formats.available())                       # 踩点默认关
        set_config("stakeout_enabled", "1")
        self.assertIn("踩点", formats.available())
        self.assertEqual(formats.template("火星")[0], "unknown")


class ButtonsE2E(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("sheets", "relationship_details", "relationships", "role_groups", "roles", "config"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except Exception:  # noqa: BLE001 表名随版本可能不同
                    pass
        import changri.core.qqsend as qs
        qs._btn_off_until = 0.0
        SENT.clear()
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
        text, btns = self.say("格式私约")[0]
        self.assertIn("私约 时间 地点", text)
        self.assertEqual(btns, [("✏️ 填入输入框", "私约 ", False)])
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
