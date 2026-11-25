import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "trpglog.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

from nonebot.adapters.qq import GroupMessageCreateEvent  # noqa: E402
from nonebot.matcher import current_event  # noqa: E402

from changri.admin import create_app  # noqa: E402
from changri.core import trpg, trpglog, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402
from changri.plugins import _logtap  # noqa: E402

TABLES = ("trpg_log_members", "trpg_log_lines", "trpg_logs", "trpg_observers", "trpg_attr_history", "trpg_active",
          "trpg_card_attrs", "trpg_cards", "trpg_identities", "trpg_players", "config", "web_users")


def ev(text, member="M1", group="G1"):
    return SimpleNamespace(group_openid=group, author=SimpleNamespace(member_openid=member), get_plaintext=lambda: text)


def clear():
    init_db()
    with get_conn() as c:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
    _logtap._last_line.clear()


class LogState(unittest.TestCase):
    def setUp(self):
        clear()

    def test_states_pause_end_halt_reopen(self):
        log, _ = trpglog.new_log("G1", "战役一", "M1")
        self.assertEqual(log["status"], "on")
        self.assertIsNone(trpglog.new_log("G1", "战役二", "M1")[0])          # 还有没结束的，不能新建
        self.assertTrue(trpglog.pause("G1")[0])
        self.assertFalse(trpglog.pause("G1")[0])
        self.assertIsNone(trpglog.recording_id("G1"))                        # 暂停不记
        self.assertTrue(trpglog.resume("G1")[0])
        done, _ = trpglog.finish("G1")
        self.assertEqual(done["status"], "ended")
        self.assertIsNone(trpglog.current("G1"))
        self.assertTrue(trpglog.new_log("G1", "战役二", "M1")[0])            # 结束后才能开新的
        trpglog.finish("G1", halt=True)
        self.assertEqual(trpglog.find("G1", "战役二")["status"], "halted")
        ok, msg = trpglog.resume("G1", "战役一")                               # 已结束的可以接着记
        self.assertTrue(ok, msg)
        self.assertEqual(trpglog.recording_id("G1"), log["id"])
        self.assertFalse(trpglog.resume("G1", "战役二")[0])                    # 当前还有一个，不能同时两个
        self.assertEqual(trpglog.find("G1", trpglog.short(log))["id"], log["id"])   # 短编号可以代替名字
        self.assertIsNone(trpglog.find("G2", "战役一"))                        # 别的群看不到

    def test_skip_rules(self):
        self.assertTrue(trpglog.should_skip("（场外话）"))
        self.assertTrue(trpglog.should_skip("(ooc)"))
        self.assertTrue(trpglog.should_skip("【备注】"))
        self.assertTrue(trpglog.should_skip("// 备忘"))
        self.assertFalse(trpglog.should_skip("他说（小声）你好"))              # 括号不在开头不算
        self.assertFalse(trpglog.should_skip("/ra 侦查", is_command=True))      # 默认指令文字也记
        from changri.core.db import set_config
        set_config("trpg_log_skip_cmds", "1")
        self.assertTrue(trpglog.should_skip("/ra 侦查", is_command=True))
        set_config("trpg_log_skip_brackets", "0")
        self.assertFalse(trpglog.should_skip("（场外话）"))


