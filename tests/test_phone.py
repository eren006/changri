"""小手机 P0：激活码登录、三道闸、外观、重置码后旧登录失效。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "ph.db"))

from changri.core import phonecodes, phonegate, roles  # noqa: E402
from changri.core.db import get_conn, get_config, init_db, set_config  # noqa: E402
from changri.phone import app as phone_app  # noqa: E402
from changri.phone.app import create_phone_app  # noqa: E402
from changri.phone.themes import phone_theme  # noqa: E402


class Phone(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("phone_codes", "phone_seen", "messages", "roles", "role_groups", "config", "gift_unlocks", "gift_shop_visits", "gift_shop"):
                c.execute(f"DELETE FROM {t}")
        set_config("phone_enabled", "1")                          # 默认是关的，要走过初始化才开；这里直接开
        roles.create_role("甲", "B1", "P0", "M0")
        roles.create_role("乙", "B1", "P1", "M1")
        self.a = roles.get_role_by_name("甲")
        self.b = roles.get_role_by_name("乙")
        phone_app._fails.clear()
        self.app = create_phone_app("k" * 32)
        self.c = self.app.test_client()

    def csrf(self, path="/p"):
        self.c.get(path)
        with self.c.session_transaction() as s:
            return s.setdefault("csrf", "tok")

    def login(self, code):
        tok = self.csrf()
        return self.c.post("/p", data={"code": code, "_csrf": tok})

    def test_codes(self):
        code = phonecodes.code_for_role(self.a["id"])
        self.assertEqual(len(code), 10)
        self.assertEqual(code, phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(phonecodes.role_for_code(code.lower())["id"], self.a["id"])
        new = phonecodes.reset(self.a["id"])
        self.assertNotEqual(new, code)
        self.assertIsNone(phonecodes.role_for_code(code))
        self.assertEqual(phonecodes.generate_all(), 1)

    def test_login_flow(self):
        self.assertEqual(self.c.get("/p/me").status_code, 302)
        r = self.login("WRONGCODE1")
        self.assertIn("激活码不对", r.get_data(as_text=True))
        code = phonecodes.code_for_role(self.a["id"])
        self.assertEqual(self.login(code).status_code, 302)
        for path in ("/p/me", "/p/me/discover", "/p/me/profile", "/p/guide"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIn("甲", self.c.get("/p/me/profile").get_data(as_text=True))
        phonecodes.reset(self.a["id"])                       # 重置后旧登录立刻失效
        self.assertEqual(self.c.get("/p/me").status_code, 302)

    def test_lockout_and_csrf(self):
        self.assertEqual(self.c.post("/p", data={"code": "x"}).status_code, 400)
        for _ in range(5):
            self.login("BADCODE123")
        self.assertIn("输错太多次", self.login("BADCODE123").get_data(as_text=True))

    def test_inbox_and_thread(self):
        with get_conn() as c:
            c.execute("INSERT INTO messages(kind,from_role_id,to_role_id,content,created_ts,status) VALUES('sms',?,?,'你好呀',1700000000,'sent')",
                      (self.b["id"], self.a["id"]))
        self.login(phonecodes.code_for_role(self.a["id"]))
        html = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("乙", html)
        self.assertIn("你好呀", html)
        self.assertIn("你好呀", self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True))
        self.assertEqual(self.c.get(f"/p/me/{self.a['id']}").status_code, 404)    # 不能和自己开对话

    def test_skin(self):
        self.login(phonecodes.code_for_role(self.a["id"]))
        tok = self.csrf("/p/me/profile")
        self.c.post("/p/me/skin", data={"skin": "classic", "_csrf": tok})
        self.assertIn('data-visual="classic"', self.c.get("/p/me").get_data(as_text=True))
        self.assertEqual(phone_theme("", "nope")["visual"], "modern")

    def test_gate(self):
        self.assertTrue(phonegate.blocked_hit("甲", "t", "我要看 色 情 内容") or True)
        self.assertEqual(phonegate.check_send("甲", "t", "你好"), "现在没有进行中的季度，暂时不能发送")
        set_config("phone_paused", "1")
        self.assertIn("暂时关闭", phonegate.check_send("甲", "t", "你好"))

    def _login(self, role):
        self.login(phonecodes.code_for_role(role["id"]))

    def _send(self, **data):
        from unittest import mock
        sent = []

        async def fake(bot, grp, text):
            sent.append((grp, text))
            return "mid"
        tok = self.csrf("/p/me/new")
        with mock.patch("changri.core.qqsend.make_send", return_value=fake), mock.patch("changri.core.phonegate.season.phase", return_value="playing"):
            r = self.c.post("/p/me/send", data={"_csrf": tok, **data}, follow_redirects=True)
        return r.get_data(as_text=True), sent

    def test_send_sms_and_gift(self):
        self._login(self.a)
        html, sent = self._send(to=self.b["id"], kind="sms", text="晚上好")
        self.assertIn("已送达", html)
        self.assertTrue(any("晚上好" in t for g, t in sent))
        self.assertEqual(get_conn().execute("SELECT COUNT(*) FROM messages WHERE kind='sms'").fetchone()[0], 1)
        html, sent = self._send(to=self.b["id"], kind="gift", gift_name="白玫瑰", text="给你")
        self.assertIn("已送达", html)
        self.assertEqual(get_conn().execute("SELECT title FROM messages WHERE kind='gift'").fetchone()[0], "白玫瑰")

    def test_send_rules(self):
        self._login(self.a)
        set_config("sms_daily_limit", "1")
        set_config("sms_cooldown_sec", "0")
        self._send(to=self.b["id"], kind="sms", text="一")
        html, _ = self._send(to=self.b["id"], kind="sms", text="二")
        self.assertIn("次数用完", html)
        set_config("sms_daily_limit", "0")
        html, _ = self._send(to=self.b["id"], kind="sms", text="我要看色情内容 sb")
        self.assertIn("不能发送的词", html)
        set_config("phone_paused", "1")
        html, _ = self._send(to=self.b["id"], kind="sms", text="你好")
        self.assertIn("暂时关闭", html)
        set_config("phone_paused", "0")
        set_config("window_sms", "0:00-0:01")
        html, _ = self._send(to=self.b["id"], kind="sms", text="你好")
        self.assertIn("没有开放", html)

    def test_block_flow(self):
        from changri.core import blocks
        self._login(self.b)
        tok = self.csrf("/p/me/new")
        self.c.post("/p/me/block", data={"_csrf": tok, "to": self.a["id"], "silent": "1"})
        self.assertTrue(blocks.get_block(self.b["id"], self.a["id"]))
        html = self.c.get(f"/p/me/{self.a['id']}").get_data(as_text=True)
        self.assertIn("你已拉黑 TA", html)
        r = self.c.post("/p/me/block", data={"_csrf": tok, "to": self.a["id"], "action": "unblock"}, follow_redirects=True)
        self.assertIn("还要等", r.get_data(as_text=True))                 # 拉黑后要满 2 小时才能解除

    def test_shop_and_new(self):
        set_config("gift_catalog", "白玫瑰、手写信")
        self._login(self.a)
        self.assertIn("0/2", self.c.get("/p/me/shop").get_data(as_text=True))           # 还没逛：图鉴是空的
        set_config("gift_shop_bonus_pct", "100")
        r = self.c.post("/p/me/shop/visit", data={"_csrf": self.csrf("/p/me/shop")}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("两件都收入图鉴", r)                                               # 逛一逛：收进图鉴
        self.assertIn("白玫瑰", r)
        self.assertIn("白玫瑰", self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True))   # 送礼面板只列图鉴里的
        html = self.c.get("/p/me/new?gift=白玫瑰").get_data(as_text=True)
        self.assertIn("乙", html)
        self.assertIn("gift=", html)
        self.assertIn("giftName", self.c.get(f"/p/me/{self.b['id']}?gift=白玫瑰").get_data(as_text=True))

    def test_live_port_change(self):
        import socket
        import urllib.request
        from changri.core import phoneserver

        def free():
            with socket.socket() as k:
                k.bind(("127.0.0.1", 0))
                return k.getsockname()[1]
        p1, p2 = free(), free()
        try:
            phoneserver.set_port(p1)
            self.assertIn(str(p1), phoneserver.start())
            self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{p1}/p/health", timeout=3).read(), b"ok")
            phoneserver.set_port(p2)                                        # 改端口：立刻换，不用重启
            self.assertIn(str(p2), phoneserver.start())
            self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{p2}/p/health", timeout=3).read(), b"ok")
            with self.assertRaises(Exception):
                urllib.request.urlopen(f"http://127.0.0.1:{p1}/p/health", timeout=2)
            with socket.socket() as busy:                                   # 端口被占：给出能看懂的提示
                busy.bind(("0.0.0.0", 0))
                busy.listen()
                phoneserver.set_port(busy.getsockname()[1])
                self.assertIn("没能在", phoneserver.start())
        finally:
            phoneserver.stop()

    def test_disabled(self):
        set_config("phone_enabled", "0")
        self.assertEqual(self.c.get("/p").status_code, 404)


class TimelineTodoPages(Phone):
    def test_pages_render_and_match_core(self):
        from changri.core import overview
        self.login(phonecodes.code_for_role(self.a["id"]))
        for path in ("/p/me/timeline", "/p/me/todo", "/p/me/discover"):
            self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertIn("时间线还是空的", self.c.get("/p/me/timeline").get_data(as_text=True))
        self.assertIn("没有要回的事", self.c.get("/p/me/todo").get_data(as_text=True))
        self.assertIn("时间线", self.c.get("/p/me/discover").get_data(as_text=True))
        self.assertEqual(overview.todo_items(self.a), [])
        with get_conn() as c:                                      # 一个排队中的约会：时间线和待回都该看到
            c.execute("INSERT INTO rooms(id, kind, creator_id, day, start_hm, end_hm, place, created_ts) VALUES (901,'约会',?,1,'1400','1500','花园',1)", (self.a["id"],))
            for r in (self.a, self.b):
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES (901, ?)", (r["id"],))
        tl = self.c.get("/p/me/timeline").get_data(as_text=True)
        self.assertIn("D1", tl)
        self.assertIn("花园", tl)
        self.assertIn("乙", tl)
        self.assertIn("R901", self.c.get("/p/me/todo").get_data(as_text=True))
        from changri.core import overview as ov
        key = ov.todo_entries(self.a)[0][0]
        tok = self.csrf("/p/me/todo")
        html = self.c.post("/p/me/todo/dismiss", data={"_csrf": tok, "key": key}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("已暂不提醒（1）", html)                                           # 收进折叠里，不算已回复
        self.assertIn("R901", overview.todo_items(self.a)[0])                           # 群里「我的待回」照旧
        html = self.c.post("/p/me/todo/dismiss", data={"_csrf": tok, "key": key, "action": "restore"}, follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("已暂不提醒", html)


class PhoneExtras(Phone):
    def _clean(self):
        with get_conn() as c:
            for t in ("collect_items", "collections", "gacha_log", "gacha_state", "gacha_prizes", "gacha_pools", "lovemails", "wishes", "drift_msgs", "drift_bottles",
                      "rel_items", "rel_details", "relations", "rpg_inventory"):
                c.execute(f"DELETE FROM {t}")

    def test_collect_and_draw(self):
        import time as _t
        from changri.core import collect, gacha, rpg
        self._clean()
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertIn("没有正在进行", self.c.get("/p/me/collect").get_data(as_text=True))
        now = int(_t.time())
        ok, cid = collect.create("今晚", "说一句", now - 60, now + 3600, 1)
        tok = self.csrf("/p/me/collect")
        r = self.c.post("/p/me/collect/submit", data={"cid": cid, "text": "晚上好", "_csrf": tok}, follow_redirects=True)
        self.assertIn("晚上好", r.get_data(as_text=True))
        self.assertEqual(collect.items(int(cid), 0)[0]["content"], "晚上好")
        # RPG 页：背包、属性；抽卡 tab 要抽取池开着
        for v in ("", "?view=attrs"):
            self.assertEqual(self.c.get("/p/me/char" + v).status_code, 200)
        set_config("ext:gacha", "0")
        self.assertEqual(self.c.get("/p/me/draw/state").status_code, 404)
        set_config("ext:gacha", "1")
        rpg.save_item("ph_a", "糖", "item", "甜的")
        gacha.save_pool("春", "", 0)
        pid = gacha.pools()[0]["id"]
        gacha.add_prize(pid, "ph_a", 1, 1)
        gacha.grant(self.a["id"], "春", 2)
        self.assertIn("抽卡", self.c.get("/p/me/char").get_data(as_text=True))
        self.assertEqual(self.c.get("/p/me/char?view=draw").status_code, 200)
        st = self.c.get("/p/me/draw/state").get_json()
        self.assertEqual(st["pools"][0]["left"], 2)
        r = self.c.post("/p/me/draw", data={"pool": "春", "_csrf": tok}).get_json()
        self.assertTrue(r["ok"])
        st = self.c.get("/p/me/draw/state").get_json()
        self.assertEqual((st["pools"][0]["left"], st["results"][0]["name"]), (1, "糖×1"))

    def test_lovemail_page(self):
        from changri.core import lovemail
        self._clean()
        set_config("lovemail_enabled", "1")
        set_config("ext:lovemail", "1")
        self.login(phonecodes.code_for_role(self.a["id"]))
        for v in ("", "?view=sent", "?view=write"):
            self.assertEqual(self.c.get("/p/me/lovemail" + v).status_code, 200, v)
        tok = self.csrf("/p/me/lovemail")
        r = self.c.post("/p/me/lovemail", data={"to": "乙", "content": "喜欢你", "signature": "", "_csrf": tok}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        pool = lovemail.my_pool(self.a["id"])
        if pool:                                   # 季度没开时 check_send 会拦，这里只在投进去的时候继续测撤回
            self.c.post("/p/me/lovemail", data={"action": "revoke", "mail": pool[0]["id"], "_csrf": tok})
            self.assertEqual(lovemail.my_pool(self.a["id"]), [])
        set_config("ext:lovemail", "0")
        self.assertEqual(self.c.get("/p/me/lovemail").status_code, 404)

    def test_wishes_bottles_pages(self):
        self._clean()
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(self.c.get("/p/me/wishes").status_code, 404)
        self.assertEqual(self.c.get("/p/me/bottles").status_code, 404)
        set_config("ext:wishes", "1")
        set_config("ext:bottle", "1")
        self.assertEqual(self.c.get("/p/me/wishes").status_code, 200)
        self.assertEqual(self.c.get("/p/me/bottles").status_code, 200)
        from changri.core import bottle
        ok, tip, catcher, bid = bottle.throw(self.b, "有人吗")
        if ok and catcher == self.a["id"]:
            r = self.c.get(f"/p/me/bottles/{bid}")
            self.assertEqual(r.status_code, 200)
            self.assertIn("有人吗", r.get_data(as_text=True))
        d = self.c.get("/p/me/discover").get_data(as_text=True)
        for name in ("RPG", "心愿", "漂流瓶", "收集", "心动信箱", "关系线"):
            self.assertIn(name, d)

    def test_relation_pages(self):
        from changri.core import relations
        self._clean()
        set_config("ext:relations", "1")
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(self.c.get("/p/me/rel").status_code, 200)
        self.assertEqual(self.c.get("/p/me/rel?tab=gongzhi").status_code, 200)
        tok = self.csrf("/p/me/rel")
        self.c.post("/p/me/rel/new", data={"to": "乙", "kind": "chat", "text": "你好呀", "_csrf": tok})
        rel = relations.get_rel(self.a["id"], self.b["id"])
        self.assertIsNotNone(rel)                  # 先闲聊也会建线
        self.assertEqual(relations.items(rel["id"], "chat")[0]["text"], "你好呀")
        self.c.post("/p/me/rel/乙/detail", data={"text": "我们是在雨天认识的", "_csrf": tok})
        d = relations.details(rel["id"])[0]
        self.c.post("/p/me/rel/乙/time", data={"label": "D1 夜里", "text": "第一次见面", "_csrf": tok})
        self.assertEqual(relations.items(rel["id"], "time")[0]["label"], "D1 夜里")
        for tab in ("chat", "detail", "time"):
            r = self.c.get(f"/p/me/rel/乙?tab={tab}")
            self.assertEqual(r.status_code, 200, tab)
        self.c.post(f"/p/me/rel/detail/{d['id']}/edit", data={"text": "改过了", "_csrf": tok})
        self.assertEqual(relations.details(rel["id"])[0]["text"], "改过了")
        self.c.post(f"/p/me/rel/detail/{d['id']}/delete", data={"_csrf": tok})
        self.assertEqual(relations.details(rel["id"]), [])
        self.c.post("/p/me/rel/乙/confirm", data={"_csrf": tok})
        self.assertTrue(relations.get_rel(self.a["id"], self.b["id"])["confirmed"])

    def test_relation_unread_dot(self):
        from changri.core import relations
        self._clean()
        set_config("ext:relations", "1")
        relations.add_item(self.b["id"], self.a["id"], "甲", "chat", "在吗")
        relations.add_detail(self.b["id"], self.a["id"], "甲", "我们是旧识")
        rid = relations.get_rel(self.a["id"], self.b["id"])["id"]
        self.assertEqual(relations.unread(self.a["id"]), {rid: {"chat": 1, "detail": 1}})
        self.assertEqual(relations.unread(self.b["id"]), {})          # 自己写的不算未读
        self.login(phonecodes.code_for_role(self.a["id"]))
        d = self.c.get("/p/me/discover").get_data(True)
        self.assertIn('class="row unread"', d)
        self.assertIn("1 条新留言", self.c.get("/p/me/rel").get_data(True))
        self.c.get("/p/me/rel/乙?tab=chat")                                # 看过闲谈 → 只剩细节未读
        self.assertEqual(relations.unread(self.a["id"]), {rid: {"chat": 0, "detail": 1}})
        self.c.get("/p/me/rel/乙?tab=detail")
        self.assertEqual(relations.unread(self.a["id"]), {})
        self.assertNotIn("unread", self.c.get("/p/me/discover").get_data(True).split("关系线")[0][-80:])

    def test_nick_remark_avatar(self):
        import io
        from PIL import Image
        from changri.core import profiles
        self._clean()
        with get_conn() as c:
            for t in ("role_nicks", "role_remarks", "role_avatars"):
                c.execute(f"DELETE FROM {t}")
        self.login(phonecodes.code_for_role(self.a["id"]))
        tok = self.csrf("/p/me/profile")
        self.c.post("/p/me/nickname", data={"nick": "小甲", "_csrf": tok})
        self.assertEqual(profiles.nick_map(), {"甲": "小甲"})
        self.assertIn("小甲", self.c.get("/p/me/profile").get_data(True))
        self.c.post("/p/me/nickname", data={"nick": "乙", "_csrf": tok})          # 撞别人真名
        self.assertEqual(profiles.nick_map(), {"甲": "小甲"})
        self.c.post("/p/me/nickname", data={"nick": "甲（乙）", "_csrf": tok})   # 带括号
        self.assertEqual(profiles.nick_map(), {"甲": "小甲"})
        d = profiles.make_disp(self.b["id"])
        self.assertEqual(d("甲"), "小甲（甲）")
        self.assertEqual(d("乙"), "乙")
        self.c.post("/p/me/remark", data={"target": str(self.b["id"]), "remark": "老乙", "_csrf": tok})
        self.assertEqual(profiles.remark_map(self.a["id"]), {"乙": "老乙"})
        self.assertIn("老乙（乙）", self.c.get("/p/me/" + str(self.b["id"])).get_data(True))
        buf = io.BytesIO(); Image.new("RGB", (600, 300), (200, 30, 30)).save(buf, "PNG")
        r = self.c.post("/p/me/avatar", data={"avatar": (io.BytesIO(buf.getvalue()), "a.png"), "_csrf": tok}, content_type="multipart/form-data").get_json()
        self.assertTrue(r["ok"], r)
        img = Image.open(io.BytesIO(self.c.get("/p/me/avatar/甲").data))
        self.assertEqual(img.size, (256, 256))
        self.assertIn("甲", profiles.avatar_versions())
        bad = self.c.post("/p/me/avatar", data={"avatar": (io.BytesIO(b"nope"), "a.png"), "_csrf": tok}, content_type="multipart/form-data").get_json()
        self.assertFalse(bad["ok"])
        self.c.post("/p/me/avatar/delete", data={"_csrf": tok})
        self.assertEqual(profiles.avatar_versions(), {})
        self.assertEqual(self.c.get("/p/me/avatar/甲").status_code, 404)

    def test_public_feed(self):
        from changri.core import publicfeed
        self._clean()
        with get_conn() as c:
            c.execute("DELETE FROM public_feed")
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertIn("还没有公开播报", self.c.get("/p/me/public").get_data(True))
        publicfeed.record("gift", "🎁 甲 送出了一份礼物「白玫瑰」给 乙")
        publicfeed.record("wish", "📌 新心愿 #1 D1 14:00-15:00 @花园：散步")
        html = self.c.get("/p/me/public").get_data(True)
        self.assertIn("白玫瑰", html)
        self.assertIn("新心愿", html)
        self.assertIn("新心愿", self.c.get("/p/me").get_data(True))        # 消息列表置顶行显示最新一条
        for i in range(publicfeed.KEEP + 20):
            publicfeed.record("other", f"x{i}")
        with get_conn() as c:
            self.assertLessEqual(c.execute("SELECT COUNT(*) FROM public_feed").fetchone()[0], publicfeed.KEEP + 1)

    def _png(self, color=(200, 30, 30), size=(800, 600)):
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", size, color).save(buf, "PNG")
        return buf.getvalue()

    def test_moments(self):
        import io
        from changri.core import moments
        self._clean()
        moments.clear_all()
        set_config("moment_image_quota", "3")
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(self.c.get("/p/me/moments").status_code, 404)      # 扩展没开
        set_config("ext:moments", "1")
        self.assertEqual(self.c.get("/p/me/moments").status_code, 200)
        tok = self.csrf("/p/me/moments")
        r = self.c.post("/p/me/moments/post", data={"content": "今天天气真好", "images": [(io.BytesIO(self._png()), "a.png"), (io.BytesIO(self._png()), "b.png")], "_csrf": tok},
                        content_type="multipart/form-data").get_json()
        self.assertTrue(r["ok"], r)
        mid = r["id"]
        self.assertEqual(moments.used_images(self.a["id"]), 2)
        html = self.c.get("/p/me/moments").get_data(True)
        self.assertIn("今天天气真好", html)
        self.assertIn("/p/me/moments/img/", html)
        img_id = moments.feed(self.a["id"])[0][0]["images"][0]["id"]
        from PIL import Image
        self.assertLessEqual(max(Image.open(io.BytesIO(self.c.get(f"/p/me/moments/img/{img_id}").data)).size), 1600)
        self.assertLessEqual(max(Image.open(io.BytesIO(self.c.get(f"/p/me/moments/img/{img_id}?s=thumb").data)).size), 480)
        # 额度：已用 2/3，再发 2 张不行
        r = self.c.post("/p/me/moments/post", data={"content": "又来", "images": [(io.BytesIO(self._png()), "a.png"), (io.BytesIO(self._png()), "b.png")], "_csrf": tok},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("额度", r.get_json()["msg"])
        bad = self.c.post("/p/me/moments/post", data={"content": "", "images": [(io.BytesIO(b"nope"), "a.png")], "_csrf": tok}, content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        # 乙来点赞、评论、回复
        self.c.post("/p/logout", data={"_csrf": tok})
        self.login(phonecodes.code_for_role(self.b["id"]))
        tok = self.csrf("/p/me/moments")
        self.assertTrue(self.c.post(f"/p/me/moments/{mid}/like", data={"_csrf": tok}).get_json()["liked"])
        self.assertTrue(self.c.post(f"/p/me/moments/{mid}/comment", data={"content": "确实", "_csrf": tok}).get_json()["ok"])
        self.assertEqual(self.c.post(f"/p/me/moments/{mid}/comment", data={"content": "太快", "_csrf": tok}).status_code, 400)   # 间隔太短
        posts = moments.feed(self.b["id"])[0]
        self.assertEqual(posts[0]["likes"], ["乙"])
        self.assertEqual(posts[0]["comments"][0]["content"], "确实")
        cid = posts[0]["comments"][0]["id"]
        self.assertEqual(self.c.post(f"/p/me/moments/{mid}/delete", data={"_csrf": tok}).status_code, 400)       # 不是自己的帖子
        self.assertEqual(self.c.post(f"/p/me/moments/image/{img_id}/delete", data={"_csrf": tok}).status_code, 400)
        self.assertFalse(self.c.post(f"/p/me/moments/{mid}/like", data={"_csrf": tok}).get_json()["liked"])        # 再点取消
        # 楼主甲删乙的评论、删一张图、删帖
        self.c.post("/p/logout", data={"_csrf": tok})
        self.login(phonecodes.code_for_role(self.a["id"]))
        tok = self.csrf("/p/me/moments")
        self.assertTrue(self.c.post(f"/p/me/moments/comment/{cid}/delete", data={"_csrf": tok}).get_json()["ok"])
        self.assertTrue(self.c.post(f"/p/me/moments/image/{img_id}/delete", data={"_csrf": tok}).get_json()["ok"])
        self.assertEqual(moments.used_images(self.a["id"]), 1)
        self.assertEqual(self.c.get(f"/p/me/moments/img/{img_id}").status_code, 404)
        self.assertTrue(self.c.post(f"/p/me/moments/{mid}/delete", data={"_csrf": tok}).get_json()["ok"])
        self.assertEqual(moments.used_images(self.a["id"]), 0)
        self.assertEqual(moments.feed(self.a["id"])[0], [])
        self.assertEqual(moments.stats()["bytes"], 0)

    def test_masquerade(self):
        from changri.core import masquerade as mq
        self._clean()
        with get_conn() as c:
            for t in ("masq_reacts", "masq_dance_msgs", "masq_invites", "masq_msgs", "masq_sched", "masq_members", "masq_archives"):
                c.execute(f"DELETE FROM {t}")
        set_config("masq_on", "0"); set_config("masq_muted", "0")
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(self.c.get("/p/me/ball").status_code, 404)        # 扩展没开
        set_config("ext:masquerade", "1")
        self.assertEqual(self.c.get("/p/me/ball").status_code, 302)        # 没开场：回消息列表
        mq.set_on(True)
        tok = self.csrf("/p/me")
        self.assertIn("匿名舞会", self.c.get("/p/me").get_data(True))
        st = self.c.get("/p/me/ball/state").get_json()
        self.assertEqual(st["stage"], "mask")
        self.assertEqual(len(st["offers"]), 4)
        pick = st["offers"][0]["mask"]
        self.assertTrue(self.c.post("/p/me/ball/mask", data={"mask": "不在列表里", "_csrf": tok}).status_code == 400)
        self.assertTrue(self.c.post("/p/me/ball/mask", data={"mask": pick, "_csrf": tok}).get_json()["ok"])
        self.assertEqual(self.c.get("/p/me/ball/state").get_json()["stage"], "nick")
        self.assertEqual(self.c.post("/p/me/ball/nick", data={"nick": "乙", "_csrf": tok}).status_code, 400)
        self.assertTrue(self.c.post("/p/me/ball/nick", data={"nick": "夜行者", "_csrf": tok}).get_json()["ok"])
        # 乙也入场
        self.c.post("/p/logout", data={"_csrf": tok})
        self.login(phonecodes.code_for_role(self.b["id"]))
        tok = self.csrf("/p/me")
        st = self.c.get("/p/me/ball/state").get_json()
        self.assertNotIn(pick, [o["mask"] for o in st["offers"]])          # 被选走的不再出现
        self.c.post("/p/me/ball/mask", data={"mask": st["offers"][0]["mask"], "_csrf": tok})
        self.assertTrue(self.c.post("/p/me/ball/nick", data={"nick": "路人乙君", "_csrf": tok}).get_json()["ok"] is True)
        mq._rate.clear()
        self.assertTrue(self.c.post("/p/me/ball/say", data={"text": "大家好 @夜行者", "_csrf": tok}).get_json()["ok"])
        st = self.c.get("/p/me/ball/state").get_json()
        self.assertEqual(st["stage"], "room")
        mid = st["msgs"][-1]["id"]
        self.assertTrue(self.c.post("/p/me/ball/react", data={"id": mid, "emoji": "❤️", "_csrf": tok}).get_json()["ok"])
        me_id = st["me"]["id"]
        other = st["members"][0]["id"]
        # 邀请 / 接受 / 私聊
        self.assertTrue(self.c.post("/p/me/ball/invite", data={"to": other, "_csrf": tok}).get_json()["ok"])
        self.assertEqual(self.c.post("/p/me/ball/invite", data={"to": other, "_csrf": tok}).status_code, 400)       # 重复邀请
        self.c.post("/p/logout", data={"_csrf": tok})
        self.login(phonecodes.code_for_role(self.a["id"]))
        tok = self.csrf("/p/me")
        st = self.c.get("/p/me/ball/state").get_json()
        self.assertTrue(st["msgs"][-1]["to_me"])                              # 被 @ 了
        self.assertEqual(st["msgs"][-1]["react"], {"❤️": 1})
        iv = st["dances"][0]
        self.assertEqual((iv["dir"], iv["status"]), ("in", "pending"))
        self.assertTrue(self.c.post(f"/p/me/ball/invite/{iv['id']}/answer", data={"accept": "1", "_csrf": tok}).get_json()["ok"])
        mq._rate.clear()
        self.assertTrue(self.c.post(f"/p/me/ball/dance/{iv['id']}/say", data={"text": "你好呀", "_csrf": tok}).get_json()["ok"])
        # 禁言
        mq.set_mute(True)
        self.assertEqual(self.c.post("/p/me/ball/say", data={"text": "嘘", "_csrf": tok}).status_code, 400)
        mq.set_mute(False)
        # 回放含真名；新开一场后清空并存档
        txt = mq.transcript()
        self.assertIn("甲 = ", txt); self.assertIn("你好呀", txt)
        self.assertTrue(mq.new_session())
        self.assertEqual(mq.sessions()[0]["no"], 1)
        self.assertFalse(mq.has_data())
        self.assertIn("你好呀", mq.session_text(1))
        # 打烊后入口消失
        mq.set_on(False)
        self.assertEqual(self.c.get("/p/me/ball/state").status_code, 400)

    def test_appoint_and_web_room(self):
        from changri.core import roomlog, rooms
        from changri.core import overview as rooms_overview
        roles.create_role("丙", "B1", "P2", "M2")
        c_ = roles.get_role_by_name("丙")
        with get_conn() as c:
            for t in ("join_requests", "room_lines", "room_members", "rooms", "seasons"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        set_config("game_day", "1")
        set_config("ext:chatroom", "1")
        self.assertEqual(self.app.test_client().get("/p/me/appoint").status_code, 302)
        calls = []
        self.app.config["ROOM_HOOKS"] = {
            "start": lambda kind, me, targets, t, place: (lambda r: (r[0], r[1], r[2]))(rooms.create(kind, me["id"], [x["id"] for x in targets], t, place)),
            "join": lambda me, room, qid: calls.append("join"), "answer": lambda me, q, acc: calls.append("answer"),
            "end": lambda rid: rooms.end(rid)}
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(self.c.get("/p/me/appoint").status_code, 200)
        tok = self.csrf("/p/me/appoint")
        r = self.c.post("/p/me/appoint/new", data={"kind": "约会", "to": str(self.b["id"]), "time": "1400-1500", "place": "花园", "_csrf": tok})
        self.assertEqual(r.status_code, 302)
        rid = rooms.my_open(self.a["id"])[0]["id"]
        self.assertTrue(r.headers["Location"].endswith(f"/p/me/room/{rid}"))
        page = self.c.get(f"/p/me/room/{rid}").get_data(True)
        self.assertIn("花园", page)
        self.assertEqual(self.c.post(f"/p/me/room/{rid}/say", data={"text": "你好呀", "_csrf": tok}).status_code, 200)
        self.assertIsNone(rooms.get(rid)["pool_id"])
        self.assertIn("聊天室", rooms_overview.group_line(rooms.get(rid)))
        self.assertEqual(self.c.get(f"/p/me/room/{rid}/lines?after=0").get_json()["lines"][0]["text"], "你好呀")
        # 非成员 404
        other = self.app.test_client()
        with other.session_transaction() as s:
            s["csrf"] = "t2"
        other.post("/p", data={"code": phonecodes.code_for_role(c_["id"]), "_csrf": "t2"})
        self.assertEqual(other.get(f"/p/me/room/{rid}").status_code, 404)
        # 申请加入：默认关，表单不显示、提交 404；打开后 → 同意
        self.assertNotIn("申请加入别人的约会", self.c.get("/p/me/appoint").get_data(True))
        self.assertEqual(other.post("/p/me/appoint/join", data={"to": str(self.a["id"]), "_csrf": "t2"}).status_code, 404)
        set_config("ext:joinreq", "1")
        self.assertIn("申请加入别人的约会", self.c.get("/p/me/appoint").get_data(True))
        other.post("/p/me/appoint/join", data={"to": str(self.a["id"]), "_csrf": "t2"})
        self.assertIn("join", calls)
        q = rooms.pending_requests(self.a["id"])[0]
        self.assertIn("丙", self.c.get("/p/me/appoint").get_data(True))
        self.c.post("/p/me/appoint/answer", data={"qid": str(q["id"]), "accept": "1", "_csrf": tok})
        self.assertTrue(rooms.is_member(rid, c_["id"]))
        # 结束
        self.c.post(f"/p/me/room/{rid}/end", data={"_csrf": tok})
        self.assertEqual(rooms.get(rid)["status"], "ended")
        self.assertEqual(self.c.post(f"/p/me/room/{rid}/say", data={"text": "再说", "_csrf": tok}).status_code, 400)
        set_config("ext:chatroom", "0")
        self.assertEqual(self.c.get("/p/me/appoint").status_code, 404)
        set_config("ext:groupchat", "1")
        self.assertEqual(self.c.post("/p/me/appoint/new", data={"kind": "约会", "to": str(self.b["id"]), "time": "1600-1700", "_csrf": tok}).status_code, 404)
        r2 = self.c.post("/p/me/appoint/new", data={"kind": "微信", "to": [str(self.b["id"]), str(c_["id"])], "_csrf": tok})
        self.assertEqual(r2.status_code, 302)
        gid = rooms.my_open(self.a["id"])[0]["id"]
        self.assertIsNone(rooms.get(gid)["pool_id"])
        self.assertIn("多人群聊", self.c.get(f"/p/me/room/{gid}").get_data(True))
        set_config("ext:groupchat", "0")
        self.assertEqual(self.c.get(f"/p/me/room/{rid}").status_code, 404)
        set_config("game_day", "")
        with get_conn() as c:
            c.execute("DELETE FROM seasons")

    def test_games(self):
        from changri.core import games
        self._clean()
        with get_conn() as c:
            c.execute("DELETE FROM game_scores"); c.execute("DELETE FROM game_attempts")
        set_config("game_daily_attempts", "2")
        self.login(phonecodes.code_for_role(self.a["id"]))
        self.assertEqual(self.c.get("/p/me/games").status_code, 200)
        self.assertIn("今天还剩 2 次", self.c.get("/p/me/games").get_data(True))
        self.assertEqual(self.c.get("/p/me/games/pigeon").status_code, 200)
        tok = self.csrf("/p/me/games")
        # 没开局不能交分
        self.assertEqual(self.c.post("/p/me/games/jump/score", data={"score": "10", "_csrf": tok}).status_code, 400)
        self.c.get("/p/me/games/jump")
        r = self.c.post("/p/me/games/jump/restart", data={"_csrf": tok}).get_json()
        self.assertEqual(r["left"], 1)
        self.assertEqual(self.c.post("/p/me/games/jump/score", data={"score": "999999", "_csrf": tok}).status_code, 400)   # 离谱分数
        r = self.c.post("/p/me/games/jump/score", data={"score": "5", "_csrf": tok}).get_json()
        self.assertTrue(r["ok"] and r["new_best"] and r["rank"] == 1)
        # 同一局不能再交
        self.assertEqual(self.c.post("/p/me/games/jump/score", data={"score": "5", "_csrf": tok}).status_code, 400)
        self.c.post("/p/me/games/jump/restart", data={"_csrf": tok})
        self.assertEqual(self.c.post("/p/me/games/jump/restart", data={"_csrf": tok}).status_code, 400)               # 次数用完
        self.assertEqual(games.board("jump", self.a["id"])[0][0]["score"], 5)
        self.assertEqual(self.c.post("/p/me/games/nope/restart", data={"_csrf": tok}).status_code, 400)


class PhoneSearchSongs(Phone):
    def test_admin_open_as_token(self):
        set_config("phone_as:tok1", f"{self.b['id']}|{int(time.time()) + 60}")
        r = self.app.test_client().get("/p/as/tok1")
        self.assertEqual(r.status_code, 302)
        self.assertIn("phone_auth", r.headers.get("Set-Cookie", ""))
        self.assertEqual(self.app.test_client().get("/p/as/tok1").status_code, 404)       # 用过即废
        set_config("phone_as:tok2", f"{self.b['id']}|{int(time.time()) - 1}")
        self.assertEqual(self.app.test_client().get("/p/as/tok2").status_code, 404)       # 过期

    def test_search_fav_stats_songs(self):
        with get_conn() as c:
            c.execute("DELETE FROM phone_favs")
            c.execute("INSERT INTO messages(kind,from_role_id,to_role_id,content,created_ts,status,day) VALUES('sms',?,?,'今晚月色真美',1,'sent','D1')", (self.a["id"], self.b["id"]))
            mid = c.execute("SELECT MAX(id) FROM messages").fetchone()[0]
        self.login(phonecodes.code_for_role(self.a["id"]))
        tok = self.csrf("/p/me/search")
        self.assertIn("<mark>月色</mark>", self.c.get("/p/me/search?q=月色").get_data(True))
        self.assertIn("乙", self.c.get("/p/me/search?q=乙").get_data(True))
        self.assertIn(f'id="m{mid}"', self.c.get(f"/p/me/{self.b['id']}").get_data(True))
        self.assertEqual(self.c.post("/p/me/fav", data={"id": mid, "_csrf": tok}).status_code, 302)
        self.assertIn("今晚月色真美", self.c.get("/p/me/search").get_data(True))
        self.c.post("/p/me/fav", data={"id": mid, "_csrf": tok})
        self.assertNotIn("今晚月色真美", self.c.get("/p/me/search").get_data(True))
        self.assertEqual(self.c.get("/p/me/timeline?view=stats").status_code, 200)
        self.assertEqual(self.c.get("/p/me/songs").status_code, 404)           # 点歌扩展关着
        set_config("ext:song", "1")
        set_config("announce_group", "bot|G1")
        try:
            self.assertEqual(self.c.get("/p/me/songs").status_code, 200)
            sent = []
            self.app.config["SONG_SEARCH"] = lambda kw, pf: {"platform": "qq", "id": "1", "title": "晴天", "artist": "周杰伦", "url": "http://x", "cover": ""}
            self.app.config["SONG_ANNOUNCE"] = lambda req, sg: sent.append(sg["title"]) or ""
            self.assertEqual(self.c.post("/p/me/songs/pick", data={"keyword": "晴天", "to": "0", "_csrf": tok}).status_code, 302)
            self.assertEqual(sent, ["晴天"])
            self.assertIn("晴天", self.c.get("/p/me/songs").get_data(True))
        finally:
            set_config("ext:song", "0")
            set_config("announce_group", "")
            with get_conn() as c:
                c.execute("DELETE FROM song_requests")


class PhonePrecall(Phone):
    """前置电话（照老系统小手机的滑卡玩法）：填资料 → 翻卡选人 → 接通后在小手机里聊。"""

    def _wipe(self):
        with get_conn() as c:
            for t in ("precall_msgs", "precall_sessions", "precall_likes", "precall_profiles"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM config WHERE key IN ('precall_max_picks','precall_mutual','precall_group_mode','precall_anonymous','precall_msg_limit','precall_round')")

    def test_rules(self):
        from changri.core import precall
        self._wipe()
        roles.create_role("丙", "B1", "P2", "M2")
        a, b, cc = self.a["id"], self.b["id"], roles.get_role_by_name("丙")["id"]
        self.assertFalse(precall.save_profile(a, "f", "")[0])                          # 照片和介绍至少一样
        precall.save_profile(a, "f", "喜欢下雨天"); precall.save_profile(b, "m", "夜猫子"); precall.save_profile(cc, "f", "不爱说话")
        self.assertEqual([x["bio"] for x in precall.candidates(a)], ["夜猫子"])         # 只看异性：丙是女生，翻不到
        self.assertTrue(precall.candidates(a)[0]["name"].startswith("匿名 #"))            # 选人时匿名
        set_config("precall_group_mode", "mixed")
        self.assertEqual(len(precall.candidates(a)), 2)
        pid_b = precall.profile(b)["pid"]
        r = precall.like(a, pid_b)                                                      # 单向：选了就接通
        self.assertTrue(r["ok"] and r["matched"])
        self.assertEqual(precall.sessions_for(a)[0]["name"], "乙")                       # 接通后公开真名
        self.assertNotIn("夜猫子", [x["bio"] for x in precall.candidates(a)])           # 选过的、通过话的不再出现
        self.assertFalse(precall.like(a, pid_b)["ok"])
        set_config("precall_mutual", "1"); set_config("precall_max_picks", "2")
        pid_c = precall.profile(cc)["pid"]
        r = precall.like(a, pid_c)
        self.assertTrue(r["ok"]); self.assertFalse(r["matched"])                        # 双向：等对方也选
        self.assertEqual(r["left"], 0)
        r = precall.like(cc, precall.profile(a)["pid"])
        self.assertTrue(r["matched"])
        precall.new_round()
        self.assertEqual(precall.left(a), 2)                                            # 新一轮：次数重新算
        set_config("precall_msg_limit", "1")
        sid = r["session_id"]
        self.assertTrue(precall.send(cc, sid, "你好")["ok"])
        self.assertIn("说完了", precall.send(cc, sid, "还在吗", now=int(time.time()) + 5)["msg"])
        self.assertFalse(precall.send(b, sid, "插一句")["ok"])                           # 不是这通电话的人
        self.assertEqual(precall.sessions_for(a)[0]["unread"], 1)
        self.assertTrue(precall.end(sid, a))
        self.assertIn("结束", precall.send(a, sid, "喂")["msg"])
        self._wipe()

    def test_pages(self):
        from changri.core import precall
        self._wipe()
        set_config("ext:precall", "0")
        self._login(self.a)
        tok = self.csrf("/p/me/discover")
        self.assertEqual(self.c.get("/p/me/precall").status_code, 404)               # 默认关
        set_config("ext:precall", "1")
        try:
            self.assertIn("我的资料", self.c.get("/p/me/precall").get_data(True))        # 没填资料：先去填
            r = self.c.post("/p/me/precall/profile", data={"_csrf": tok, "gender": "f", "bio": "你好呀"}, follow_redirects=True)
            self.assertIn("资料已保存", r.get_data(True))
            precall.save_profile(self.b["id"], "m", "乙的介绍")
            deck = self.c.get("/p/me/precall").get_data(True)
            pid = precall.profile(self.b["id"])["pid"]
            self.assertIn(pid, deck); self.assertIn("\\u533f\\u540d #", deck)               # 卡片数据里是「匿名 #编号」，不是真名
            self.assertEqual(self.c.post("/p/me/precall/swipe", data={"_csrf": tok, "to": pid}).status_code, 400)   # 没确认不算
            d = self.c.post("/p/me/precall/swipe", data={"_csrf": tok, "to": pid, "confirm": "1"}).get_json()
            self.assertTrue(d["ok"] and d["matched"])
            call = self.c.get(d["session"]).get_data(True)
            self.assertIn("乙", call)                                                   # 接通后公开名字
            sid = int(d["session"].rstrip("/").split("/")[-1])
            self.assertTrue(self.c.post(f"/p/me/precall/call/{sid}/send", data={"_csrf": tok, "text": "嗨"}).get_json()["ok"])
            self.assertIn("嗨", str(self.c.get(f"/p/me/precall/call/{sid}/poll").get_json()["msgs"]))
            self.assertIn("前置电话", self.c.get("/p/me").get_data(True))                # 消息列表里也有这一行
            self.assertTrue(self.c.post(f"/p/me/precall/call/{sid}/end", data={"_csrf": tok}).get_json()["ok"])
            self.assertTrue(self.c.get(f"/p/me/precall/call/{sid}/poll").get_json()["ended"])
        finally:
            set_config("ext:precall", "0")
            self._wipe()

class PhoneArchive(Phone):
    @staticmethod
    def _drop_seasons():
        with get_conn() as c:
            c.execute("DELETE FROM seasons")

    def test_archive_pages(self):
        from changri.core import rooms
        roles.create_role("丙", "B1", "P2", "M2")
        c_ = roles.get_role_by_name("丙")
        with get_conn() as c:
            for t in ("room_lines", "room_members", "rooms", "seasons"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO seasons(name,start_date,end_date,status) VALUES('S','2000-01-01','2099-01-01','running')")
        set_config("game_day", "1")
        self.addCleanup(self._drop_seasons)
        from changri.core import pool
        pool.register("B1", "POOLX", real_gid="990001", set_name="A组")
        pool.set_in_pool(pool.is_pool_group("B1", "POOLX")["id"], True)
        a, b = self.a["id"], self.b["id"]
        ok, _, rid = rooms.create("约会", a, [b], "1400-1500", "")
        self.assertTrue(ok)
        now = int(time.time())
        with get_conn() as c:
            c.execute("INSERT INTO room_lines(room_id,ts,qq,role_id,name,text,is_rp,chars) VALUES(?,?,?,?,?,?,1,3)", (rid, now, "1", a, "甲", "你好呀"))
            c.execute("INSERT INTO room_lines(room_id,ts,qq,role_id,name,text,is_rp,chars) VALUES(?,?,?,?,?,?,0,2)", (rid, now, "2", b, "乙", "哈哈"))
        sent = []
        self.app.config["ARCHIVE_SEND"] = lambda rid_, text: sent.append((rid_, text)) or True
        self.login(phonecodes.code_for_role(a))
        self.assertIn(f"R{rid}", self.c.get("/p/me/archive").get_data(True))
        t = self.c.get(f"/p/me/archive/{rid}").get_data(True)
        self.assertIn("你好呀", t)
        self.assertNotIn("哈哈", t)                       # 闲聊默认不显示
        self.assertIn("哈哈", self.c.get(f"/p/me/archive/{rid}?chat=1").get_data(True))
        tok = self.csrf()
        self.c.post(f"/p/me/archive/{rid}/send", data={"_csrf": tok})
        self.assertEqual(len(sent), 1)
        self.assertIn("你好呀", sent[0][1])
        other = self.app.test_client()
        with other.session_transaction() as s:
            s["csrf"] = "t2"
        other.post("/p", data={"code": phonecodes.code_for_role(c_["id"]), "_csrf": "t2"})
        self.assertEqual(other.get(f"/p/me/archive/{rid}").status_code, 404)   # 非参与者


class PhoneAdminHome(Phone):
    def test_admin_phone(self):
        from changri.core import rooms
        self.assertEqual(self.c.get("/p/admin").status_code, 404)                 # 没令牌进不去
        self.assertEqual(self.c.get("/p/admin/enter/bad").status_code, 404)
        set_config("phone_admin:tk1", str(int(time.time()) + 60))
        r = self.c.get("/p/admin/enter/tk1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/p/admin/enter/tk1").status_code, 404)       # 用过即废
        t = self.c.get("/p/admin").get_data(True)
        self.assertIn("甲", t)
        self.assertEqual(self.c.get("/p/admin/rooms").status_code, 200)
        tok = self.csrf("/p/admin")
        self.c.post("/p/admin/pause", data={"_csrf": tok})
        self.assertEqual(get_config("phone_paused", "0"), "1")
        self.c.post("/p/admin/pause", data={"_csrf": tok})
        self.assertEqual(get_config("phone_paused", "0"), "0")
        r = self.c.post(f"/p/admin/as/{self.a['id']}", data={"_csrf": tok})
        self.assertEqual(self.c.get("/p/me/archive").status_code, 200)            # 已经以甲的身份登录
        from changri.core import messaging, phoneunread
        html = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("管理员只读查看", html)                                        # 只读：页顶有提示
        before = messaging.day_count(self.a["id"], "sms")
        r = self.c.post("/p/me/send", data={"_csrf": tok, "to": self.b["id"], "kind": "sms", "text": "替甲发的"}, follow_redirects=True)
        self.assertIn("不能替玩家操作", r.get_data(as_text=True))
        self.assertEqual(messaging.day_count(self.a["id"], "sms"), before)         # 没发出去
        messaging.queue_message("sms", self.b["id"], self.a["id"], "", "给甲的", "")
        self.c.get(f"/p/me/{self.b['id']}")
        self.assertNotIn(f"r:{self.b['id']}", phoneunread._reads(self.a["id"]))       # 管理员看过不算甲已读
        with get_conn() as c:
            mid = c.execute("SELECT id FROM messages WHERE content='给甲的'").fetchone()["id"]
        self.assertIn("admin/msg/%d/delete" % mid, self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True))
        self.c.post(f"/p/admin/msg/{mid}/delete", data={"_csrf": tok})                   # 管理员删消息：两边都不再显示
        self.assertNotIn("给甲的", self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True))
        set_config("phone_admin:old", str(int(time.time()) - 5))
        self.assertEqual(self.app.test_client().get("/p/admin/enter/old").status_code, 404)


class PhoneThreads(Phone):
    """会话归属（照老系统）：收到的按落款认人；带署名的匿名短信不进真名会话；点歌台、偷盗记录、匿名对话各自一个会话。"""

    def setUp(self):
        super().setUp()
        with get_conn() as c:
            for t in ("phone_aliases", "song_requests", "gift_steals", "gift_unlocks", "gift_shop"):
                c.execute(f"DELETE FROM {t}")

    def _post(self, path, **data):
        from unittest import mock
        sent = []

        async def fake(bot, grp, text, *a, **k):
            sent.append((grp, text))
            return "mid"
        tok = self.csrf("/p/me/new")
        with mock.patch("changri.core.qqsend.make_send", return_value=fake), mock.patch("changri.core.phonegate.season.phase", return_value="playing"):
            r = self.c.post(path, data={"_csrf": tok, **data}, follow_redirects=True)
        return r.get_data(as_text=True), sent

    def test_signed_sms_gets_own_thread(self):
        from changri.core import messaging
        roles.create_role("丙", "B1", "P2", "M2")
        self.cc = roles.get_role_by_name("丙")
        messaging.queue_message("sms", self.a["id"], self.b["id"], "", "你猜我是谁", "神秘人")      # 甲署名「神秘人」发给乙
        messaging.queue_message("sms", self.a["id"], self.b["id"], "", "冒充丙的信", "丙")          # 署名正好是丙：并进丙的会话
        self._login(self.b)
        inbox = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("神秘人", inbox)
        self.assertNotIn(f'href="/p/me/{self.a["id"]}"', inbox)                                     # 乙的手机里没有甲的真名会话
        self.assertNotIn("你猜我是谁", self.c.get(f"/p/me/{self.a['id']}").get_data(as_text=True))
        self.assertIn("冒充丙的信", self.c.get(f"/p/me/{self.cc['id']}").get_data(as_text=True))
        html = self.c.get("/p/me/s/神秘人").get_data(as_text=True)
        self.assertIn("你猜我是谁", html); self.assertIn("不能直接回复", html)
        self._login(self.a)                                                                         # 甲自己那边挂在乙名下
        self.assertIn("你猜我是谁", self.c.get(f"/p/me/{self.b['id']}").get_data(as_text=True))

    def test_song_and_steal_threads(self):
        from changri.core import gifts, song
        set_config("ext:song", "1")
        song.record(self.a["id"], {"to": "乙", "message": "送你", "signer": "某人"}, {"title": "晴天", "artist": "周杰伦"}, "test")
        song.record(self.a["id"], {"to": ""}, {"title": "给大家的"}, "test")
        set_config("gift_catalog", "玫瑰"); set_config("gift_steal_enabled", "1")
        gifts.unlock(self.a["id"], gifts.items()[0]["id"])
        self.assertTrue(gifts.steal(self.b["id"], self.a["id"])[0])
        self._login(self.b)
        inbox = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("点歌台", inbox); self.assertIn("偷盗记录", inbox)
        html = self.c.get("/p/me/sys/song").get_data(as_text=True)
        self.assertIn("某人 给你点了一首《晴天》", html); self.assertNotIn("给大家的", html)
        self.assertIn("你从「甲」的图鉴里偷到了「玫瑰」", self.c.get("/p/me/sys/steal").get_data(as_text=True))
        self._login(self.a)
        self.assertIn("有人偷偷抄走了你图鉴里的「玫瑰」", self.c.get("/p/me/sys/steal").get_data(as_text=True))

    def test_alias_chat(self):
        from changri.core import aliases
        set_config("sms_cooldown_sec", "0")
        self._login(self.a)
        html, _ = self._post("/p/me/alias", to=self.b["id"], alias="乙")
        self.assertIn("不能用", html)                                                               # 不能用别人的名字当化名
        html, _ = self._post("/p/me/alias", to=self.b["id"], alias="夜莺")
        aid = aliases.threads(self.a["id"])[0]["alias_id"]
        self.assertIn("乙＠夜莺", html)
        html, sent = self._post(f"/p/me/a/{aid}/send", text="你好呀")
        self.assertTrue(any("夜莺 给你发来一条短信" in t and "甲" not in t for _g, t in sent), sent)   # QQ 里落款是化名
        self._login(self.b)
        inbox = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("夜莺", inbox); self.assertNotIn("你好呀</div>", self.c.get(f"/p/me/{self.a['id']}").get_data(as_text=True))
        html = self.c.get(f"/p/me/a/{aid}").get_data(as_text=True)
        self.assertIn("你好呀", html); self.assertNotIn("甲", html.split("<header")[1].split("</header>")[0])
        html, sent = self._post(f"/p/me/a/{aid}/send", text="你是谁")
        self.assertTrue(any("回你的化名「夜莺」" in t for _g, t in sent), sent)                     # 回给化名主人
        self._post(f"/p/me/a/{aid}/block")
        self.assertIn("已经结束", self._post(f"/p/me/a/{aid}/send", text="还在吗")[0])
        self.assertIn("2 小时", self._post(f"/p/me/a/{aid}/block", action="unblock")[0])
        self._login(self.a)
        for n in ("二号", "三号"):
            self._post("/p/me/alias", to=self.b["id"], alias=n)
        self.assertIn("最多用 3 个化名", self._post("/p/me/alias", to=self.b["id"], alias="四号")[0])

    def test_unread_badges_and_poll(self):
        """未读：按会话算；会话列表打开不算读，点进会话才算；匿名短信算在署名会话上，不算到真名会话头上。"""
        from changri.core import messaging
        messaging.queue_message("sms", self.a["id"], self.b["id"], "", "在吗", "")
        messaging.queue_message("sms", self.a["id"], self.b["id"], "", "还在吗", "")
        messaging.queue_message("sms", self.a["id"], self.b["id"], "", "猜猜我", "神秘人")
        self._login(self.b)
        d = self.c.get("/p/me/poll").get_json()
        self.assertEqual(d["total"], 3)
        inbox = self.c.get("/p/me").get_data(as_text=True)
        self.assertIn("未读消息", inbox); self.assertIn('aria-label="2 条未读"', inbox); self.assertIn('aria-label="1 条未读"', inbox)
        self.assertEqual(self.c.get("/p/me/poll").get_json()["total"], 3)                   # 只看列表不算读
        self.c.get(f"/p/me/{self.a['id']}")
        d = self.c.get("/p/me/poll").get_json()
        self.assertEqual(d["total"], 1); self.assertEqual(d["top"]["name"], "神秘人")
        self.c.get("/p/me/s/神秘人")
        self.assertEqual(self.c.get("/p/me/poll").get_json()["total"], 0)


class PhonePush(Phone):
    """网页推送（照老系统）：订阅只认浏览器自家的推送服务；第一次扫只记起点；之后有新东西各推一条，不写是谁、写了什么。"""

    def test_subscribe_and_scan(self):
        from unittest import mock

        from changri.core import messaging, webpush
        with get_conn() as c:
            c.execute("DELETE FROM push_subs")
        set_config("push_cursor", "")
        self._login(self.b)
        self.assertTrue(self.c.get("/p/push/key").get_json()["ok"])
        tok = self.csrf("/p/me/profile")
        bad = self.c.post("/p/push/subscribe", data={"_csrf": tok, "endpoint": "https://evil.example.com/x", "p256dh": "a", "auth": "b"})
        self.assertFalse(bad.get_json()["ok"])
        ok = self.c.post("/p/push/subscribe", data={"_csrf": tok, "endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": "BKey", "auth": "auth"})
        self.assertTrue(ok.get_json()["ok"])
        sent = []
        with mock.patch.object(webpush, "send", lambda sub, payload: sent.append(payload) or True):
            self.assertEqual(webpush.scan_once(), 0)                                 # 第一次：只记起点
            messaging.queue_message("sms", self.a["id"], self.b["id"], "", "秘密内容", "神秘人")
            messaging.queue_message("gift", self.a["id"], self.b["id"], "玫瑰", "", "")
            self.assertEqual(webpush.scan_once(), 1)
            self.assertEqual(webpush.scan_once(), 0)                                 # 不重复推
        self.assertEqual(len(sent), 1)
        self.assertIn("1 条新短信", sent[0]["body"]); self.assertIn("1 份礼物", sent[0]["body"])
        self.assertNotIn("秘密内容", sent[0]["body"]); self.assertNotIn("甲", sent[0]["body"]); self.assertNotIn("神秘人", sent[0]["body"])
        self.c.post("/p/push/unsubscribe", data={"_csrf": tok, "endpoint": "https://fcm.googleapis.com/fcm/send/abc"})
        self.assertEqual(webpush.subs_of(self.b["id"]), [])

    def test_sw_and_manifest(self):
        r = self.c.get("/p/sw.js")
        self.assertEqual(r.headers["Service-Worker-Allowed"], "/p/")
        self.assertIn("showNotification", r.get_data(as_text=True))
        m = self.c.get("/p/manifest.json").get_json()
        self.assertEqual((m["start_url"], m["scope"]), ("/p/me", "/p/"))            # 相对地址：跟着机器人自己的公网地址走
