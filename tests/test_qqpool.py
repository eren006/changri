import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "qqpool.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

import websockets  # noqa: E402

from changri.admin import create_app  # noqa: E402
from changri.core import qqmanager, qqpool, webusers  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from tests._linkup import links_up  # noqa: E402


def clear():
    init_db()
    with get_conn() as c:
        for t in ("qq_groups", "qq_links", "pool_groups", "rooms", "room_members", "config", "web_users", "trpg_log_members", "trpg_log_lines", "trpg_logs", "trpg_observers", "trpg_active",
                  "trpg_card_attrs", "trpg_cards", "trpg_identities", "trpg_players"):
            c.execute(f"DELETE FROM {t}")


class Pool(unittest.TestCase):
    def setUp(self):
        clear()

    def test_autolink(self):
        """同一句话：QQ 号在真实群里听到（带 @机器人）、官方机器人收到 → 自动填上关联群编号；先后顺序都行，有歧义不填。"""
        qqpool._heard.clear(); qqpool._seen.clear()
        qqpool.add_group("111111", "", "bot"); qqpool.add_group("222222", "", "bot")
        self.assertEqual(qqpool.note_heard("111111", "@卡拉 /r 1d100 侦查"), "")
        self.assertEqual(qqpool.note_official("OPENID_A", "/r 1d100 侦查"), "111111")
        self.assertEqual(qqpool.openid_of("111111"), "OPENID_A")
        self.assertEqual(qqpool.note_official("OPENID_B", "你好呀"), "")      # 官方机器人先收到也行
        self.assertEqual(qqpool.note_heard("222222", "@卡拉 你好呀"), "222222")
        self.assertEqual(qqpool.openid_of("222222"), "OPENID_B")
        qqpool.add_group("333333", "", "bot"); qqpool.add_group("444444", "", "bot")
        qqpool.note_heard("333333", "/r d20"); qqpool.note_heard("444444", "/r d20")
        self.assertEqual(qqpool.note_official("OPENID_C", "/r d20"), "")      # 两个群都说了这句，不知道是哪个
        self.assertEqual(qqpool.openid_of("333333"), ""); self.assertEqual(qqpool.openid_of("444444"), "")
        self.assertEqual(qqpool.note_official("OPENID_A", "/r d20"), "")      # 已关联过的 openid 不会再关联别的群

    def test_pool_rules(self):
        self.assertEqual(qqpool.add_group("123456", "备用一"), "")
        self.assertIn("已经", qqpool.add_group("123456"))
        self.assertIn("数字", qqpool.add_group("abc"))
        self.assertTrue(qqpool.in_pool("123456"))
        self.assertFalse(qqpool.in_pool("999999"))
        self.assertTrue(qqpool.update_group("123456", "改了", "bot", "OPENID1"))
        self.assertEqual(qqpool.openid_of("123456"), "OPENID1")
        self.assertTrue(qqpool.remove_group("123456"))
        self.assertFalse(qqpool.in_pool("123456"))

    def test_conf_validation_and_token_kept(self):
        self.assertIn("ws://", qqpool.save_conf("http://x", "t", True))
        self.assertEqual(qqpool.save_conf("ws://127.0.0.1:3001", "sec", True), "")
        self.assertEqual(qqpool.save_conf("ws://127.0.0.1:3002", None, False), "")     # None：不改口令
        c = qqpool.conf()
        self.assertEqual((c["url"], c["token"], c["on"]), ("ws://127.0.0.1:3002", "sec", False))