class LogRecording(unittest.TestCase):
    def setUp(self):
        clear()
        trpg.set_mode("G1", "trpg")

    def test_record_incoming_rules(self):
        _logtap.record_incoming(ev("没开 log 不记"))
        log, _ = trpglog.new_log("G1", "测试", "M1")
        for t in ("大家好", "（场外）不记", "/log off 管理指令不记", "log off 不带前缀也是指令", "/ra 侦查 50", "ra 侦查 50"):
            _logtap.record_incoming(ev(t))
        texts = [l["text"] for l in trpglog.lines(log["id"])]
        self.assertEqual(texts, ["大家好", "/ra 侦查 50", "ra 侦查 50"])             # 默认不带前缀也算指令，管理类的不记
        from changri.core.db import set_config
        set_config("require_prefix", "1")                                        # 打开「必须带前缀」后，不带前缀的就是普通发言
        _logtap.record_incoming(ev("log 一下就是聊天"))
        self.assertEqual(trpglog.lines(log["id"])[-1]["text"], "log 一下就是聊天")
        trpglog.pause("G1")
        _logtap.record_incoming(ev("暂停时不记"))
        self.assertEqual(len(trpglog.lines(log["id"])), 4)

    def test_say_observer_and_romance_group(self):
        log, _ = trpglog.new_log("G1", "测试", "M1")
        self.assertTrue(_logtap.record_say(ev("台词一"), "台词一"))
        self.assertFalse(_logtap.record_say(ev("（旁白）"), "（旁白）"))
        pid = trpg.player_for("M2", "G1")
        trpglog.toggle_observer("G1", pid)
        _logtap.record_incoming(ev("旁观者说话", member="M2"))
        self.assertEqual([l["text"] for l in trpglog.lines(log["id"])], ["台词一"])
        trpg.set_mode("G1", "romance")                                       # 恋综模式的群完全不记
        _logtap.record_incoming(ev("恋综里不记"))
        self.assertEqual(len(trpglog.lines(log["id"])), 1)

    def test_bot_reply_pairing_and_stats_and_history(self):
        log, _ = trpglog.new_log("G1", "测试", "M1")
        e = mock.MagicMock(spec=GroupMessageCreateEvent)
        e.group_openid, e.author = "G1", SimpleNamespace(member_openid="M1")
        e.get_plaintext = lambda: "/ra 侦查 50"
        trpg.active_card("G1", trpg.player_for("M1", "G1"), create=True)
        _logtap.record_incoming(e)
        tok = current_event.set(e)
        try:
            _logtap.tap_reply(SimpleNamespace(plugin_name="dice"), "侦查 D100=12/50 成功")
            _logtap.tap_reply(SimpleNamespace(plugin_name="dice"), "侦查 D100=99/50 大失败")
            _logtap.tap_reply(SimpleNamespace(plugin_name="play"), "别的插件的回复不记")
        finally:
            current_event.reset(tok)
        ls = trpglog.lines(log["id"])
        self.assertEqual([l["kind"] for l in ls], ["say", "dice", "dice"])
        self.assertEqual(ls[1]["ref"], ls[0]["id"])
        card = trpg.active_card("G1", trpg.player_for("M1", "G1"))
        trpg.apply_st(card["id"], [("力量", "60")], log_id=trpglog.recording_id("G1"))
        s = trpglog.stats(log["id"])
        self.assertEqual((s["lines"], s["dice"], s["success"], s["fail"], s["changes"]), (3, 2, 1, 1, 1))
        self.assertIn("(", trpglog.export_txt(log["id"]))
        self.assertEqual([b["name"] for b in trpglog.logs_of_card(card["id"])], ["测试"])
        self.assertEqual(trpg.card_history(card["id"])[0]["log_id"], log["id"])
        trpglog.delete(log["id"])
        self.assertEqual(trpglog.lines(log["id"]), [])


class LogPeople(unittest.TestCase):
    def setUp(self):
        clear()

    def test_per_person_stats_and_highlights(self):
        log, _ = trpglog.new_log("G1", "战报", "M1")
        lid = log["id"]

        def roll(who, name, *results):
            say = trpglog.add_line(lid, who, name, "say", "/ra 侦查")
            for r in results:
                trpglog.add_line(lid, "", "骰子", "dice", f"侦查 检定：D100=1/50 —— {r}。", say)
        roll("M1", "小明", "大成功", "成功", "成功", "失败")
        roll("M2", "小红", "大失败", "失败", "失败", "成功")
        trpglog.add_line(lid, "", "骰子", "dice", "D20 = 7", None)                    # 对不上人的普通掷骰
        card = trpg.active_card("G1", trpg.player_for("M2", "G1"), create=True)
        trpg.apply_st(card["id"], [("理智", "60")], log_id=lid)
        trpg.apply_st(card["id"], [("理智", "-5")], log_id=lid)
        trpg.apply_st(card["id"], [("理智", "+1")], log_id=lid)                     # 回升的不算损失
        other = trpg.active_card("G1", trpg.player_for("M3", "G1"), create=True)       # 同名的另一张卡，损失不能混进来
        self.assertEqual(other["name"], card["name"])
        trpg.apply_st(other["id"], [("理智", "60")], log_id=lid)
        trpg.apply_st(other["id"], [("理智", "-1")], log_id=lid)
        s = trpglog.stats(lid)
        self.assertEqual(sorted(x["理智"] for x in s["losses"]), [1, 5])
        ppl = {p["name"]: p for p in s["people"]}
        self.assertEqual((ppl["小明"]["crit"], ppl["小明"]["ok"], ppl["小明"]["bad"], ppl["小明"]["rate"]), (1, 2, 1, 75))
        self.assertEqual((ppl["小红"]["fumble"], ppl["小红"]["rate"]), (1, 25))
        self.assertEqual((ppl["（不明）"]["dice"], ppl["（不明）"]["rate"]), (1, None))
        text = trpglog.stats_text(trpglog.get(lid))
        for want in ("【每个人】", "小明：骰 4 次（大成功 1、成功 2、失败 1）", "大成功最多：小明", "大失败最多：小红",
                     "骰运最好：小明", "骰运最差：小红", "理智掉得最多：", "（-5）"):
            self.assertIn(want, text)

    def test_no_forced_highlights(self):
        log, _ = trpglog.new_log("G1", "安静", "M1")
        trpglog.add_line(log["id"], "M1", "小明", "say", "大家好")
        text = trpglog.stats_text(trpglog.get(log["id"]))
        self.assertNotIn("【每个人】", text)
        self.assertNotIn("【本场之最】", text)


