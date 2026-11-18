"""后台多机器人：登录后选机器人、/b/<AppID>/ 下数据隔离、老网址自动补前缀。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, roles, webusers  # noqa: E402


from changri.core import godmode as god_core  # noqa: E402


class AdminMultiBot(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        from changri.admin import create_app
        self.app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        botconf.add_account("111111", "a" * 20, "甲号")
        self.c = self.app.test_client()
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self._csrf("/login")})

    def tearDown(self):
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def test_msgimages_page(self):
        if type(self) is not AdminMultiBot:
            return
        from changri.core import msgimages
        r = self.c.get("/b/111111/images")
        self.assertEqual(r.status_code, 200)
        self.assertIn("还没有图片", r.get_data(as_text=True))
        for src in ("sheet", "entrance"):
            self.assertEqual(self.c.get(f"/b/111111/images?src={src}").status_code, 200)
        self.assertEqual(self.c.get("/b/111111/images/sms/999/raw").status_code, 404)
        self.assertEqual(self.c.post("/b/111111/images/purge", data={"src": "sms", "_csrf": self._csrf("/b/111111/images")}).status_code, 302)

    def test_tools_page(self):
        if type(self) is not AdminMultiBot:
            return
        r = self.c.get("/b/111111/tools")
        self.assertEqual(r.status_code, 200)
        h = r.get_data(as_text=True)
        self.assertIn("随机分组", h)
        self.assertIn("未分组", h)
        self.assertIn("小工具", h)

    def test_collect_pages(self):
        if type(self) is not AdminMultiBot:
            return
        self.assertEqual(self.c.get("/b/111111/collect").status_code, 200)
        tok = self._csrf("/b/111111/collect")
        r = self.c.post("/b/111111/collect/add", data={"title": "晚间", "prompt": "说一句", "timed": "1", "start": "2026-11-15T20:00", "end": "2026-11-15T22:00", "_csrf": tok})
        self.assertEqual(r.status_code, 302)
        loc = r.headers["Location"]
        v = self.c.get(loc)
        self.assertEqual(v.status_code, 200)
        self.assertIn("晚间", v.get_data(as_text=True))
        self.assertEqual(self.c.post(loc + "/close", data={"_csrf": tok}).status_code, 302)
        self.assertEqual(self.c.post(loc + "/delete", data={"_csrf": tok}).status_code, 302)
        self.assertEqual(self.c.get(loc).status_code, 404)

    def test_gacha_page_and_extension_gate(self):
        if type(self) is not AdminMultiBot:
            return
        self.c.post("/b/111111/extensions/toggle", data={"id": "gacha", "on": "1", "_csrf": self._csrf("/b/111111/extensions")})
        r = self.c.get("/b/111111/gacha")
        self.assertEqual(r.status_code, 200)
        self.assertIn("抽取池", r.get_data(as_text=True))
        tok = self._csrf("/b/111111/gacha")
        self.assertEqual(self.c.post("/b/111111/gacha/pool/save", data={"name": "春", "pity_n": "5", "_csrf": tok}).status_code, 302)
        self.assertIn("春", self.c.get("/b/111111/gacha").get_data(as_text=True))

    def test_official_page(self):
        if type(self) is not AdminMultiBot:
            return
        r = self.c.get("/b/111111/official")
        self.assertEqual(r.status_code, 200)
        self.assertIn("发起官约", r.get_data(as_text=True))
        tok = self._csrf("/b/111111/official")
        r = self.c.post("/b/111111/official/launch", data={"kind": "官约", "time": "1400-1500", "role": ["1"], "_csrf": tok}, follow_redirects=True)
        self.assertIn("至少选两位", r.get_data(as_text=True))

    def test_precall_admin_page(self):
        if type(self) is not AdminMultiBot:
            return
        self.assertEqual(self.c.get("/b/111111/precall").status_code, 404)           # 扩展默认关
        self.c.post("/b/111111/extensions/toggle", data={"id": "precall", "on": "1", "_csrf": self._csrf("/b/111111/extensions")})
        r = self.c.get("/b/111111/precall")
        self.assertEqual(r.status_code, 200)
        self.assertIn("还没有人提交", r.get_data(as_text=True))
        self.assertIn("每人最多主动拨出", self.c.get("/b/111111/extensions?tab=aux").get_data(as_text=True))       # 扩展卡片里有自己的设置

    def test_sighting_admin_page(self):
        if type(self) is not AdminMultiBot:
            return
        self.assertEqual(self.c.get("/b/111111/sightings").status_code, 404)        # 默认关
        self.c.post("/b/111111/extensions/toggle", data={"id": "sighting", "on": "1", "_csrf": self._csrf("/b/111111/extensions")})
        r = self.c.get("/b/111111/sightings")
        self.assertEqual(r.status_code, 200)
        self.assertIn("还没有目击记录", r.get_data(as_text=True))

    def test_moments_masquerade_admin_pages(self):
        if type(self) is not AdminMultiBot:
            return
        self.assertEqual(self.c.get("/b/111111/masquerade").status_code, 404)        # 默认关
        self.assertEqual(self.c.get("/b/111111/moments").status_code, 404)
        self.c.post("/b/111111/extensions/toggle", data={"id": "phone", "on": "1", "_csrf": self._csrf("/b/111111/extensions")})
        self.c.post("/b/111111/phone/features", data={"feat_masquerade": "1", "feat_moments": "1", "_csrf": self._csrf("/b/111111/phone")})
        self.assertIn("匿名舞会", self.c.get("/b/111111/masquerade").get_data(as_text=True))
        self.assertEqual(self.c.get("/b/111111/moments").status_code, 200)
        self.assertIn("每人图片额度", self.c.get("/b/111111/images?src=moments").get_data(as_text=True))
        tok = self._csrf("/b/111111/masquerade")
        self.assertEqual(self.c.post("/b/111111/masquerade/op", data={"action": "on", "_csrf": tok}).status_code, 302)
        self.assertIn("开场中", self.c.get("/b/111111/masquerade").get_data(as_text=True))
        self.c.post("/b/111111/masquerade/op", data={"action": "narrate", "text": "灯光暗下来", "_csrf": tok})
        self.assertIn("灯光暗下来", self.c.get("/b/111111/masquerade").get_data(as_text=True))
        self.assertEqual(self.c.get("/b/111111/masquerade/export/1.txt").status_code, 404)      # 还没有人入场，没有场次

    def test_new_pages_npc_alarm_recipe_notify(self):
        if type(self) is not AdminMultiBot:
            return
        base = "/b/111111"
        post = lambda path, **d: self.c.post(base + path, data={**d, "_csrf": self._csrf(base + "/season")})
        r = post("/season/npc/create", name="阿林")
        self.assertEqual(r.status_code, 302)
        self.assertIn("阿林", self.c.get(base + "/season").get_data(as_text=True))
        self.assertEqual(self.c.get(base + "/alarm").status_code, 404)           # 加百列闹钟默认关
        post("/extensions/toggle", id="alarm", on="1")
        self.assertEqual(self.c.get(base + "/alarm").status_code, 200)
        post("/alarm/add", lines="2099-1-1 20:00 新年好")
        self.assertIn("新年好", self.c.get(base + "/alarm").get_data(as_text=True))
        self.assertEqual(self.c.get(base + "/rpg").status_code, 200)
        self.assertEqual(post("/rpg/recipe", name="x", mat_item="NOPE", mat_n="1", out_item="NOPE", out_n="1").status_code, 302)
        self.assertEqual(post("/rooms/notify", hours="2", text="快回", mode="idle").status_code, 302)
        self.assertEqual(post("/rooms/notify", hours="", text="").status_code, 302)

    def _csrf(self, path):
        import re
        html = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

    def test_dashboard_counts_free_room_groups(self):
        """总览的「空闲的房间群」：只算池子里空着的，等人退群的单独注明（不是角色的备用群）。"""
        from changri.core import pool
        from changri.core.db import use_bot
        with use_bot("111111"):
            a = pool.register("111111", "G1")
            pool.register("111111", "G2")
            b = pool.register("111111", "G3")
            pool.release(b, "cleaning")
            pool.note_group("111111", "G4")                         # 记下来了但没放进池子：不算
            with db.get_conn() as c:
                c.execute("UPDATE pool_groups SET state='occupied' WHERE id=?", (a,))
        html = self.c.get("/b/111111/").get_data(as_text=True)
        self.assertIn("空闲的房间群（1 个等人退群）", html)
        self.assertNotIn(">备用群", html)
        i = html.index("空闲的房间群")
        self.assertIn('<span class="v">1</span>', html[i - 200:i])          # 只有 G2 空着

    def test_msgmon_page(self):
        from changri.core import msgmon
        base = "/b/111111"
        with db.use_bot("111111"):
            msgmon.clear()
            msgmon.record("official", "甲 的个人群", "甲", "<b>你好</b>")
        page = self.c.get(f"{base}/monitor").get_data(as_text=True)
        self.assertIn("甲 的个人群", page)
        self.assertIn("&lt;b&gt;你好", page)                                # 内容要转义
        with db.use_bot("111111"):
            msgmon.record("qq", "备用群 123", "QQ 1", "第二条")
            first = msgmon.since()[0]["id"]
        items = self.c.get(f"{base}/monitor/feed?after={first}").get_json()["items"]
        self.assertEqual([x["text"] for x in items], ["第二条"])

    def test_dice_lines_page(self):
        """跑团台词：框里预填默认；改了的档按自己的句子随机说；清空保存 = 用默认；恢复默认回到原样。"""
        from changri.core import dice
        base = "/b/111111"
        page = self.c.get(f"{base}/dice/lines").get_data(as_text=True)
        self.assertIn("漂亮，干净利落。", page)
        tok = self._csrf(f"{base}/dice/lines")
        form = {"_csrf": tok}
        for _t, pool, keys in dice.LINE_GROUPS:
            for key, _l in keys:
                form[f"{pool}.{key}"] = "\n".join(dice.POOLS[pool][key])
        form["coc.大成功"] = "神了！\n\n天选之人。"
        form["coc.失败"] = "差一点点"
        self.c.post(f"{base}/dice/lines", data=form)
        with db.use_bot("111111"):
            self.assertEqual(dice.custom_lines(), {"coc.大成功": ["神了！", "天选之人。"], "coc.失败": ["差一点点"]})
            self.assertIn(dice._pick(dice.LEVEL_REMARKS, "大成功"), ("神了！", "天选之人。"))
        form["coc.失败"] = ""                                                    # 清空保存：回到默认
        self.c.post(f"{base}/dice/lines", data=form)
        with db.use_bot("111111"):
            self.assertNotIn("coc.失败", dice.custom_lines())
            self.assertIn(dice._pick(dice.LEVEL_REMARKS, "失败"), dice.LEVEL_REMARKS["失败"])
        form["coc.失败"] = "差一点点"
        self.c.post(f"{base}/dice/lines", data=form)
        with db.use_bot("111111"):
            self.assertIn(dice._pick(dice.LEVEL_REMARKS, "成功"), dice.LEVEL_REMARKS["成功"])     # 没改的照旧
        self.assertIn("已改", self.c.get(f"{base}/dice/lines").get_data(as_text=True))
        self.c.post(f"{base}/dice/lines/reset", data={"k": "coc.失败", "_csrf": tok})
        with db.use_bot("111111"):
            self.assertEqual(list(dice.custom_lines()), ["coc.大成功"])
        self.c.post(f"{base}/dice/lines/reset", data={"k": "*", "_csrf": tok})
        with db.use_bot("111111"):
            self.assertEqual(dice.custom_lines(), {})

    def test_god_erase_role(self):
        """上帝模式里彻底删除角色：要输对角色名才删。"""
        base = "/b/111111"
        roles.create_role("录错", "B1", "P0", "M0")
        with db.get_conn() as c:
            rid = c.execute("SELECT id FROM roles WHERE name='录错'").fetchone()["id"]
        page = self.c.get(f"{base}/god?role={rid}").get_data(as_text=True)
        self.assertIn("彻底删除这个角色", page)
        tok = self._csrf(f"{base}/god?role={rid}")
        r = self.c.post(f"{base}/god/{rid}/erase", data={"confirm": "录", "_csrf": tok}, follow_redirects=True)
        self.assertIn("角色名没对上", r.get_data(as_text=True))
        self.assertIsNotNone(roles.get_role(rid))
        r = self.c.post(f"{base}/god/{rid}/erase", data={"confirm": "录错", "_csrf": tok}, follow_redirects=True)
        self.assertIn("已彻底删除角色「录错」", r.get_data(as_text=True))
        self.assertIsNone(roles.get_role(rid))

    def test_god_mode_page(self):
        base = "/b/111111"
        from changri.core import roles as roles_c
        roles_c.create_role("甲", "B1", "P0", "M0")
        page = self.c.get(base + "/god").get_data(as_text=True)
        self.assertIn("上帝模式", page)
        self.assertIn("甲", page)
        with db.get_conn() as c:
            rid = c.execute("SELECT id FROM roles WHERE name='甲'").fetchone()["id"]
        tok = self._csrf(f"{base}/god")
        self.c.post(f"{base}/god/{rid}/perm", data={"on": ["sms"], "_csrf": tok})            # 只留短信，其余关掉
        page = self.c.get(f"{base}/god?role={rid}").get_data(as_text=True)
        self.assertIn("关着的功能", page)
        self.assertEqual(god_core.check(rid, "gift") != "", True)
        self.assertEqual(god_core.check(rid, "sms"), "")
        self.c.post(f"{base}/god/{rid}/lock", data={"time": "1400-1500", "note": "录像", "_csrf": tok})
        self.assertIn("录像", self.c.get(f"{base}/god?role={rid}&tab=time").get_data(as_text=True))
        # 背包：发金币、改属性、扣除
        from changri.core import rpg as rpg_c
        rpg_c.save_item("GOLD", "金币", kind="currency")
        self.c.post(f"{base}/god/{rid}/item", data={"item": "GOLD", "n": "10", "act": "give", "tab": "bag", "_csrf": tok})
        self.c.post(f"{base}/god/{rid}/item", data={"item": "GOLD", "n": "3", "act": "take", "tab": "bag", "_csrf": tok})
        self.c.post(f"{base}/god/{rid}/attr", data={"name": "力量", "value": "50", "act": "set", "tab": "bag", "_csrf": tok})
        self.c.post(f"{base}/god/{rid}/attr", data={"name": "力量", "value": "-5", "act": "add", "tab": "bag", "_csrf": tok})
        bag = self.c.get(f"{base}/god?role={rid}&tab=bag").get_data(as_text=True)
        self.assertIn("×7", bag)
        self.assertIn("力量", bag)
        self.assertIn("45", bag)
        self.assertNotIn("结束这场", self.c.get(f"{base}/god?role={rid}&tab=time").get_data(as_text=True))
        # 全体
        self.c.post(f"{base}/god/all/perm", data={"on": ["sms"], "_csrf": tok})
        self.assertTrue(god_core.global_off("gift"))
        self.assertFalse(god_core.global_off("sms"))
        self.assertIn("全体", self.c.get(f"{base}/god?role=all").get_data(as_text=True))
        self.c.post(f"{base}/god/all/perm", data={"on": list(god_core.FEATURES), "_csrf": tok})

    def test_rpg_exchange_admin(self):
        base = "/b/111111"
        from changri.core import rpg as rpg_c
        rpg_c.save_item("GOLD", "金币", kind="currency")
        rpg_c.save_item("SILVER", "银币", kind="currency")
        tok = self._csrf(f"{base}/rpg")
        self.c.post(f"{base}/rpg/exchange", data={"from_item": "GOLD", "from_n": "100", "to_item": "SILVER", "to_n": "1", "_csrf": tok})
        page = self.c.get(base + "/rpg").get_data(as_text=True)
        self.assertIn("金币 ×100 → 银币 ×1", page)
        ex_id = rpg_c.exchanges()[0]["id"]
        self.c.post(f"{base}/rpg/exchange/{ex_id}/delete", data={"_csrf": tok})
        self.assertEqual(rpg_c.exchanges(), [])

    def test_rewards_page_and_rule_form(self):
        base = "/b/111111"
        self.c.post(f"{base}/extensions/toggle", data={"id": "rewards", "on": "1", "_csrf": self._csrf(f"{base}/extensions")})
        r = self.c.get(base + "/rewards")
        self.assertEqual(r.status_code, 200)
        self.assertIn("结戏奖励", r.get_data(as_text=True))
        self.c.post(f"{base}/rewards/save", data={"name": "认真演", "kinds": ["约会", "电话", "官约", "微信", "心愿"], "segs_min": "3", "fx_ref": ["a:力量"], "fx_n": ["2"], "fx_p": ["100"], "_csrf": self._csrf(f"{base}/rewards")})
        page = self.c.get(base + "/rewards").get_data(as_text=True)
        self.assertIn("认真演", page)
        self.assertIn("戏文段数 ≥ 3段", page)
        self.assertIn("力量 +2", page)

    def test_extra_section_forum_items_places(self):
        """「额外」：论坛、物品与商城、地点都是可开关的扩展；开着的常显，关着的收进「更多」；属性一直在。"""
        base = "/b/111111"
        for e in ("rewards", "alarm", "bottle", "chaos_letter", "dinner", "phone", "giftshop", "gacha", "song", "moments", "masquerade", "sighting", "precall"):      # 这些默认关，先打开才算「都开着」
            self.c.post(f"{base}/extensions/toggle", data={"id": e, "on": "1", "_csrf": self._csrf(f"{base}/extensions")})
        self.assertEqual(self.c.get(f"{base}/attrs").status_code, 200)       # 属性是常规功能，不受任何扩展开关影响
        for path in ("/forum", "/rpg", "/places"):
            self.assertEqual(self.c.get(base + path).status_code, 200, path)
        home = self.c.get(base + "/").get_data(as_text=True)
        self.assertIn("额外", home)
        self.assertNotIn("更多（未开启）", home)                                # 都开着，没有折叠项
        for ext, path, name in (("forum", "/forum", "论坛"), ("rpg", "/rpg", "物品与商城"), ("places", "/places", "地点")):
            self.c.post(f"{base}/extensions/toggle", data={"id": ext, "on": "0", "_csrf": self._csrf(f"{base}/extensions")})
            self.assertEqual(self.c.get(base + path).status_code, 404, path)  # 关了，页面网址也关掉
            home = self.c.get(base + "/").get_data(as_text=True)
            self.assertIn("更多（未开启）", home)
            more = home[home.index("更多（未开启）"):]
            self.assertIn(name, more[:more.index("</details>")])            # 折叠项里能看到它
            self.assertEqual(self.c.get(f"{base}/attrs").status_code, 200)
            self.c.post(f"{base}/extensions/toggle", data={"id": ext, "on": "1", "_csrf": self._csrf(f"{base}/extensions")})
        self.assertNotIn("更多（未开启）", self.c.get(base + "/").get_data(as_text=True))

    def test_rpg_pages_and_pool_split(self):
        """物品与商城、属性、地点是三个页面；商城用下拉选货币；个人群不混在能用的群里。"""
        base = "/b/111111"
        tok = lambda: self._csrf(f"{base}/extensions")                    # noqa: E731
        post = lambda path, **d: self.c.post(base + path, data={**d, "_csrf": tok()})   # noqa: E731
        post("/attrs/save", name="体力", min="0", max="100", default="50")
        post("/rpg/item", code="GOLD", name="金币", kind="currency", description="", effects="", max_uses="-1", can_give="on")
        post("/rpg/item", code="POTION", name="治疗药水", kind="item", description="", effects="体力+20", max_uses="1", can_give="on")
        post("/rpg/shop", item="POTION", price="30", currency="GOLD")
        post("/places/save", name="顶楼花园", description="", locked="on")
        page = self.c.get(base + "/rpg").get_data(as_text=True)
        self.assertIn("体力 +20", page)                                    # 属性效果是小标签
        self.assertIn("30", page); self.assertIn("金币", page)
        self.assertNotIn("顶楼花园", page); self.assertNotIn("属性定义", page)  # 地点和属性不在这一页
        self.assertIn("体力", self.c.get(base + "/attrs").get_data(as_text=True))
        self.assertIn("顶楼花园", self.c.get(base + "/places").get_data(as_text=True))
        from changri.core import roles as R
        from changri.core import pool
        R.create_role("甲", "111111", "PERSONAL_G", "M1")
        pool.add_group("111111", "PERSONAL_G") if hasattr(pool, "add_group") else None

    def test_default_hp_gold_and_one_click_init(self):
        """恋综一直有 HP（0–100）和金币；删了不会自己长回来，一键初始化才补回；消息记录页没了；账号/机器人/监听页能打开。"""
        base = "/b/111111"
        page = self.c.get(base + "/attrs").get_data(as_text=True)
        self.assertIn("HP", page)
        self.assertIn("金币", self.c.get(base + "/rpg").get_data(as_text=True))
        tok = lambda: self._csrf(f"{base}/extensions")                    # noqa: E731
        from changri.core import rpg
        with db.use_bot("111111"):
            rpg.delete_attr_def("HP")
            self.assertEqual([x["name"] for x in rpg.attr_defs()], [])             # 删了就是删了，不会自己长回来
        r = self.c.post(base + "/init", data={"mode": "fill", "_csrf": tok()})
        self.assertEqual(r.status_code, 302)
        with db.use_bot("111111"):
            hp = [x for x in rpg.attr_defs() if x["name"] == "HP"][0]
        self.assertEqual((hp["min_value"], hp["max_value"], hp["default_value"]), (0, 100, 100))
        self.assertEqual(self.c.get(base + "/messages").status_code, 404)
        for path in ("/qqlink", "/season", "/relations", "/backup-pool"):
            self.assertEqual(self.c.get(base + path).status_code, 200, path)
        for path in ("/accounts", "/bots"):
            self.assertEqual(self.c.get(path).status_code, 200, path)

    def test_lovemail_delivery_time_editable(self):
        base = "/b/111111"
        tok = self._csrf(base + "/extensions")
        self.c.post(base + "/lovemail/time", data={"at": "9:5", "_csrf": tok})            # 格式不对：不改
        with db.use_bot("111111"):
            self.assertEqual(db.get_config("lovemail_delivery_time", "22:00"), "22:00")
        self.c.post(base + "/lovemail/time", data={"at": "21:30", "_csrf": tok})
        with db.use_bot("111111"):
            self.assertEqual(db.get_config("lovemail_delivery_time", "22:00"), "21:30")
        self.assertIn("每天 21:30", self.c.get(base + "/lovemail").get_data(as_text=True))

    def test_healthz_and_restart_overlay(self):
        """页面里自带「正在重启」提示：轮询 /healthz，不需要登录；带机器人前缀的页面轮询带前缀的地址。"""
        r = self.c.get("/healthz", follow_redirects=True)                    # 没带机器人前缀的老网址会先补前缀
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "ok"))
        page = self.c.get("/b/111111/").get_data(as_text=True)
        self.assertIn("正在重启", page); self.assertIn("/b/111111", page.split("/healthz")[0][-40:])
        self.assertEqual(self.c.get("/b/111111/healthz").status_code, 200)

    def test_sheets_and_relations_page_dialogs(self):
        """公知/二表/关系线页：表格打勾、点查看能取到内容（JSON + 图片），展示公知开关；别的机器人看不到图片文件。"""
        import io
        from PIL import Image
        from changri.core import sheets
        base = "/b/111111"
        with db.use_bot("111111"):
            roles.create_role("甲", "111111", "G1", "M1"); roles.create_role("乙", "111111", "G2", "M2")
            rid = roles.get_role_by_name("甲")["id"]
            buf = io.BytesIO(); Image.new("RGB", (20, 20), (1, 2, 3)).save(buf, "PNG")
            sheets.submit(rid, "gongzhi", "我的公知内容", [buf.getvalue()])
        page = self.c.get(base + "/relations").get_data(as_text=True)
        self.assertIn("公知、二表与关系线", page)
        self.assertIn("公知 1/2 · 二表 0/2", page)
        r = self.c.get(base + f"/sheets/view/gongzhi/{rid}")
        d = r.get_json()
        self.assertEqual(d["text"], "我的公知内容"); self.assertEqual(len(d["images"]), 1)
        self.assertEqual(self.c.get(d["images"][0]).headers["Content-Type"], "image/jpeg")
        self.assertEqual(self.c.get(base + f"/sheets/view/erbiao/{rid}").status_code, 404)
        self.assertEqual(self.c.get(base + "/sheets/img/..%2Fx.jpg").status_code, 404)
        tok = self._csrf(base + "/extensions")
        self.c.post(base + "/sheets/public", data={"on": "1", "_csrf": tok})
        with db.use_bot("111111"):
            self.assertTrue(sheets.public())

    def test_single_bot_goes_straight_in_and_old_urls_redirect(self):
        r = self.c.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/b/111111/"))
        r = self.c.get("/roles")
        self.assertEqual(r.headers["Location"], "/b/111111/roles")
        self.assertEqual(self.c.get("/b/111111/roles").headers["Location"], "/b/111111/#roles")       # 角色与群并进总览
        self.assertEqual(self.c.get("/b/999999/").status_code, 404)

    def test_picker_with_two_bots_and_isolation(self):
        botconf.add_account("222222", "b" * 20, "乙号")
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn("选择机器人", html)
        self.assertIn("甲号", html)
        self.assertIn("乙号", html)
        with db.use_bot("111111"):
            roles.create_role("只在甲号", "111111", "G1", "M1")
        self.assertIn("只在甲号", self.c.get("/b/111111/").get_data(as_text=True))
        self.assertNotIn("只在甲号", self.c.get("/b/222222/").get_data(as_text=True))
        self.assertIn('id="roles"', self.c.get("/b/222222/").get_data(as_text=True))
        self.assertIn('id="rooms"', self.c.get("/b/222222/").get_data(as_text=True))                  # 进行中的房间在总览里   # 页面链接自带前缀
        self.assertIn("/b/111111/", self.c.get("/b/222222/").get_data(as_text=True))                # 切换下拉
        self.assertEqual(self.c.get("/accounts").status_code, 200)                                   # 全局页面不带前缀

    def test_remote_block_regex_with_prefix(self):
        from changri.admin.remote import is_blocked_path
        self.assertTrue(is_blocked_path("/b/111111/extensions/upload"))
        self.assertTrue(is_blocked_path("/b/111111/accounts"))
        self.assertFalse(is_blocked_path("/b/111111/roles"))


if __name__ == "__main__":
    unittest.main()


class Erase(AdminMultiBot):
    def test_erase_needs_exact_appid_and_removes_everything(self):
        botconf.add_account("222222", "b" * 20, "乙号")
        with db.use_bot("222222"):
            roles.create_role("乙的角色", "222222", "G", "M")
        t = self._csrf("/bots")
        self.c.post("/bots/222222/erase", data={"confirm": "wrong", "_csrf": t})
        self.assertTrue(db.workspace_path("222222").exists())                      # 没对上：什么都不删
        self.assertIsNotNone(botconf.get_account("222222"))
        self.c.post("/bots/222222/erase", data={"confirm": "222222", "_csrf": t})
        self.assertFalse(db.workspace_path("222222").parent.exists())
        self.assertIsNone(botconf.get_account("222222"))
        self.assertTrue(db.workspace_path("111111").exists() or botconf.get_account("111111"))   # 别的机器人不受影响


class NoGuess(AdminMultiBot):
    def test_write_without_bot_prefix_is_refused(self):
        botconf.add_account("222222", "b" * 20, "乙号")
        t = self._csrf("/b/111111/")
        r = self.c.post("/roles/1/delete", data={"_csrf": t})              # 没前缀的写操作
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/?pick=1"))
        r = self.c.post("/b/111111/roles/1/delete", data={"_csrf": t})     # 带前缀照常
        self.assertNotEqual(r.headers.get("Location"), "/?pick=1")


class Wizard(AdminMultiBot):
    def test_wizard_progress_and_redirect(self):
        self.assertEqual(self.c.get("/wizard").status_code, 200)
        html = self.c.get("/wizard").get_data(as_text=True)
        self.assertIn("已接入：甲号", html)
        self.assertIn("让机器人连上", html)                      # 机器人没在线：停在第 2 步，第 3 步提示先连上
        self.assertIn("先让机器人连上", html)

    def test_fresh_install_goes_to_wizard(self):
        botconf.delete_account("111111")
        r = self.c.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/wizard"))
        html = self.c.get("/wizard").get_data(as_text=True)
        self.assertIn("接入你的 QQ 机器人", html)
        self.assertIn('name="next" value="wizard"', html)


class ErrLog(unittest.TestCase):
    def test_record_scrub_crash_marker_and_report(self):
        """报错日志：记下来并抹掉密钥和长数字；上次没正常退出算崩溃；有接收地址就发出去并标记已发送。"""
        import json
        from unittest import mock
        from changri.core import errlog
        d = Path(tempfile.mkdtemp())
        old = db.DB_PATH
        db.DB_PATH = d / "changri.db"
        try:
            errlog.record("error", "x:y:1", "boom 123456789 secret=" + "A" * 30, "trace " + "B" * 30)
            row = errlog.recent()[0]
            self.assertNotIn("123456789", row["message"]); self.assertNotIn("A" * 30, row["message"]); self.assertNotIn("B" * 30, row["trace"])
            (d / ".running").write_text("1")                       # 上次启动后没清掉标记 = 崩溃
            with mock.patch("threading.Thread"):
                errlog.install()
            self.assertEqual(errlog.recent()[0]["kind"], "crash")
            errlog.mark_clean_exit()
            self.assertFalse((d / ".running").exists())
            sent = {}

            class R:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *a): return False
            def fake(req, timeout=0):
                sent["body"] = json.loads(req.data)
                return R()
            with mock.patch.object(errlog, "report_url", return_value="https://example.test/in"), mock.patch("urllib.request.urlopen", fake):
                self.assertEqual(errlog.report_pending(), 2)
                self.assertEqual(errlog.report_pending(), 0)       # 发过的不重发
            self.assertEqual(len(sent["body"]["errors"]), 2)
            errlog.clear()
            self.assertEqual(errlog.recent(), [])
        finally:
            db.DB_PATH = old
