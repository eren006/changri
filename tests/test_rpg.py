import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "rpg.db")

from core import attrs, roles, rpg  # noqa: E402
from core.db import get_conn, init_db, set_config  # noqa: E402


class RPGTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("rpg_item_log", "rpg_shop", "rpg_inventory", "rpg_place_keys", "rpg_places", "rpg_items",
                      "rpg_attr_defs", "role_attrs", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B", "G1", "M1")
        roles.create_role("乙", "B", "G2", "M2")
        self.a = roles.get_role_by_name("甲")["id"]
        self.b = roles.get_role_by_name("乙")["id"]

    def test_attrs_clamp_and_item_use_charges(self):
        self.assertTrue(rpg.save_attr_def("体力", 0, 10, 5, "")[0])
        attrs.set_attrs(self.a, [("体力", 99)])
        self.assertEqual(attrs.get_attr(self.a, "体力"), 10)
        self.assertTrue(rpg.save_item("ITEM_001", "药水", effects="体力-3", max_uses=2)[0])
        self.assertTrue(rpg.adjust(self.a, "药水", 2)[0])
        self.assertTrue(rpg.use_item(self.a, "ITEM_001", 3)[0])
        self.assertEqual(attrs.get_attr(self.a, "体力"), 1)  # 10-9，仍受下限保护
        inv = rpg.inventory(self.a)
        self.assertEqual([(x["count"], x["remaining_uses"]) for x in inv], [(1, 1)])
        self.assertFalse(rpg.use_item(self.a, "药水", 2)[0])
        self.assertEqual(rpg.inventory(self.a)[0]["remaining_uses"], 1)  # 失败不部分扣次数

    def test_shop_atomic_and_give(self):
        rpg.save_item("COIN", "金币", kind="currency", max_uses=-1)
        rpg.save_item("TEA", "茶", max_uses=1)
        rpg.set_listing("茶", 3, "金币")
        rpg.adjust(self.a, "金币", 5)
        self.assertFalse(rpg.buy(self.a, "茶", 2)[0])
        self.assertEqual(rpg.inventory(self.a)[0]["count"], 5)
        self.assertTrue(rpg.buy(self.a, "茶", 1)[0])
        self.assertTrue(rpg.give(self.a, self.b, "茶", 1)[0])
        self.assertEqual([x["name"] for x in rpg.inventory(self.b)], ["茶"])

    def test_places(self):
        rpg.save_place("图书馆", "", True)
        set_config("place_system_enabled", "1")
        self.assertFalse(rpg.check_place(self.a, "图书馆")[0])
        pid = rpg.places()[0]["id"]
        rpg.set_key(self.a, pid, True)
        self.assertTrue(rpg.check_place(self.a, "图书馆")[0])
        self.assertTrue(rpg.check_place(self.a, "乙的房间")[0])
        set_config("allow_private_rooms", "0")
        self.assertFalse(rpg.check_place(self.a, "乙的房间")[0])


if __name__ == "__main__":
    unittest.main()