class LogAdmin(unittest.TestCase):
    def test_pages_and_rules(self):
        clear()
        webusers.create_user("boss", "longenough1", "owner")
        log, _ = trpglog.new_log("G1", "测试", "M1")
        trpglog.add_line(log["id"], "M1", "小明", "say", "你好")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        self.assertIn("测试", c.get("/trpglog").get_data(as_text=True))
        self.assertIn("你好", c.get(f"/trpglog/{log['id']}").get_data(as_text=True))
        say = trpglog.add_line(log["id"], "M1", "小明", "say", "/ra 侦查")
        trpglog.add_line(log["id"], "", "骰子", "dice", "侦查 检定：D100=1/50 —— 大成功。", say)
        page = c.get(f"/trpglog/{log['id']}").get_data(as_text=True)
        self.assertIn("<h2>每个人</h2>", page)
        self.assertIn("大成功最多：小明", page)
        c.post("/trpglog/rules", data={"_csrf": tok("/trpglog"), "prefixes": "//\n!!"})
        r = trpglog.rules()
        self.assertEqual((r["brackets"], r["cmds"], r["prefixes"]), (False, False, ["//", "!!"]))   # 没勾选 = 关
        c.post(f"/trpglog/{log['id']}/delete", data={"_csrf": tok("/trpglog")})
        self.assertIsNone(trpglog.get(log["id"]))


