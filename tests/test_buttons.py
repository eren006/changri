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
            for t in ("messages", "sheets", "rel_details", "relations", "rpg_place_keys", "rpg_places", "role_groups", "roles", "config"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except Exception:  # noqa: BLE001 表名随版本可能不同
                    pass
        import changri.core.qqsend as qs
        qs._btn_off_until = 0.0
        qs._btn_fails = 0
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

    def test_help_hub(self):
        """「帮助」：最上层入口，一排直接发出的按钮；只列开着的功能；后面跟别的字不理。"""
        out = self.say("帮助")
        self.assertEqual(len(out), 1)
        text, btns = out[0]
        self.assertIn("帮助", text)
        for b in (("格式", "格式", True), ("我的", "我的", True), ("时间线", "时间线", True), ("好友列表", "好友列表", True)):
            self.assertIn(b, btns)
        set_config("ext:relations", "1")
        set_config("ext:wishes", "1")
        labels = [b[0] for b in self.say("帮助")[0][1]]
        self.assertIn("公知格式", labels)                    # 甲还没交公知
        self.assertIn("看心愿", labels)
        set_config("ext:wishes", "0")
        self.assertNotIn("看心愿", [b[0] for b in self.say("帮助")[0][1]])
        self.assertEqual(self.say("帮助我"), [])

    def test_help_hub_follows_player(self):
        """没交公知 → 「公知格式」；没有关系线 → 「开始关系线」；交了、拉了就变回「看」。"""
        set_config("ext:relations", "1")
        btns = self.say("帮助", member="M1", group="G1")[0][1]
        self.assertIn(("公知格式", "格式公知", True), btns)
        self.assertIn(("开始关系线", "格式关系线", True), btns)
        self.assertIn("提交公知", self.say("格式公知")[0][0])                          # 点了真的拿到格式
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        sheets.submit(a, "gongzhi", "我的公知")
        relations.add_detail(a, b, "乙", "第一段")
        btns = self.say("帮助", member="M1", group="G1")[0][1]
        self.assertIn(("我的公知", "查看公知 甲", True), btns)                          # 交了、还没展示：只看自己的
        self.assertIn(("看关系线", "查看关系线", True), btns)
        self.assertEqual(self.say("查看公知 甲")[0], ("<图>", []))                     # 点了直接出自己那份
        set_config("gongzhi_public", "1")                                              # 展示了：看所有人的
        self.assertIn(("看公知", "查看公知", True), self.say("帮助", member="M1", group="G1")[0][1])
        set_config("gongzhi_public", "0")
        btns = self.say("帮助", member="M3", group="G3")[0][1]                         # 丙还都没有
        self.assertIn(("公知格式", "格式公知", True), btns)
        self.assertIn(("开始关系线", "格式关系线", True), btns)
        from changri.core import deadlines
        deadlines.set("gongzhi", "2000-01-01 00:00")                                   # 截止锁上了：不再引导去写
        deadlines.set("rel", "2000-01-01 00:00")
        btns = self.say("帮助", member="M3", group="G3")[0][1]
        self.assertIn(("看公知", "查看公知", True), btns)
        self.assertIn(("看关系线", "查看关系线", True), btns)

    def test_contacts(self):
        """「好友列表」（「玩家名单」也一样）：名单 + 一人一个按钮，点了发「好友列表 名字」；张三的卡片上是能对 TA 做的事。"""
        set_config("ext:relations", "1"); set_config("ext:lovemail", "1")
        for word in ("好友列表", "联系人", "玩家名单"):
            text, btns = self.say(word, member="M1", group="G1")[-1]
            self.assertIn(("乙", "好友列表 乙", True), btns)
            self.assertNotIn("甲", [b[0] for b in btns])
            self.assertEqual(btns[-1], ("帮助", "帮助", True))
        text, btns = self.say("好友列表 乙", member="M1", group="G1")[0]
        self.assertIn("乙", text)
        got = {b[0]: (b[1], b[2]) for b in btns}
        self.assertEqual(got["短信"], ("【短信】\n对象：乙\n内容：", False))
        self.assertEqual(got["送礼"], ("【送礼】\n对象：乙\n礼物：\n留言：", False))
        self.assertEqual(got["心动信"], ("发送心动信\n【发送对象】乙\n【内容】\n【署名】", False))
        self.assertEqual(got["约会"], ("约会 时间 地点 乙", False))
        self.assertEqual(got["电话"], ("电话 时间 乙", False))
        self.assertEqual(got["拉线"], ("拉线 乙 ", False))                               # 还没有关系线
        self.assertNotIn("看公知", got)                                                  # 公知没展示
        self.assertEqual(btns[-1], ("换个人", "好友列表", True))
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        relations.add_detail(b, a, "甲", "乙先写")
        sheets.submit(b, "gongzhi", "乙的公知"); set_config("gongzhi_public", "1")
        text, btns = self.say("好友列表 乙", member="M1", group="G1")[0]
        self.assertIn("关系线：1 段，轮到你回", text)
        got = {b[0]: (b[1], b[2]) for b in btns}
        self.assertEqual(got["看关系线"], ("查看关系线 乙", True))
        self.assertNotIn("拉线", got)
        self.assertEqual(got["看公知"], ("查看公知 乙", True))
        from changri.core import dinner, rpg
        with get_conn() as c:
            c.execute("DELETE FROM rpg_place_keys"); c.execute("DELETE FROM rpg_places")
        set_config("ext:places", "1"); rpg.save_place("花园")
        set_config("ext:dinner", "1"); dinner.start(3, "无菜"); dinner.sit("甲", 1); dinner.sit("乙", 2)
        got = {b[0]: (b[1], b[2]) for b in self.say("好友列表 乙", member="M1", group="G1")[0][1]}
        self.assertEqual(got["约会"], ("约会 乙", True))                                 # 有预设地点：下一步选地点
        self.assertEqual(got["戳一下"], ("戳你一下 乙", True))                            # 邻座
        self.assertNotIn("戳一下", {b[0] for b in self.say("好友列表 丙", member="M1", group="G1")[0][1]})
        dinner.stop()
        self.assertIn("这是你自己", self.say("好友列表 甲", member="M1", group="G1")[0][0])
        self.assertIn("找不到", self.say("好友列表 不存在", member="M1", group="G1")[0][0])

    def test_date_picker_card(self):
        """「约会」「电话」后面不写东西：选人卡片，点名字填「约会 时间 地点 名字」，不含自己；常联系的在前；最后是自己写、帮助。"""
        a, c = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("丙")["id"]
        relations.add_detail(a, c, "丙", "认识")                     # 甲和丙有关系线：丙排前面
        text, btns = self.say("约会", member="M1", group="G1")[0]
        self.assertIn("选一个人", text)
        self.assertEqual(btns[0], ("丙", "约会 时间 地点 丙", False))
        self.assertIn(("乙", "约会 时间 地点 乙", False), btns)
        self.assertNotIn("甲", [b[0] for b in btns])
        self.assertEqual(btns[-2:], [("自己写", "约会 ", False), ("帮助", "帮助", True)])
        _t, btns = self.say("电话", member="M1", group="G1")[0]
        self.assertIn(("乙", "电话 时间 乙", False), btns)
        self.assertIn("「时间、地点」两个字还没换", self.say("约会 时间 地点 乙", member="M1", group="G1")[0][0])   # 忘了换占位字
        self.assertIn("「地点」两个字还没换", self.say("约会 1400-1500 地点 乙", member="M1", group="G1")[0][0])

    def test_date_picker_with_places(self):
        """有预设地点：选人卡片点名字直接发「约会 名字」，回地点卡片；上锁没钥匙的不列；只能用预设地点时没有「别的地方」。"""
        from changri.core import rpg
        with get_conn() as c:
            c.execute("DELETE FROM rpg_place_keys"); c.execute("DELETE FROM rpg_places")
        set_config("ext:places", "1")
        rpg.save_place("咖啡厅"); rpg.save_place("花园"); rpg.save_place("密室", locked=True)
        _t, btns = self.say("约会", member="M1", group="G1")[0]
        self.assertIn(("乙", "约会 乙", True), btns)                                   # 第一步：点了直接发
        text, btns = self.say("约会 乙", member="M1", group="G1")[0]
        self.assertIn("和 乙 去哪儿", text)
        self.assertIn(("咖啡厅", "约会 时间 咖啡厅 乙", False), btns)
        self.assertIn(("花园", "约会 时间 花园 乙", False), btns)
        self.assertNotIn("密室", [b[0] for b in btns])                                 # 没钥匙
        self.assertIn(("别的地方", "约会 时间 地点 乙", False), btns)
        self.assertEqual(btns[-1], ("换个人", "约会", True))
        a = roles.get_role_by_name("甲")["id"]
        rpg.set_key(a, next(p["id"] for p in rpg.places() if p["name"] == "密室"), True)
        set_config("place_system_enabled", "1")                                          # 只能用预设地点
        _t, btns = self.say("约会 乙/丙", member="M1", group="G1")[0]
        self.assertIn(("密室", "约会 时间 密室 乙/丙", False), btns)
        self.assertNotIn("别的地方", [b[0] for b in btns])
        self.assertIn("用法", self.say("约会 不存在的人", member="M1", group="G1")[0][0])   # 名字不认得：照旧给用法
        set_config("ext:places", "0")                                                    # 关了地点：回到一步填好
        _t, btns = self.say("约会", member="M1", group="G1")[0]
        self.assertIn(("乙", "约会 时间 地点 乙", False), btns)

    def test_wish_list_buttons(self):
        """看心愿：一个心愿一段；自己挂的给「撤」，别人的给「摘」；都只填不发。挂心愿不带东西：有预设地点给地点按钮。"""
        import time as _t
        from changri.core import rpg
        set_config("ext:wishes", "1")
        if "changri.plugins.wishes" not in {p.module_name for p in nonebot.get_loaded_plugins()}:
            nonebot.load_plugin("changri.plugins.wishes")                        # 心愿默认关，启动时没加载
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        with get_conn() as c:
            c.execute("DELETE FROM wishes")
            mine = c.execute("INSERT INTO wishes(poster_id, start_hm, end_hm, place, content, sign, status, created_ts) VALUES(?,?,?,?,?,?,?,?)",
                             (a, "14:00", "15:00", "花园", "一起散步", "", "open", int(_t.time()))).lastrowid
            other = c.execute("INSERT INTO wishes(poster_id, start_hm, end_hm, place, content, sign, status, created_ts) VALUES(?,?,?,?,?,?,?,?)",
                              (b, "16:00", "17:00", "图书馆", "陪我看书", "神秘人", "open", int(_t.time()))).lastrowid
        text, btns = self.say("看心愿", member="M1", group="G1")[0]
        self.assertIn(f"＃{other}", text); self.assertIn("陪我看书", text); self.assertIn("还剩", text)   # Markdown 里 # 转成全角
        self.assertNotIn("｜", text)                                                     # 不再挤成一行
        self.assertIn((f"撤 #{mine}", f"撤心愿 {mine}", False), btns)
        self.assertIn((f"摘 #{other}", f"摘心愿 {other}", False), btns)
        text, btns = self.say("撤心愿", member="M1", group="G1")[0]
        self.assertIn("你挂着的心愿", text)
        self.assertEqual(btns, [(f"撤 #{mine}", f"撤心愿 {mine}", False)])
        with get_conn() as c:
            c.execute("DELETE FROM rpg_place_keys"); c.execute("DELETE FROM rpg_places")
        out = self.say("挂心愿", member="M1", group="G1")[0]                              # 没有预设地点：用法 + 填入
        self.assertEqual(out[1], [("填入输入框", "挂心愿 时间 地点 ", False)])
        with get_conn() as c:
            c.execute("DELETE FROM rpg_place_keys"); c.execute("DELETE FROM rpg_places")
        set_config("ext:places", "1"); rpg.save_place("花园")
        text, btns = self.say("挂心愿", member="M1", group="G1")[0]
        self.assertIn("选地点", text)
        self.assertIn(("花园", "挂心愿 时间 花园 ", False), btns)
        self.assertIn(("别的地方", "挂心愿 时间 地点 ", False), btns)

    def test_dinner_seat_buttons(self):
        """晚餐：座位表下面挂空位按钮，点了直接发「入座 N」；坐了的位子在新的一条里就没有了；坐不上的也给现在的空位。"""
        from changri.core import dinner
        set_config("ext:dinner", "1")
        if "changri.plugins.dinner" not in {p.module_name for p in nonebot.get_loaded_plugins()}:
            nonebot.load_plugin("changri.plugins.dinner")                        # 晚餐默认关，启动时没加载
        dinner.start(4, "无菜")
        text, btns = self.say("入座", member="M1", group="G1")[0]
        self.assertIn("点空位入座", text)
        self.assertEqual(btns, [("1号", "入座 1", True), ("2号", "入座 2", True), ("3号", "入座 3", True), ("4号", "入座 4", True)])
        text, btns = self.say("入座 3", member="M1", group="G1")[0]
        self.assertIn("3. 甲", text)
        self.assertNotIn("3号", [b[0] for b in btns])
        text, btns = self.say("入座 3", member="M2", group="G2")[0]                     # 有人了
        self.assertIn("已经被 甲 坐了", text)
        self.assertEqual([b[0] for b in btns], ["1号", "2号", "4号"])
        for n, m in ((1, "M2"), (2, "M3")):
            self.say(f"入座 {n}", member=m, group=m.replace("M", "G"))
        roles.create_role("丁", "B1", "G4", "M4")
        text, btns = self.say("入座 4", member="M4", group="G4")[0]
        self.assertIn("座位都坐满了", text)
        self.assertEqual(btns, [])
        dinner.stop()

    def test_form_pickers(self):
        """只发「短信」「送礼」「心动信」「拉线」：选人卡片，点名字把对象填好；写了一半给用法 + 填入输入框。"""
        set_config("ext:relations", "1"); set_config("ext:lovemail", "1")
        _t, btns = self.say("短信", member="M1", group="G1")[0]
        self.assertIn(("乙", "【短信】\n对象：乙\n内容：", False), btns)
        self.assertNotIn("甲", [b[0] for b in btns])
        _t, btns = self.say("送礼", member="M1", group="G1")[0]
        self.assertIn(("乙", "【送礼】\n对象：乙\n礼物：\n留言：", False), btns)
        text, btns = self.say("短信 乙", member="M1", group="G1")[0]                   # 写了一半
        self.assertIn("短信 收信人 内容", text)
        self.assertEqual(btns, [("填入输入框", "【短信】\n对象：乙\n内容：", False)])
        _t, btns = self.say("心动信", member="M1", group="G1")[0]
        self.assertIn(("乙", "发送心动信\n【发送对象】乙\n【内容】\n【署名】", False), btns)
        _t, btns = self.say("拉线", member="M1", group="G1")[0]
        self.assertIn(("乙", "拉线 乙 ", False), btns)
        text, btns = self.say("拉线 乙", member="M1", group="G1")[0]                    # 没写内容
        self.assertEqual(btns, [("填入输入框", "拉线 乙 ", False)])
        from changri.core import deadlines
        deadlines.set("rel", "2000-01-01 00:00")                                           # 截止了：不给选人
        self.assertNotIn("选一个人", self.say("拉线", member="M1", group="G1")[0][0])
        deadlines.set("rel", "")

    def test_lovemail_one_line_is_anonymous(self):
        """一行写的心动信不写署名也是匿名（跟选人卡片「署名不写就是匿名」一致），回执写明署名。"""
        set_config("ext:lovemail", "1")
        text = self.say("心动信 乙 晚安", member="M1", group="G1")[0][0]
        self.assertIn("署名：匿名", text)
        self.assertNotIn("署名：甲", text)
        text = self.say("发送心动信\n【发送对象】乙\n【内容】早\n【署名】小风", member="M1", group="G1")[0][0]
        self.assertIn("署名：小风", text)

    def test_relation_detail_next(self):
        set_config("ext:relations", "1")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        relations.add_detail(a, b, "乙", "第一段")
        out = self.say("查看关系线 乙", member="M1", group="G1")
        self.assertEqual(out[-1][1], [("接着写", "拉线 乙 ", False), ("回列表", "查看关系线", True)])

    def test_usage_has_fill(self):
        text, btns = self.say("约会 1400-1500", member="M1", group="G1")[0]
        self.assertIn("用法", text)
        self.assertEqual(btns, [("填入输入框", "约会 ", False)])
        _t, btns = self.say("电话 1400-1500", member="M1", group="G1")[0]
        self.assertEqual(btns, [("填入输入框", "电话 ", False)])

    def test_dinner_poke_neighbors(self):
        from changri.core import dinner
        set_config("ext:dinner", "1")
        if "changri.plugins.dinner" not in {p.module_name for p in nonebot.get_loaded_plugins()}:
            nonebot.load_plugin("changri.plugins.dinner")
        dinner.start(4, "无菜")
        self.assertIn("还没入座", self.say("戳你一下", member="M1", group="G1")[0][0])
        dinner.sit("甲", 1); dinner.sit("乙", 2); dinner.sit("丙", 4)
        text, btns = self.say("戳你一下", member="M1", group="G1")[0]
        self.assertIn("旁边坐着", text)
        self.assertEqual(sorted(b[1] for b in btns), ["戳你一下 丙", "戳你一下 乙"])          # 首尾相邻
        self.assertTrue(all(b[2] for b in btns))
        self.assertEqual(dinner.neighbors("乙"), ["甲"])
        dinner.stop()

    def test_card_followed_by_actions(self):
        """「角色卡」先发卡片，后面跟一条「接下来做什么？」挂按钮；关系线有待回的把数写在按钮上。"""
        set_config("ext:relations", "1")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        relations.add_detail(b, a, "甲", "乙先写了一段，轮到甲回")
        out = self.say("角色卡", member="M1", group="G1")
        self.assertGreaterEqual(len(out), 2)
        text, btns = out[-1]
        self.assertIn("接下来做什么", text)
        cmds = [x[1] for x in btns]
        for c in ("时间线", "我的待回", "查看关系线", "好友列表", "帮助"):
            self.assertIn(c, cmds)
        self.assertIn(("关系线 1待回", "查看关系线", True), btns)
        set_config("ext:relations", "0")
        self.assertNotIn("查看关系线", [x[1] for x in self.say("角色卡", member="M1", group="G1")[-1][1]])

    def test_npc_after_players(self):
        """选人按钮：NPC 一律排在玩家后面，就算跟 NPC 常联系也一样。"""
        from changri.core import datepick
        with get_conn() as c:
            c.execute("UPDATE roles SET is_npc=1 WHERE name='乙'")
        a, b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]
        relations.add_detail(a, b, "乙", "跟 NPC 有关系线")
        names = [r["name"] for r in datepick.people(a)[0]]
        self.assertEqual(names[-1], "乙")
        _t, btns = self.say("短信", member="M1", group="G1")[0]
        self.assertLess([x[0] for x in btns].index("丙"), [x[0] for x in btns].index("乙"))

    def test_btn_name_rule(self):
        from changri.core import charprofile
        self.assertEqual(charprofile.btn_name("欧阳娜娜子", ""), "欧阳娜")        # 最多前 3 个字
        self.assertEqual(charprofile.btn_name("欧阳娜娜子", "娜娜"), "娜娜")      # 有简称用简称

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
        after = [b[0] for b in self.say("我的")[0][1]]
        self.assertNotIn("背包", after)                                        # 关掉的功能不出现
        self.assertIn("角色卡", after)                                         # 角色卡是常规功能，商城关着也在
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

    def test_gongzhi_list_splits_players_and_npcs(self):
        """玩家、NPC 分两条发，各自一组按钮；只有玩家时只发一条。"""
        set_config("gongzhi_public", "1")
        for n in ("甲", "乙", "丙"):
            sheets.submit(roles.get_role_by_name(n)["id"], "gongzhi", f"我是{n}", [])
        with get_conn() as c:
            c.execute("UPDATE roles SET is_npc=1 WHERE name='乙'")
        out = self.say("查看公知")
        self.assertEqual(len(out), 2)
        self.assertIn("玩家", out[0][0])
        self.assertIn("2 位", out[0][0])
        self.assertEqual([x[1] for x in out[0][1]], ["查看公知 甲", "查看公知 丙"])
        self.assertIn("NPC", out[1][0])
        self.assertEqual([x[1] for x in out[1][1]], ["查看公知 乙"])

    def test_gongzhi_falls_back_to_long_image(self):
        set_config("gongzhi_public", "1")
        for n in ("甲", "乙"):
            sheets.submit(roles.get_role_by_name(n)["id"], "gongzhi", f"我是{n}", [])
        self.bot.reject = True
        self.assertEqual(self.say("查看公知"), [("<图>", [])])              # 发不了按钮：照旧拼成一张长图

    def test_rejected_once_falls_back_then_retries(self):
        """偶然被拒一次：这一条改发纯文字，下一条照样试按钮（以前会全体停一小时，用户以为按钮坏了）。"""
        self.bot.reject = True
        out = self.say("格式")
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0][0].startswith("[MD]"))
        self.assertIn("格式短信", out[0][0])
        self.bot.reject = False
        self.assertTrue(self.say("格式")[0][0].startswith("[MD]"))

    def test_rejected_in_a_row_pauses(self):
        import changri.core.qqsend as qs
        self.bot.reject = True
        for _ in range(qs.BTN_FAIL_LIMIT - 1):
            self.say("格式")
        self.assertEqual(qs._btn_off_until, 0.0)                            # 还没到次数，不暂停
        self.say("格式")
        self.assertGreater(qs._btn_off_until, 0)
        self.assertLessEqual(qs._btn_off_until - __import__("time").time(), qs.BTN_COOLDOWN)
        self.bot.reject = False
        self.assertFalse(self.say("格式")[0][0].startswith("[MD]"))         # 暂停期内不再试
        qs._btn_off_until = 0.0
        self.assertTrue(self.say("格式")[0][0].startswith("[MD]"))          # 暂停过了又会试

    def test_success_resets_reject_count(self):
        import changri.core.qqsend as qs
        for _ in range(qs.BTN_FAIL_LIMIT - 1):
            self.bot.reject = True
            self.say("格式")
            self.bot.reject = False
            self.assertTrue(self.say("格式")[0][0].startswith("[MD]"))      # 中间成功一次就清零
        self.bot.reject = True
        self.say("格式")
        self.assertEqual(qs._btn_off_until, 0.0)                            # 不是连续的，不暂停

    def test_card_plain_fallback(self):
        """按钮发不出去（关了 / QQ 拒收）：选人卡片改发纯文字，列出名字和照抄的格式，不说「点」。"""
        set_config("ext:lovemail", "1")
        set_config("qq_buttons", "0")
        for cmd, want in (("短信", "【短信】"), ("心动信", "【发送对象】"), ("约会", "约会 时间 地点 对方"), ("电话", "电话 时间 对方")):
            out = self.say(cmd, member="M1", group="G1")
            text = "\n".join(o[0] for o in out)
            self.assertTrue(all(o[1] == [] for o in out), out)
            self.assertIn("乙", text, cmd)
            self.assertIn("丙", text, cmd)
            self.assertIn(want, text, cmd)
            self.assertNotIn("点", text.replace("地点", ""), cmd)
        set_config("qq_buttons", "1")
        self.bot.reject = True                                                 # QQ 拒收 Markdown：也退回同样的纯文字
        text = "\n".join(o[0] for o in self.say("送礼", member="M1", group="G1"))
        self.assertIn("乙、丙", text)
        self.assertIn("【送礼】", text)
        self.assertNotIn("点名字", text)

    def test_card_capacity_from_layout(self):
        """名字多到放不下：按 5 行 × 4 个排，名字 18 个 +「自己写」「帮助」正好 20 个都在，「还有 N 位」算对。"""
        from changri.core import buttons as bt, datepick
        for i in range(30):
            roles.create_role(f"人{i:02d}", "B1", f"GX{i}", f"MX{i}")
        text, items, _plain = datepick.form_card("短信", roles.get_role_by_name("甲")["id"])
        self.assertEqual(len(items), bt.MAX_ROWS * bt.CARD_PER_ROW)
        self.assertEqual([x[0] for x in items[-2:]], ["自己写", "帮助"])
        self.assertIn(f"还有 {32 - datepick.MAX_ITEMS} 位", text)                # 乙、丙 + 30 个
        _t, btns = self.say("短信", member="M1", group="G1")[0]
        self.assertIn("自己写", [b[0] for b in btns])
        self.assertIn("帮助", [b[0] for b in btns])

    def test_switch_off(self):
        set_config("qq_buttons", "0")
        text, btns = self.say("格式")[0]
        self.assertEqual(btns, [])
        self.assertIn("格式短信", text)


if __name__ == "__main__":
    unittest.main()