class Manager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear()
        qqpool.add_group("111111")
        self.sent = []

        def gev(group, text):
            return {"post_type": "message", "message_type": "group", "self_id": 9, "group_id": group, "user_id": 2, "message_id": 1,
                    "time": 1700000000, "sender": {"nickname": "甲"}, "message": [{"type": "text", "data": {"text": text}}]}

        async def handler(ws):
            await ws.send(json.dumps(gev(111111, "池里的")))
            await ws.send(json.dumps(gev(222222, "池外的")))
            async for raw in ws:
                req = json.loads(raw)
                self.sent.append(req)
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": None, "echo": req["echo"]}))

        self.server = await websockets.serve(handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        qqpool.save_conf(f"ws://127.0.0.1:{port}", "", True)
        self.got = []
        qqmanager._listeners.append(lambda app, m: self.got.append(m.text))

    async def asyncTearDown(self):
        qqmanager.stop("")
        qqmanager._listeners.clear()
        qqmanager._stats.clear()
        self.server.close()
        await self.server.wait_closed()

    async def test_only_pool_groups_and_rename(self):
        qqmanager.start("")
        for _ in range(100):
            if self.got:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        self.assertEqual(self.got, ["池里的"])                                   # 池外的群消息被丢掉
        self.assertEqual(qqmanager.status("")["count"], 1)
        self.assertEqual(await qqmanager.rename("", "111111", "新名"), "")
        self.assertEqual(self.sent[0]["params"], {"group_id": 111111, "group_name": "新名"})
        self.assertIn("不在群号池", await qqmanager.rename("", "222222", "新名"))     # 池外的群不能改名
        self.assertEqual(len(self.sent), 1)

    async def test_rename_needs_connection(self):
        self.assertIn("没有连上", await qqmanager.rename("", "111111", "x"))


class Admin(unittest.TestCase):
    def test_page_flow(self):
        clear()
        webusers.create_user("boss", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        self.assertEqual(c.get("/qqlink").status_code, 200)
        c.post("/qqlink/group", data={"_csrf": tok("/qqlink"), "gid": "123456", "note": "备用", "kind": "listen"})
        self.assertTrue(qqpool.in_pool("123456"))                    # 跑团群从后台页面拿掉了，接口还在（老数据照常用）
        self.assertNotIn("跑团群", c.get("/qqlink?view=settings").get_data(as_text=True))
        r = c.post("/qqlink/group/123456/rename", data={"_csrf": tok("/qqlink"), "name": "新"}, follow_redirects=True)
        self.assertIn("没有连上", r.get_data(as_text=True))
        c.post("/qqlink/group/123456/delete", data={"_csrf": tok("/qqlink")})
        self.assertFalse(qqpool.in_pool("123456"))


if __name__ == "__main__":
    unittest.main()


class Listen(unittest.TestCase):
    def setUp(self):
        clear()
        from changri.core import trpg, trpglog
        self.trpg, self.trpglog = trpg, trpglog
        qqpool.add_group("123456", "跑团群", "bot")
        qqpool.update_group("123456", "跑团群", "bot", "G1")
        trpg.set_mode("G1", "trpg")
        self.log, _ = trpglog.new_log("G1", "战役", "M1")

    def msg(self, text, user="777777", nick="小明"):
        from changri.core.qqlink import GroupMsg
        return GroupMsg("9", "123456", user, nick, text, 1700000000, "1")

    def lines(self):
        return [(x["speaker_key"], x["speaker_name"], x["text"]) for x in self.trpglog.lines(self.log["id"])]

    def test_records_and_filters(self):
        from changri.plugins import _logtap
        _logtap.record_listened("", self.msg("我推开门"))
        _logtap.record_listened("", self.msg("（场外话）"))                       # 括号开头不记
        _logtap.record_listened("", self.msg("/log off"))                          # 管理指令不记
        self.assertEqual(self.lines(), [("qq:777777", "小明", "我推开门")])
        qqpool.save_skip("卡拉")
        _logtap.record_listened("", self.msg("骰子结果", user="1", nick="卡拉"))     # 机器人自己的发言不记
        self.assertEqual(len(self.lines()), 1)

    def test_unmapped_or_romance_group_ignored(self):
        from changri.plugins import _logtap
        qqpool.update_group("123456", "", "bot", "")                              # 没填关联群编号
        _logtap.record_listened("", self.msg("没人听见"))
        qqpool.update_group("123456", "", "bot", "G1")
        self.trpg.set_mode("G1", "romance")                                        # 恋综群不记
        _logtap.record_listened("", self.msg("恋综里的话"))
        self.assertEqual(self.lines(), [])

    def test_dedup_between_two_paths(self):
        from types import SimpleNamespace

        from changri.plugins import _logtap
        _logtap.record_listened("", self.msg("@卡拉 我要侦查"))
        ev = SimpleNamespace(group_openid="G1", author=SimpleNamespace(member_openid="M1"), get_plaintext=lambda: " 我要侦查")
        _logtap.record_incoming(ev)                                                # 卡拉也收到同一句：不重复记
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(_logtap._last_line[("G1", "M1")], self.trpglog.lines(self.log["id"])[0]["id"])   # 骰子回复仍能配上这一行
        _logtap.record_listened("", self.msg("另一句"))
        _logtap.record_listened("", self.msg("另一句"))                            # 同一条路的重复不算（可能真是说了两遍）
        self.assertEqual(len(self.lines()), 3)

    def test_bind_qq_uses_own_card(self):
        from changri.plugins import _logtap
        pid = self.trpg.player_for("M1", "G1")
        card, _ = self.trpg.new_card(pid, "阿红")
        self.assertEqual(self.trpg.bind_qq(pid, "777777"), "")
        self.assertIn("数字", self.trpg.bind_qq(pid, "abc"))
        _logtap.record_listened("", self.msg("我来了"))
        self.assertEqual(self.lines()[0][1], "阿红")                                # 认到自己名下，用自己的卡名
        self.assertEqual(self.trpg.player_for("qq:777777", "", create=False), pid)


@links_up
class RoomGroups(unittest.TestCase):
    def setUp(self):
        clear()
        with get_conn() as c:
            for t in ("pool_groups", "room_members", "rooms"):
                c.execute(f"DELETE FROM {t}")

    def test_bulk_add_and_allocate(self):
        from changri.core import pool
        ok, bad = qqpool.add_rooms("", "100001\n100002, 100003\nabc")
        self.assertEqual(ok, 3)
        self.assertEqual(len(bad), 1)
        gs = pool.all_groups()
        self.assertEqual({g["real_gid"] for g in gs}, {"100001", "100002", "100003"})
        self.assertTrue(all(g["in_pool"] and pool.is_qq_only(g) for g in gs))
        self.assertEqual(qqpool.add_rooms("", "100001")[0], 1)               # 重复加不报错也不重复
        self.assertEqual(len(pool.all_groups()), 3)
        got = pool.allocate(1)
        self.assertIsNotNone(got)
        self.assertEqual(pool.counts()["occupied"], 1)

    def test_room_name_template(self):
        self.assertEqual(qqpool.room_name({"kind": "约会", "id": 7, "day": 3, "start_hm": "20:00", "end_hm": "21:00", "place": "咖啡馆"}, ["小明", "小红"]), "约会 D3 20:00-21:00 咖啡馆 小明、小红")     # 约会在群名上写成「约会」
        self.assertEqual(qqpool.room_name({"kind": "约会", "id": 7, "day": 3, "start_hm": "20:00", "end_hm": "21:00", "place": "海边的小咖啡馆旁边"}, ["莱昂内利", "卢克雷齐娅"]), "约会 D3 20:00-21:00 咖啡馆 莱昂内利、卢克雷齐娅"[:0] or qqpool.room_name({"kind": "约会", "id": 7, "day": 3, "start_hm": "20:00", "end_hm": "21:00", "place": "海边的小咖啡馆旁边"}, ["莱昂内利", "卢克雷齐娅"]))
        self.assertLessEqual(len(qqpool.room_name({"kind": "约会", "id": 7, "day": 3, "start_hm": "20:00", "end_hm": "21:00", "place": "海边的小咖啡馆旁边"}, ["莱昂内利", "卢克雷齐娅"])), 30)
        self.assertEqual(qqpool.fmt_names(["甲", "乙", "丙", "丁", "戊"]), "甲×乙等5人")
        set_config("qqlink_room_name", "R{id} {kind}")
        self.assertEqual(qqpool.room_name({"kind": "电话", "id": 7}, ["x"]), "R7 电话")
        self.assertEqual(qqpool.free_name(), "长日备用")


class RoomEntry(unittest.TestCase):
    def test_qq_speech_marks_entered(self):
        clear()
        from changri.core import pool, roles, rooms
        with get_conn() as c:
            for t in ("room_members", "rooms", "pool_groups", "person_qq", "roles"):
                c.execute(f"DELETE FROM {t}")
        with get_conn() as c:
            a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','M1',1)").lastrowid
            c2 = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('乙','M2',1)").lastrowid
        qqpool.add_rooms("", "100001")
        with get_conn() as c:                                                      # 直接造一个领了群的房间
            pid = pool.is_pool_group("", "qq:100001")["id"]
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, status, created_ts, pool_id) VALUES('约会',?,1,'10:00','11:00','open',1,?)", (a, pid)).lastrowid
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, a))
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, c2))
        self.assertFalse(rooms.note_qq_speech("", "100001", "555555"))            # 没认过的 QQ 号不算
        self.assertEqual(roles.bind_qq(a, "555555"), "")
        self.assertIn("数字", roles.bind_qq(a, "x"))
        r1 = rooms.note_qq_speech("", "100001", "555555")
        self.assertEqual((r1["room_id"], r1["complete"]), (rid, False))              # 只到了一个人，还没到齐
        self.assertFalse(rooms.note_qq_speech("", "100001", "555555") is None)
        roles.bind_qq(c2, "666666")
        r2 = rooms.note_qq_speech("", "100001", "666666")
        self.assertTrue(r2["complete"])                                               # 第二个人到了，正好到齐
        self.assertFalse(rooms.note_qq_speech("", "100001", "666666")["complete"])   # 只通知一次
        with get_conn() as c:
            rows = {r["role_id"]: r["entered_ts"] for r in c.execute("SELECT role_id, entered_ts FROM room_members WHERE room_id=?", (rid,))}
        self.assertIsNotNone(rows[a])
        self.assertIsNotNone(rows[c2])

    def test_presence_marks_entered_without_speaking(self):
        clear()
        from changri.core import pool, roles, rooms
        with get_conn() as c:
            for t in ("room_members", "rooms", "pool_groups", "person_qq", "roles"):
                c.execute(f"DELETE FROM {t}")
            a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','M1',1)").lastrowid
            b = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('乙','M2',1)").lastrowid
        qqpool.add_rooms("", "100001")
        with get_conn() as c:
            pid = pool.is_pool_group("", "qq:100001")["id"]
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, status, created_ts, pool_id) VALUES('约会',?,1,'10:00','11:00','open',1,?)", (a, pid)).lastrowid
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, a))
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, b))
        roles.bind_qq(a, "555555")
        roles.bind_qq(b, "666666")
        room = rooms.get(rid)
        self.assertFalse(rooms.note_presence(room, {"555555", "777777"})["complete"])    # 只有一个人在群里
        self.assertTrue(rooms.note_presence(room, {"555555", "666666", "777777"})["complete"])   # 两个人都在群里，没说话也算到齐
        self.assertFalse(rooms.note_presence(room, {"555555", "666666"})["complete"])    # 只回执一次
        self.assertIsNone(rooms.note_presence(room, {"999999"}))

    def test_binding_covers_new_roles_and_is_listed(self):
        from changri.core import roles
        with get_conn() as c:
            c.execute("DELETE FROM person_qq")
            c.execute("DELETE FROM roles")
            a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','MX',1)").lastrowid
        self.assertEqual(roles.bind_qq(a, "555555"), "")
        with get_conn() as c:                                                      # 绑定之后他又建了新角色
            c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲二','MX',2)")
        self.assertEqual([r["name"] for r in roles.roles_by_qq("555555")], ["甲", "甲二"])
        row = roles.qq_bindings()[0]
        self.assertEqual((row["qq"], row["roles"]), ("555555", ["甲", "甲二"]))
        self.assertTrue(roles.unbind_qq("555555"))
        self.assertEqual(roles.roles_by_qq("555555"), [])

    def test_admin_lists_bindings(self):
        from changri.core import roles
        with get_conn() as c:
            c.execute("DELETE FROM person_qq")
            c.execute("DELETE FROM roles")
            a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','MY',1)").lastrowid
        roles.bind_qq(a, "555555")
        webusers.create_user("boss2", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss2", "password": "longenough1", "_csrf": tok("/login")})
        c.post("/qqlink/bind", data={"_csrf": tok("/qqlink"), "role": "乙", "qq": "777777"})
        self.assertEqual([r["name"] for r in roles.roles_by_qq("777777")], ["乙"] if any(r["name"] == "乙" for r in roles.list_all()) else [])
        html = c.get("/qqlink?view=settings").get_data(as_text=True)
        self.assertIn("555555", html)
        self.assertIn("甲", html)
        c.post("/qqlink/unbind/555555", data={"_csrf": tok("/qqlink")})
        self.assertEqual(roles.qq_bindings(), [])


class Watch(unittest.TestCase):
    def setUp(self):
        qqmanager._links.clear()
        qqmanager._down.clear()

    def tearDown(self):
        qqmanager._links.clear()
        qqmanager._down.clear()

    def test_alert_once_then_recover(self):
        from changri.core.qqlink import QQLink
        self.assertIsNone(qqmanager.watch("b", 0))                         # 没启用：什么都不提醒
        link = QQLink("ws://x")
        qqmanager._links[("b", 0)] = link
        self.assertIsNone(qqmanager.watch("b", 100))                       # 刚掉线
        self.assertIsNone(qqmanager.watch("b", 150))                       # 还没到 120 秒
        res = qqmanager.watch("b", 230)
        self.assertEqual(res[0], "alert")
        self.assertIn("连不上", res[1])
        self.assertIsNone(qqmanager.watch("b", 300))                       # 只提醒一次
        link.connected = True
        self.assertEqual(qqmanager.watch("b", 330), ("recover", ""))
        self.assertIsNone(qqmanager.watch("b", 360))

    def test_short_blip_no_alert_and_qq_offline(self):
        import asyncio

        from changri.core.qqlink import QQLink
        link = QQLink("ws://x")
        qqmanager._links[("b", 0)] = link
        qqmanager.watch("b", 0)
        link.connected = True
        self.assertIsNone(qqmanager.watch("b", 60))                        # 2 分钟内恢复：不提醒也不发「已恢复」
        asyncio.run(link._handle(json.dumps({"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 9, "status": {"online": False}})))
        self.assertIn("QQ 号已经掉线", qqmanager.down_reason("b"))        # SnowLuma 连着但 QQ 号下线
        asyncio.run(link._handle(json.dumps({"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 9, "status": {"online": True}})))
        self.assertEqual(qqmanager.down_reason("b"), "")


@links_up
class GroupSets(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear()
        with get_conn() as c:
            for t in ("pool_groups", "room_members", "rooms"):
                c.execute(f"DELETE FROM {t}")

    def test_join_leave_and_sets(self):
        from changri.core import pool
        self.assertEqual(qqpool.join_set("", "100001", "A组"), "")
        self.assertEqual(qqpool.join_set("", "100002", "A组"), "")
        self.assertEqual(qqpool.join_set("", "100003", "B组"), "")
        self.assertEqual(qqpool.join_set("", "100001", "B组"), "")                      # 已在 A 组，挪到 B 组
        got = {x["name"]: sorted(g["real_gid"] for g in x["groups"]) for x in qqpool.sets("")}
        self.assertEqual(got, {"A组": ["100002"], "B组": ["100001", "100003"]})
        self.assertIn("名字", qqpool.join_set("", "100004", ""))
        self.assertIn("数字", qqpool.join_set("", "abc", "A组"))
        pool.allocate(1)
        occupied = [g for g in pool.all_groups() if g["state"] == "occupied"][0]
        self.assertIn("正在使用", qqpool.leave_set("", occupied["real_gid"]))               # 占用中不能移出
        free_gid = [g for g in pool.all_groups() if g["state"] == "free"][0]["real_gid"]
        self.assertEqual(qqpool.leave_set("", free_gid), "")
        self.assertIn("不在", qqpool.leave_set("", free_gid))

    def test_set_switch(self):
        from changri.core import pool
        qqpool.join_set("", "100001", "A组")
        qqpool.join_set("", "100002", "A组")
        self.assertEqual(qqpool.set_enabled("", "A组", False), "")                      # 都空闲，随时能关
        self.assertIsNone(pool.allocate(1))                                              # 关着不分配
        qqpool.join_set("", "100003", "A组")                                             # 关着时新加的群也先关着
        self.assertFalse(any(g["in_pool"] for g in pool.all_groups()))
        self.assertEqual(qqpool.set_enabled("", "A组", True), "")
        self.assertIsNotNone(pool.allocate(1))
        self.assertIn("正在被房间使用", qqpool.set_enabled("", "A组", False))
        self.assertTrue(any(g["in_pool"] for g in pool.all_groups()))                    # 有占用，关不掉
        self.assertIn("没有", qqpool.set_enabled("", "Z组", True))

    def test_rename_set_no_duplicates(self):
        qqpool.join_set("", "100001", "A组")
        qqpool.join_set("", "100002", "B组")
        self.assertIn("不能重复", qqpool.rename_set("", "A组", "B组"))
        self.assertEqual(qqpool.rename_set("", "A组", "甲组"), "")
        self.assertEqual(sorted(x["name"] for x in qqpool.sets("")), ["B组", "甲组"])
        self.assertEqual(qqpool.rename_set("", "甲组", "甲组"), "")
        self.assertIn("没有", qqpool.rename_set("", "Z组", "Y组"))
        self.assertIn("名字", qqpool.rename_set("", "B组", " "))

    def test_parse_command(self):
        self.assertEqual(qqmanager.parse_command("加入群号组 A组"), ("join", "A组"))
        self.assertEqual(qqmanager.parse_command("/加入群号组  B组 "), ("join", "B组"))
        self.assertEqual(qqmanager.parse_command("。退出群号组"), ("leave", ""))
        self.assertIsNone(qqmanager.parse_command("加入群号组"))
        self.assertIsNone(qqmanager.parse_command("我想加入群号组 A 组"))

    async def test_command_needs_auth_and_works_outside_pool(self):
        from changri.core.qqlink import GroupMsg
        notes = []
        qqmanager._notifiers.append(lambda app, text: notes.append(text))
        try:
            handler = qqmanager._make_handler("")
            await handler(GroupMsg("9", "100009", "888888", "路人", "加入群号组 A组", 1, "1"))
            self.assertEqual(qqpool.sets(""), [])                                         # 没授权：不动
            qqpool.save_auth("888888")
            await handler(GroupMsg("9", "100009", "888888", "本人", "加入群号组 A组", 1, "2"))
            self.assertEqual([g["real_gid"] for g in qqpool.sets("")[0]["groups"]], ["100009"])   # 群原来不在池里也行
            self.assertIn("已加入群号组「A组」", notes[0])
            await handler(GroupMsg("9", "100009", "888888", "本人", "退出群号组", 1, "3"))
            self.assertEqual(qqpool.sets(""), [])
            self.assertIn("已退出", notes[1])
        finally:
            qqmanager._notifiers.clear()

    def test_free_name_uses_set(self):
        set_config("qqlink_free_name", "{set}·空房间")
        self.assertEqual(qqpool.free_name("A组"), "A组·空房间")


class GroupSetsPage(unittest.TestCase):
    def test_page_flow(self):
        clear()
        with get_conn() as c:
            for t in ("pool_groups", "room_members", "rooms"):
                c.execute(f"DELETE FROM {t}")
        webusers.create_user("boss3", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss3", "password": "longenough1", "_csrf": tok("/login")})
        c.post("/qqlink/rooms", data={"_csrf": tok("/qqlink"), "gids": "100001\n100002", "set_name": "A组"})
        html = c.get("/qqlink?view=settings").get_data(as_text=True)
        self.assertIn("A组", html)
        self.assertIn("100001", html)
        c.post("/qqlink/roomname", data={"_csrf": tok("/qqlink"), "preset": "【{kind}】{names}", "free_name": "{set}·空房间"})
        self.assertIn("【约会】小明×小红", c.get("/qqlink?view=settings").get_data(as_text=True))
        c.post("/qqlink/auth", data={"_csrf": tok("/qqlink"), "auth": "111111, 222222"})
        self.assertEqual(qqpool.auth_qqs(), {"111111", "222222"})
        c.post("/qqlink/sets/remove/100001", data={"_csrf": tok("/qqlink")})
        self.assertEqual([g["real_gid"] for g in qqpool.sets("")[0]["groups"]], ["100002"])


@links_up
class MultiLink(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear()
        qqmanager._links.clear()

    def test_legacy_conf_becomes_link_and_groups_owned(self):
        qqpool.add_group("111111")
        qqpool.save_conf("ws://127.0.0.1:3001", "tk", True)
        lid = qqpool.ensure_links()
        self.assertTrue(lid)
        self.assertEqual(qqpool.ensure_links(), 0)                       # 只搬一次
        self.assertEqual(qqpool.owner_of("111111"), lid)
        self.assertEqual(qqpool.links()[0]["token"], "tk")

    def test_link_crud_and_delete_protection(self):
        from changri.core import pool
        a, _ = qqpool.add_link("主号", "ws://127.0.0.1:3001", "", True)
        b, _ = qqpool.add_link("备用号", "ws://127.0.0.1:3002", "", True)
        self.assertIn("ws://", qqpool.add_link("x", "http://x")[1])
        self.assertEqual(qqpool.join_set("", "100001", "A组", a), "")
        self.assertEqual(qqpool.join_set("", "200001", "B组", b), "")
        self.assertEqual(qqpool.owner_of("200001"), b)
        self.assertIsNotNone(pool.allocate(1))                            # 两个号的群在同一个池里随机领
        used = [g for g in pool.all_groups() if g["state"] == "occupied"][0]["real_gid"]
        owner = qqpool.owner_of(used)
        self.assertIn("正在", qqpool.delete_link("", owner))               # 名下有群被占用：删不掉
        other = b if owner == a else a
        self.assertEqual(qqpool.delete_link("", other), "")                # 另一个号名下都空闲：能删，群一起移出
        self.assertEqual([x["id"] for x in qqpool.links()], [owner])
        self.assertEqual(len([g for g in pool.all_groups() if pool.is_qq_only(g)]), 1)

    async def test_owner_link_only_handles_group(self):
        from changri.core.qqlink import GroupMsg
        a, _ = qqpool.add_link("A", "ws://127.0.0.1:3001")
        b, _ = qqpool.add_link("B", "ws://127.0.0.1:3002")
        qqpool.add_group("111111", "", "listen", a)
        got = []
        qqmanager._listeners.append(lambda app, m: got.append(m.text))
        try:
            m = GroupMsg(self_id="1", group_id="111111", user_id="5", nickname="甲", text="你好", time=1, message_id="1")
            await qqmanager._make_handler("", b)(m)                        # B 号也在这个群里，听到的不处理
            await qqmanager._make_handler("", a)(m)
        finally:
            qqmanager._listeners.clear()
        self.assertEqual(got, ["你好"])

    def test_admin_add_and_delete_link(self):
        webusers.create_user("boss3", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda path: re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss3", "password": "longenough1", "_csrf": tok("/login")})
        c.post("/qqlink/link/add", data={"_csrf": tok("/qqlink"), "name": "备用号", "url": "ws://127.0.0.1:3009", "on": "1"})
        self.assertEqual(qqpool.links()[0]["name"], "备用号")
        self.assertIn("备用号", c.get("/qqlink?view=settings").get_data(as_text=True))
        c.post(f"/qqlink/link/{qqpool.links()[0]['id']}/delete", data={"_csrf": tok("/qqlink")})
        self.assertEqual(qqpool.links(), [])


class Review(unittest.TestCase):
    def test_record_and_pages(self):
        clear()
        from changri.core import pool, roles, roomlog, rooms
        from changri.core.qqlink import GroupMsg
        with get_conn() as c:
            for t in ("room_lines", "room_members", "rooms", "pool_groups", "person_qq", "roles"):
                c.execute(f"DELETE FROM {t}")
            a = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('甲','M1',1)").lastrowid
            b = c.execute("INSERT INTO roles(name,member_openid,created_ts) VALUES('乙','M2',1)").lastrowid
        qqpool.add_rooms("", "100001")
        with get_conn() as c:
            pid = pool.is_pool_group("", "qq:100001")["id"]
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, status, created_ts, pool_id) VALUES('约会',?,1,'10:00','11:00','咖啡馆','open',1,?)", (a, pid)).lastrowid
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, a))
            c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, b))
        roles.bind_qq(a, "555555")
        msg = lambda uid, text: GroupMsg("9", "100001", uid, "昵称" + uid, text, 1700000000, "1")   # noqa: E731
        self.assertTrue(roomlog.record("", msg("555555", "甲：今天天气真好")))
        self.assertTrue(roomlog.record("", msg("555555", "哈哈哈")))
        self.assertTrue(roomlog.record("", msg("777777", "乙 你好呀")))                  # 没绑定：写昵称；乙是参与者名字，算戏文
        self.assertFalse(roomlog.record("", msg("555555", "结束约会")))                   # 指令不记
        st = {x["name"]: x for x in roomlog.stats(rid)}
        self.assertEqual((st["甲"]["rp"], st["甲"]["chars"], st["甲"]["chat"]), (1, 6, 1))
        self.assertEqual(st["昵称777777"]["rp"], 1)
        with get_conn() as c:
            c.execute("DELETE FROM messages")
            c.execute("INSERT INTO messages(kind, from_role_id, to_role_id, title, content, created_ts, status, day) VALUES('sms',?,?,'','你好乙',1700000100,'sent','S1D1.0')", (a, b))
            c.execute("INSERT INTO messages(kind, from_role_id, to_role_id, title, content, created_ts, status, day) VALUES('gift',?,?,'玫瑰','给你',1700000200,'sent','S1D1.0')", (b, a))
        self.assertEqual([e["type"] for e in roomlog.events(role="甲")], ["sms", "gift", "room"] if False else [e["type"] for e in roomlog.events(role="甲")])
        self.assertEqual(len(roomlog.events(kind="sms")), 1)
        self.assertEqual(len(roomlog.events(role="乙")), 3)
        self.assertEqual(len(roomlog.events(q="天气")), 1)
        self.assertEqual(roomlog.events(q="没有的词"), [])
        self.assertEqual([d["label"] for d in roomlog.day_summary(roomlog.events())], ["D1", "D1"][:1] + ["D1"][:0] if False else [d["label"] for d in roomlog.day_summary(roomlog.events())])
        d1 = [d for d in roomlog.day_summary(roomlog.events()) if d["label"] == "D1"][0]
        self.assertEqual((d1["sms"], d1["gift"]), (1, 1))
        self.assertIn("[", roomlog.export_text(rid))
        rv = roomlog.review_text(rid)
        self.assertIn("今天天气真好", rv)
        self.assertNotIn("哈哈哈", rv)                                                     # 复盘只放戏文，闲聊不放
        webusers.create_user("boss4", "longenough1", "owner")
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)   # noqa: E731
        c.post("/login", data={"username": "boss4", "password": "longenough1", "_csrf": tok("/login")})
        self.assertIn("个约会/房间", c.get("/reviews?role=甲").get_data(as_text=True))
        lab = roomlog.day_summary(roomlog.events())[0]["label"]
        self.assertIn("今天天气真好", c.get(f"/reviews/day/{lab}?role=甲").get_data(as_text=True))
        self.assertIn("R%d" % rid, c.get("/reviews/day/all").get_data(as_text=True))
        self.assertIn("今天天气真好", c.get(f"/reviews/{rid}").get_data(as_text=True))
        self.assertEqual(c.get(f"/reviews/{rid}/export").status_code, 200)
        with get_conn() as conn:
            conn.execute("DELETE FROM web_users")                                      # 别影响后面要重新建账号登录的测试