class LogRender(unittest.TestCase):
    def setUp(self):
        clear()

    def _log(self):
        log, _ = trpglog.new_log("G1", "雾港", "M1")
        trpglog.add_line(log["id"], "M1", "小明", "say", "<script>alert(1)</script> 你好")
        trpglog.add_line(log["id"], "M2", "阿红", "say", "我也来了")
        trpglog.add_line(log["id"], "", "骰子", "dice", "侦查 D100=3/50 大成功")
        trpglog.add_line(log["id"], "", "骰子", "dice", "力量 D100=88/60 失败")
        return trpglog.get(log["id"])

    def test_render_escapes_and_colors(self):
        from changri.core import trpgrender
        log = self._log()
        out = trpgrender.render_html(log, trpglog.lines(log["id"]))
        self.assertNotIn("<script>alert(1)", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn('lv lv-crit">大成功', out)
        self.assertIn('lv lv-bad">失败', out)
        self.assertIn("小明", out)
        self.assertIn("prefers-color-scheme:dark", out)
        custom = trpgrender.parse_style('{"colors":{"小明":"#123456","阿红":"javascript:x"},"theme":"nope"}')
        self.assertEqual(custom["colors"], {"小明": "#123456"})              # 非法颜色丢掉
        self.assertEqual(custom["theme"], "auto")
        self.assertIn("--c:#123456", trpgrender.render_html(log, trpglog.lines(log["id"]), custom))
        off = trpgrender.render_html(log, trpglog.lines(log["id"]), trpgrender.parse_style('{"dice_hl":false}'))
        self.assertNotIn("lv-crit", off.split("</style>")[1])

    def test_same_character_same_color_across_logs(self):
        from changri.core import trpgrender
        st = trpgrender.parse_style("{}")
        mk = lambda *names: [{"kind": "say", "speaker_name": n} for n in names]
        a = trpgrender.color_map(mk("小明", "阿红"), st)
        b = trpgrender.color_map(mk("老王", "阿红", "小明"), st)                 # 换了顺序、多了人，颜色不变
        self.assertEqual((a["小明"], a["阿红"]), (b["小明"], b["阿红"]))
        many = trpgrender.color_map(mk(*[f"角色{i}" for i in range(10)]), st)
        self.assertEqual(len(set(many.values())), 10)                             # 一份 log 里不撞色
        log = self._log()
        out = trpgrender.render_html(log, trpglog.lines(log["id"]))
        k = re.search(r'class="who (s\d)">阿红', out).group(1)
        self.assertRegex(out, rf'class="ln dice {k}[" ]')                         # 阿红后面的骰点跟着阿红的颜色
        self.assertFalse(set(trpgrender.PALETTE[-2:]) & set(a.values()))             # 人少时不分到靛蓝、灰色

    def test_reading_aids(self):
        from changri.core import trpgrender
        self.assertTrue(trpgrender.is_ooc("（我去吃个饭）"))
        self.assertTrue(trpgrender.is_ooc("(brb)"))
        self.assertTrue(trpgrender.is_ooc("// 等下早点下"))
        self.assertFalse(trpgrender.is_ooc("（笑）“你好”（转身）"))              # 头尾是括号、中间是戏，不算场外
        self.assertFalse(trpgrender.is_ooc("“你好”"))
        log, _ = trpglog.new_log("G1", "雾港", "M1")
        trpglog.add_line(log["id"], "M1", "小明", "say", "我先说一句")
        cmd = trpglog.add_line(log["id"], "M2", "阿红", "say", ".ra 聆听")
        trpglog.add_line(log["id"], "", "骰子", "dice", "阿红的聆听检定：D100=12/60 —— 困难成功", cmd)
        trpglog.add_line(log["id"], "M1", "小明", "say", "（我去倒杯水）")
        out = trpgrender.render_html(trpglog.get(log["id"]), trpglog.lines(log["id"]))
        body = out.split("<main", 1)[1]
        self.assertEqual(body.count(".ra 聆听"), 1)                                # 指令不再单独一行，只在卡片标签里
        self.assertRegex(body, r'class="ln dice s1"[^>]*data-sp="阿红"[^>]*><div class="txt"><span class="cmd"><span class="who s1">阿红</span> \.ra 聆听')
        self.assertIn('class="ln say ooc', body)
        self.assertIn('id="ooc"', out)                                            # 有场外的话才出「隐藏场外」
        self.assertRegex(body, r'id="L\d+"')
        plain = trpgrender.render_html(trpglog.get(log["id"]), [l for l in trpglog.lines(log["id"]) if "倒杯水" not in l["text"]])
        self.assertNotIn('id="ooc"', plain)

    def test_token_needs_shared(self):
        log = self._log()
        self.assertIsNone(trpglog.by_token(log["token"]))                    # 没公开不能按 token 打开
        trpglog.mark_shared(log["id"])
        self.assertEqual(trpglog.by_token(log["token"])["id"], log["id"])
        self.assertIsNone(trpglog.by_token("../../etc"))
        self.assertEqual(trpglog.share_url(log), "")
        from changri.core.db import set_config
        set_config("phone_base_url", "http://x.test:5092/")
        self.assertEqual(trpglog.share_url(log), f"http://x.test:5092/l/{log['token']}")

    def test_phone_share_route(self):
        from changri.phone.app import create_phone_app
        from changri.core.db import set_config
        log = self._log()
        c = create_phone_app("k").test_client()
        set_config("phone_enabled", "1")
        self.assertEqual(c.get(f"/l/{log['token']}").status_code, 404)       # 没公开
        trpglog.mark_shared(log["id"])
        r = c.get(f"/l/{log['token']}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("我也来了", r.get_data(as_text=True))
        self.assertIn("script-src", r.headers["Content-Security-Policy"])
        self.assertNotIn("本场战报", r.get_data(as_text=True))                         # 骰点都对不上人（没有 ref）：表里只有「不明」，不出
        say = trpglog.add_line(log["id"], "M3", "<b>小绿</b>", "say", "/ra 侦查")
        trpglog.add_line(log["id"], "", "骰子", "dice", "侦查 检定：D100=2/50 —— 大成功。", say)
        page = c.get(f"/l/{log['token']}").get_data(as_text=True)
        self.assertIn("本场战报", page)
        self.assertIn("&lt;b&gt;小绿&lt;/b&gt;", page)                                   # 名字照样转义
        self.assertIn("大成功最多：&lt;b&gt;小绿&lt;/b&gt;", page)
        from changri.core import trpgexport
        self.assertIn("本场战报", trpgexport.build(trpglog.get(log["id"]), "html")[0].decode())   # 下载的 html 也有
        self.assertEqual(c.get("/l/" + "0" * 32).status_code, 404)
        set_config("phone_enabled", "0")
        self.assertEqual(c.get(f"/l/{log['token']}").status_code, 404)       # 小手机关了就打不开

    def test_admin_style_and_share(self):
        webusers.create_user("boss", "longenough1", "owner")
        log = self._log()
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        page = c.get(f"/trpglog/{log['id']}").get_data(as_text=True)
        self.assertIn("着色器", page)
        self.assertIn("srcdoc=", page)
        self.assertNotIn("<script>alert(1)", page)
        t = tok(f"/trpglog/{log['id']}")
        from changri.core import trpgrender
        red_default = trpgrender.color_map(trpglog.lines(log["id"]), trpgrender.parse_style("{}"))["阿红"][0]
        c.post(f"/trpglog/{log['id']}/style", data={"_csrf": t, "theme": "dark", "color_0": "#abcdef", "color_1": red_default, "dice_hl": "1"})
        st = trpgrender.parse_style(trpglog.get(log["id"])["palette"])
        self.assertEqual((st["theme"], st["colors"], st["dice_hl"], st["dim_bot"]), ("dark", {"小明": "#abcdef"}, True, False))
        c.post(f"/trpglog/{log['id']}/share", data={"_csrf": t, "on": "1"})
        self.assertEqual(trpglog.get(log["id"])["shared"], 1)
        c.post(f"/trpglog/{log['id']}/share", data={"_csrf": t})
        self.assertEqual(trpglog.get(log["id"])["shared"], 0)


class LogExportImport(unittest.TestCase):
    def setUp(self):
        clear()

    def _log(self):
        log, _ = trpglog.new_log("G1", "雾港", "M1")
        i1 = trpglog.add_line(log["id"], "M1", "小明", "say", "/ra 侦查 50")
        trpglog.add_line(log["id"], "", "骰子", "dice", "侦查 检定：D100=12/50 —— 成功。不错", i1)
        i2 = trpglog.add_line(log["id"], "M2", "阿红", "say", "/ra 力量 60")
        trpglog.add_line(log["id"], "", "骰子", "dice", "力量 检定：D100=99/60 —— 大失败。糟了", i2)
        trpglog.add_line(log["id"], "", "骰子", "dice", "力量 检定：D100=50/60 —— 困难失败。", i2)
        i3 = trpglog.add_line(log["id"], "M1", "小明", "say", "/sc 1/1d6")
        trpglog.add_line(log["id"], "", "骰子", "dice", "理智检定：D100=3/55 —— 成功\n损失 1", i3)
        i4 = trpglog.add_line(log["id"], "M2", "阿红", "say", "/ra 攻击")
        trpglog.add_line(log["id"], "", "骰子", "dice", "攻击 检定：D20=15+3=18 vs DC15 —— 通过", i4)
        return trpglog.get(log["id"])

    def test_verdict_prefers_after_dash(self):
        from changri.core.trpgrender import verdict
        self.assertEqual(verdict("成功率检定：D100=99/60 —— 大失败。")[:2], ("大失败", "fumble"))
        self.assertEqual(verdict("力量 检定：D100=50/60 —— 困难失败。")[:2], ("困难失败", "bad"))
        self.assertEqual(verdict("攻击 检定：D20=15 vs DC15 —— 未通过")[:2], ("未通过", "bad"))
        self.assertEqual(verdict("攻击 检定：D20=15 vs DC15 —— 通过")[:2], ("通过", "ok"))
        self.assertIsNone(verdict("掷了 3d6=10"))

    def test_summary_and_zip(self):
        import io
        import json as _json
        import zipfile
        from changri.core import trpgexport
        log = self._log()
        sm = trpgexport.summary(log, trpglog.lines(log["id"]))
        self.assertEqual(sm["people"]["小明"], {"n": 2, "ok": 2, "bad": 0, "crit": 0, "fumble": 0})
        self.assertEqual(sm["people"]["阿红"], {"n": 3, "ok": 1, "bad": 2, "crit": 0, "fumble": 1})
        self.assertEqual(sm["skills"]["力量"]["n"], 2)
        self.assertIn("理智", sm["skills"])
        data, fname, mime = trpgexport.build(log, "zip")
        self.assertEqual((fname, mime), ("雾港.zip", "application/zip"))
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(sorted(z.namelist()), sorted(["log.html", "log.txt", "log.json", "骰点汇总.txt"]))
        self.assertIn("小明", z.read("log.txt").decode())
        self.assertEqual(_json.loads(z.read("log.json"))["name"], "雾港")
        self.assertIn("成功率", z.read("骰点汇总.txt").decode())
        self.assertIn("<!doctype html>", z.read("log.html").decode())
        self.assertEqual(trpgexport.build(log, "txt")[2], "text/plain; charset=utf-8")

    def test_import_formats_dedup_and_merge(self):
        from changri.core import trpgimport
        log, _ = trpglog.new_log("G1", "雾港", "M1")
        base = int(__import__("time").mktime((2026, 11, 14, 21, 0, 0, 0, 0, -1)))
        trpglog.add_line(log["id"], "", "骰子", "dice", "侦查 检定：D100=12/50 —— 成功")
        with trpglog.get_conn() as c:
            c.execute("UPDATE trpg_log_lines SET ts=? WHERE log_id=?", (base + 30, log["id"]))
        text = ("消息记录\n=====\n"
                "2026-11-14 21:00:10 小明(123456789)\n我推开门\n用手电照一下\n\n"
                "2026-11-14 21:00:40 卡拉(999)\n侦查 检定：D100=12/50 —— 成功\n\n"
                "2026-11-14 21:01:00 阿红\n（场外话）不记\n\n"
                "阿红(2026/11/14 21:02:05)\n好的\n")
        entries = trpgimport.parse(text)
        self.assertEqual([(n, t) for _, n, t in entries], [("小明", "我推开门\n用手电照一下"), ("卡拉", "侦查 检定：D100=12/50 —— 成功"), ("阿红", "（场外话）不记"), ("阿红", "好的")])
        r = trpgimport.import_into(log["id"], entries, {"卡拉"})
        self.assertEqual((r["added"], r["skipped"], r["dup"]), (2, 2, 0))
        r2 = trpgimport.import_into(log["id"], entries, {"卡拉"})             # 再导一次：全是重复
        self.assertEqual((r2["added"], r2["dup"]), (0, 2))
        ls = trpglog.lines(log["id"])
        self.assertEqual([l["text"] for l in ls], ["我推开门\n用手电照一下", "侦查 检定：D100=12/50 —— 成功", "好的"])   # 按时间合并，骰点夹在中间
        self.assertEqual(trpgimport.decode("你好".encode("gb18030")), "你好")

    def test_admin_download_and_import_and_phone_zip(self):
        from changri.phone.app import create_phone_app
        from changri.core.db import set_config
        webusers.create_user("boss", "longenough1", "owner")
        log = self._log()
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        r = c.get(f"/trpglog/{log['id']}/download/zip")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertTrue(r.data.startswith(b"PK"))
        self.assertEqual(c.get(f"/trpglog/{log['id']}/download/exe").status_code, 404)
        t = tok(f"/trpglog/{log['id']}")
        r = c.post(f"/trpglog/{log['id']}/import", data={"_csrf": t, "text": "2026-11-14 22:00:00 路人\n来晚了", "skip": ""}, follow_redirects=True)
        self.assertIn("新增 1 条", r.get_data(as_text=True))
        self.assertIn("来晚了", [l["text"] for l in trpglog.lines(log["id"])])
        pc = create_phone_app("k").test_client()
        set_config("phone_enabled", "1")
        self.assertEqual(pc.get(f"/l/{log['token']}/zip").status_code, 404)    # 没公开
        trpglog.mark_shared(log["id"])
        self.assertTrue(pc.get(f"/l/{log['token']}/zip").data.startswith(b"PK"))
        self.assertIn("下载 zip", pc.get(f"/l/{log['token']}").get_data(as_text=True))


class LogMigration(unittest.TestCase):
    def test_old_table_gets_shared_column(self):
        import sqlite3
        from changri.core import db
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE trpg_logs (id INTEGER PRIMARY KEY, scope TEXT, name TEXT, status TEXT, token TEXT, started_by TEXT, started_ts INTEGER, ended_ts INTEGER, palette TEXT)")
        conn.commit()
        conn.close()
        db._migrate(path)
        conn = sqlite3.connect(path)
        self.assertIn("shared", [r[1] for r in conn.execute("PRAGMA table_info(trpg_logs)")])
        conn.close()


if __name__ == "__main__":
    unittest.main()
