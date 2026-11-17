import os
import tempfile
import time
import unittest

os.environ.setdefault("CHANGRI_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

from changri.core import rewards, roles, rooms, rpg
from changri.core.db import get_conn, init_db


class RewardsTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("reward_rules", "reward_log", "reward_adjust", "room_lines", "room_members", "rooms", "rpg_inventory", "rpg_items", "roles"):
                c.execute(f"DELETE FROM {t}")
        self.a = roles.create_role("甲", "B1", "P0", "M0")
        self.b = roles.create_role("乙", "B1", "P1", "M1")
        with get_conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM roles ORDER BY id")]
            self.ia, self.ib = ids[:2]
            c.execute("INSERT INTO rooms(id,kind,status,created_ts) VALUES(1,'约会','ended',?)", (int(time.time()),))
            c.executemany("INSERT INTO room_members(room_id,role_id) VALUES(1,?)", [(self.ia,), (self.ib,)])
            now = int(time.time())
            for i in range(4):
                c.execute("INSERT INTO room_lines(room_id,ts,role_id,name,text,is_rp,chars) VALUES(1,?,?,?,?,1,50)", (now + i * 60, self.ia, "甲", "x"))
            c.execute("INSERT INTO room_lines(room_id,ts,role_id,name,text,is_rp,chars) VALUES(1,?,?,?,?,0,0)", (now, self.ib, "乙", "闲聊"))
        rpg.save_item("GOLD", "金币", kind="currency")

    def _rule(self, conds=None, fixed=None, pool=None, kinds=tuple(rooms.KINDS)):
        fx, err = rewards.rows_fixed(["i:GOLD"], ["10"]) if fixed is None else (fixed, "")
        self.assertEqual(err, "")
        ok, info = rewards.save_rule(None, "认真演", list(kinds), {"segs": [3, None]} if conds is None else conds, fx, pool or [], 1, True)
        self.assertTrue(ok, info)

    def test_pays_only_matching_and_once(self):
        self._rule()
        notes = rewards.settle(1)
        self.assertEqual(list(notes), [self.ia])                      # 乙 0 段，不满足
        self.assertEqual(rewards.settle(1), {})                       # 再结算不重复发
        inv = {x["item_code"]: x["count"] for x in rpg.inventory(self.ia)}
        self.assertEqual(inv["GOLD"], 10)

    def test_kind_filter_and_no_conditions(self):
        self._rule(conds={}, kinds=["电话"])
        self.assertEqual(rewards.settle(1), {})
        rewards.delete_rule(rewards.rules()[0]["id"])
        self._rule(conds={})
        self.assertEqual(set(rewards.settle(1)), {self.ia, self.ib})

    def test_adjust_then_reissue(self):
        self._rule()
        self.assertEqual(set(rewards.settle(1)), {self.ia})
        ok, _ = rewards.adjust(1, self.ib, 5, 400)
        self.assertTrue(ok)
        self.assertEqual(set(rewards.settle(1)), {self.ib})           # 乙调整后补发，甲不重复
        st = {s["name"]: s for s in rewards.room_status(1)}
        self.assertTrue(st["乙"]["adjusted"])
        self.assertTrue(st["乙"]["rules"][0]["log"]["ok"])
        rewards.clear_adjust(1, self.ib)
        self.assertEqual(rewards.metrics(1)[self.ib]["segs"], 0)

    def test_failed_grant_retried(self):
        fx = [{"type": "item", "ref": "NOPE", "n": 1, "p": 100}]
        self._rule(fixed=fx)
        self.assertEqual(rewards.settle(1), {})
        self.assertEqual(rewards.room_status(1)[0]["rules"][0]["log"]["ok"], 0)
        with get_conn() as c:
            c.execute("UPDATE reward_rules SET fixed=?", ('[{"type":"item","ref":"GOLD","n":2}]',))
        self.assertEqual(set(rewards.settle(1)), {self.ia})

    def test_probability_and_limited_stock(self):
        self._rule(fixed=[{"type": "item", "ref": "GOLD", "n": 1, "p": 0}])                      # 0% 永远不发
        self.assertEqual(rewards.settle(1), {})
        rewards.delete_rule(rewards.rules()[0]["id"])
        ok, _ = rewards.save_rule(None, "限量", list(rooms.KINDS), {}, [], [{"ref": "GOLD", "n": 3, "stock": 1, "p": 100}], 1, True)
        self.assertTrue(ok)
        notes = rewards.settle(1)
        self.assertEqual(len(notes), 1)                                                              # 只有 1 份，两个人只有一人拿到
        self.assertEqual(rewards.rules()[0]["pool"][0]["stock"], 0)
        self.assertEqual(rewards.settle(1), {})                                                      # 已经发完，补发也没有了

    def test_max_per_person(self):
        pool = [{"ref": "GOLD", "n": 1, "stock": 5, "p": 100}, {"ref": "TEA", "n": 1, "stock": 5, "p": 100}]
        rpg.save_item("TEA", "热茶")
        ok, _ = rewards.save_rule(None, "限量", list(rooms.KINDS), {"segs": [3, None]}, [], pool, 1, True)
        self.assertTrue(ok)
        rewards.settle(1)
        left = sum(x["stock"] for x in rewards.rules()[0]["pool"])
        self.assertEqual(left, 9)                                                                    # 甲只符合条件，最多拿 1 样

    def test_validation(self):
        self.assertNotEqual(rewards.rows_fixed(["i:NOPE"], ["1"])[1], "")
        self.assertNotEqual(rewards.rows_fixed(["i:GOLD"], ["0"])[1], "")
        self.assertNotEqual(rewards.rows_fixed(["i:GOLD"], ["1"], ["150"])[1], "")
        self.assertEqual(rewards.rows_fixed(["i:GOLD"], ["2"], ["30"])[0][0]["p"], 30)
        self.assertEqual(rewards.rows_fixed(["a:力量"], ["-2"])[1], "")
        self.assertNotEqual(rewards.rows_pool(["i:GOLD"], ["1"], ["0"], ["100"])[1], "")
        ok, _ = rewards.save_rule(None, "空", list(rooms.KINDS), {}, [], [], 1, True)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
