"""特殊道具（系统自带）：默认在物品库里，「使用 道具名 目标」。"""
import asyncio
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "sp.db"))

from changri.core import extensions, roles, rpg, season, sendmsg, specials as S  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class Sent(list):
    async def __call__(self, bot, gid, text):
        self.append((gid, text))
        return "mid"


class Specials(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("tap_outbox", "item_taps", "time_locks", "lovemails", "messages", "room_members", "rooms", "rpg_keys", "rpg_places",
                      "rpg_inventory", "rpg_items", "seasons", "role_groups", "roles", "config"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except Exception:  # noqa: BLE001
                    pass
        for i, n in enumerate("甲乙丙"):
            roles.create_role(n, "B", f"G{i}", f"M{i}")
        self.r = {n: roles.get_role_by_name(n) for n in "甲乙丙"}
        S.rng = random.Random(0)
        set_config("tap_blur", "0")
        rpg.items()                                                        # 物品库第一次被读到时自动建好特殊道具

    def give(self, who, name, n=1):
        rpg.adjust(self.r[who]["id"], name, n)

    def use(self, who, name, *args):
        return S.use(self.r[who], rpg.find_item(name), list(args))

    def left(self, who, name):
        return next((x["count"] for x in rpg.inventory(self.r[who]["id"]) if x["name"] == name), 0)

    def test_seeded_by_default(self):
        names = {x["name"] for x in rpg.items()}
        for _, nm, _ in S.SPECIALS:
            self.assertIn(nm, names)
        rpg.delete_item("SPEC_009")
        rpg.items()
        self.assertIsNone(rpg.find_item("拆信刀"))                       # 管理员删了就不自动补
        self.assertIn("道具 拆信刀", " ".join(rpg.seed_defaults(force=True)))   # 一键初始化补回来

    def test_no_item_and_bad_target(self):
        self.assertFalse(self.use("甲", "截信器", "乙")[0])                 # 背包里没有
        self.give("甲", "截信器")
        self.assertIn("找不到角色", self.use("甲", "截信器", "路人")[1])
        self.assertIn("不能对自己用", self.use("甲", "截信器", "甲")[1])
        self.assertEqual(self.left("甲", "截信器"), 1)                     # 没成功不扣

    def test_fixed_uses_and_counts(self):
        """每件特殊道具用一次（物品库里显示「用 1 次」，后台也改不了）；窃听器 10 条，截信器、回音壁各 5 条。"""
        for code in S.CODES:
            self.assertEqual(rpg.find_item(code)["max_uses"], 1, code)
        self.assertTrue(rpg.save_item("SPEC_006", "窃听器", "item", "改说明", max_uses=-1)[0])
        self.assertEqual(rpg.find_item("SPEC_006")["max_uses"], 1)              # 想改成不限：不认
        self.give("丙", "窃听器"); self.give("丙", "截信器"); self.give("丙", "回音壁")
        self.assertIn("最多 10 条", self.use("丙", "窃听器", "甲")[1])
        self.assertIn("最多 5 条", self.use("丙", "截信器", "甲")[1])
        self.assertIn("最多 5 条", self.use("丙", "回音壁", "乙")[1])
        for code in ("SPEC_006", "SPEC_007", "SPEC_008"):
            self.assertEqual(self.left("丙", rpg.find_item(code)["name"]), 0)    # 用一次就没了

    def test_sms_taps_and_echo_wall(self):
        from unittest import mock
        mock.patch.dict(S.TAP_COUNT, {"SPEC_007": 1, "SPEC_008": 1}).start()
        self.addCleanup(mock.patch.stopall)
        self.give("丙", "截信器"); self.give("丙", "回音壁")
        self.assertTrue(self.use("丙", "截信器", "甲")[0])
        self.assertTrue(self.use("丙", "回音壁", "乙")[0])
        self.assertEqual(self.left("丙", "截信器"), 0)
        asyncio.new_event_loop().run_until_complete(sendmsg.send(self.r["甲"], self.r["乙"], "sms", "", "今晚见", "", Sent()))
        out = [r["text"] for r in S.take_outbox()]
        self.assertTrue(any("截信器" in t and "今晚见" in t for t in out))
        self.assertTrue(any("回音壁" in t and "今晚见" in t for t in out))
        self.assertEqual(S.taps_on(self.r["丙"]["id"]), [])                 # 每个只抄 1 条，用完失效
        self.assertEqual(S.take_outbox(), [])

    def test_phone_tap(self):
        self.give("丙", "窃听器")
        self.use("丙", "窃听器", "甲")
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind, creator_id, status, created_ts) VALUES('电话',?,'open',0)", (self.r["甲"]["id"],)).lastrowid
            other = c.execute("INSERT INTO rooms(kind, creator_id, status, created_ts) VALUES('约会',?,'open',0)", (self.r["甲"]["id"],)).lastrowid
        S.on_room_line(rid, self.r["甲"]["id"], "甲", "喂，听得到吗")
        S.on_room_line(other, self.r["甲"]["id"], "甲", "约会里说的不算")
        S.on_room_line(rid, self.r["乙"]["id"], "乙", "乙说的不算")
        out = [r["text"] for r in S.take_outbox()]
        self.assertEqual(len(out), 1)
        self.assertIn("喂，听得到吗", out[0])

    def test_blur(self):
        S.rng = random.Random(1)
        self.assertIn("▒", S.blur("这是一条很长很长很长的短信内容", 100))
        self.assertEqual(S.blur("原样", 0), "原样")

    def running_day(self):
        season.book("季", "2026-01-01", "2026-12-30")
        season.start(now="2026-06-01")
        season.set_day(2)

    def test_tracker(self):
        self.assertFalse(self.use("丙", "追踪器", "甲")[0])                  # 没道具
        self.give("丙", "追踪器", 3)
        self.assertIn("游戏天数", self.use("丙", "追踪器", "甲", "14")[1])  # 没有季度
        self.running_day()
        with get_conn() as c:
            rid = c.execute("INSERT INTO rooms(kind, creator_id, day, start_hm, end_hm, place, status, created_ts) VALUES('约会',?,2,'14:00','15:00','咖啡馆','open',0)",
                            (self.r["甲"]["id"],)).lastrowid
            for who in "甲乙":
                c.execute("INSERT INTO room_members(room_id, role_id) VALUES(?,?)", (rid, self.r[who]["id"]))
        set_config("item_tracker_success_rate", "100")
        ok, text = self.use("丙", "追踪器", "甲", "14")
        self.assertIn("咖啡馆", text)
        self.assertIn("和 乙 在一起", text)
        self.assertIn("没发现", self.use("丙", "追踪器", "甲", "9")[1])
        self.assertEqual(self.left("丙", "追踪器"), 1)

    def test_master_key(self):
        rpg.save_place("密室", "", True)
        self.give("甲", "万能钥匙")
        self.assertIn("没有叫", self.use("甲", "万能钥匙", "厨房")[1])
        self.assertTrue(self.use("甲", "万能钥匙", "密室")[0])
        self.assertTrue(any(k["name"] == "密室" for k in rpg.keys(self.r["甲"]["id"])))

    def test_mousetrap(self):
        self.give("丙", "捕鼠器")
        self.running_day()
        self.assertIn("钟点", self.use("丙", "捕鼠器", "甲")[1])
        self.assertTrue(self.use("丙", "捕鼠器", "甲", "20")[0])
        from changri.core import godmode
        self.assertIsNotNone(godmode.lock_hit(self.r["甲"]["id"], 2, "20:00", "20:30"))
        self.assertIn("踩中了捕鼠器", S.take_outbox()[0]["text"])

    def test_letter_knife(self):
        self.give("丙", "拆信刀")
        self.assertIn("没用掉", self.use("丙", "拆信刀", "甲")[1])
        with get_conn() as c:
            c.execute("INSERT INTO lovemails(from_role_id, to_role_id, signature, content, day, created_ts) VALUES(?,?,?,?,?,?)",
                      (self.r["乙"]["id"], self.r["甲"]["id"], "匿名", "我一直在看你", "x", 0))
        ok, text = self.use("丙", "拆信刀", "甲")
        self.assertIn("我一直在看你", text)
        self.assertIn("今晚才会收到", text)
        self.assertEqual(self.left("丙", "拆信刀"), 0)

    def test_letter_items_need_letters_module(self):
        self.give("丙", "望远镜")
        self.assertIn("写信综", self.use("丙", "望远镜", "甲")[1])
        extensions.set_enabled("letters", True)
        self.assertTrue(self.use("丙", "望远镜", "甲")[0])
        extensions.set_enabled("letters", False)

    def test_wipe_clears_taps(self):
        self.give("丙", "截信器"); self.use("丙", "截信器", "甲")
        season.wipe()
        with get_conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM item_taps").fetchone()[0], 0)


class OldLibraryMigration(unittest.TestCase):
    def test_0024_sets_specials_to_one_use(self):
        """老库里特殊道具是 -1（显示「不限次数」）：升级后物品库和背包里都变成用 1 次，别的物品不动。"""
        import sqlite3
        from changri.storage import migrate
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path, isolation_level=None)
        for num, p in migrate.available():
            if num > 23:
                break
            migrate._apply(conn, num, p)
        conn.execute("INSERT INTO rpg_items(code, name, max_uses, created_ts) VALUES('SPEC_007', '截信器', -1, 0), ('ITEM_001', '苹果', -1, 0)")
        conn.execute("INSERT INTO roles(id, name, member_openid, created_ts) VALUES(1, '甲', 'M0', 0)")
        conn.execute("INSERT INTO rpg_inventory(role_id, item_code, count, remaining_uses) VALUES(1, 'SPEC_007', 2, -1)")
        conn.close()
        migrate.migrate(path)
        conn = sqlite3.connect(path)
        got = dict(conn.execute("SELECT code, max_uses FROM rpg_items").fetchall())
        inv = conn.execute("SELECT count, remaining_uses FROM rpg_inventory").fetchone()
        conn.close()
        self.assertEqual(got, {"SPEC_007": 1, "ITEM_001": -1})
        self.assertEqual(inv, (2, 1))


if __name__ == "__main__":
    unittest.main()
