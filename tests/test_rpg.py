import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "rpg.db")

from changri.core import attrs, roles, rpg  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


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

    def test_item_code_auto_generated(self):
        ok, code = rpg.save_item("", "药水")
        self.assertEqual((ok, code), (True, "ITEM_001"))
        self.assertEqual(rpg.save_item("  ", "绷带"), (True, "ITEM_002"))
        self.assertEqual(rpg.save_item("", "金币", kind="currency"), (True, "CUR_001"))
        rpg.save_item("ITEM_003", "手写码")
        self.assertEqual(rpg.save_item("", "新物品"), (True, "ITEM_004"))            # 跳过已被占用的
        self.assertEqual(rpg.save_item("potion", "药水2")[1], "POTION")              # 手写的照旧（转大写）
        self.assertFalse(rpg.save_item("1bad", "x")[0])                              # 手写不合法的照旧拒绝

    def test_bulk_items_and_places(self):
        rpg.save_attr_def("体力", 0, 100, 50, "")
        rpg.save_attr_def("理智", 0, 99, 60, "")
        text = ("金苹果【恢复体力】【5次】【体力+10】\n一封旧信【无限】\n银币【货币】\n毒药【体力-30,理智-5】【不可赠送】\n"
                "金苹果【重复】\n【只有标签】\n坏效果【缺属性+5】\n")
        created, skipped, errors = rpg.bulk_items(text)
        self.assertEqual(created, ["金苹果", "一封旧信", "银币", "毒药"])
        self.assertEqual(skipped, ["金苹果"])                                       # 重名跳过
        self.assertEqual(len(errors), 2)                                            # 只有标签、没注册的属性
        apple = rpg.find_item("金苹果")
        self.assertEqual((apple["max_uses"], apple["description"]), (5, "恢复体力"))
        self.assertIn("体力", apple["attr_effects"])
        self.assertEqual(rpg.find_item("银币")["kind"], "currency")
        self.assertEqual(rpg.find_item("一封旧信")["max_uses"], -1)
        self.assertEqual(rpg.find_item("毒药")["can_give"], 0)
        self.assertTrue(rpg.find_item("金苹果")["code"].startswith("ITEM_"))
        pc, ps, pe = rpg.bulk_places("图书馆：安静的阅读空间\n天台|风很大\n顶楼花园：只有住户能上去【钥匙】\n花园 种满蔷薇\n只写名字\n图书馆：重复\n")
        self.assertEqual(pc, ["图书馆", "天台", "顶楼花园", "花园", "只写名字"])
        self.assertEqual(ps, ["图书馆"]); self.assertEqual(pe, [])
        by = {p["name"]: p for p in rpg.places()}
        self.assertEqual(by["顶楼花园"]["locked"], 1); self.assertEqual(by["顶楼花园"]["description"], "只有住户能上去")
        self.assertEqual(by["花园"]["description"], "种满蔷薇"); self.assertEqual(by["图书馆"]["locked"], 0)
        self.assertIn("已添加", rpg.bulk_summary(pc, ps, pe))

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

    def test_used_and_old_stacks_can_be_taken(self):
        """用过一次的多次数物品、改次数上限以前发的旧存货：管理员能扣、能当材料、扣光以后物品能删（以前都提示「只有 0 件」）。"""
        rpg.save_attr_def("体力", 0, 100, 50, "")
        rpg.save_item("POT", "药水", effects="体力+5", max_uses=3)
        rpg.adjust(self.a, "POT", 2)
        self.assertTrue(rpg.use_item(self.a, "药水")[0])                          # 一瓶剩 2 次，一瓶全新
        self.assertEqual(sorted((x["count"], x["remaining_uses"]) for x in rpg.inventory(self.a)), [(1, 2), (1, 3)])
        self.assertTrue(rpg.adjust(self.a, "POT", -1)[0])                           # 先扣用过的那瓶
        self.assertEqual([(x["count"], x["remaining_uses"]) for x in rpg.inventory(self.a)], [(1, 3)])
        rpg.save_item("POT", "药水", effects="体力+5", max_uses=5)                  # 改了次数上限：旧的那瓶照样能扣
        self.assertTrue(rpg.save_recipe("熬汤", [("POT", 1)], "GOLD", 1)[0])
        self.assertTrue(rpg.craft(self.a, "熬汤")[0])
        self.assertEqual([x["item_code"] for x in rpg.inventory(self.a)], ["GOLD"])
        self.assertTrue(rpg.delete_item("POT")[0])
        self.assertFalse(rpg.adjust(self.a, "GOLD", -2)[0])                         # 不够照样拒绝，什么都不动
        self.assertEqual(rpg.inventory(self.a)[0]["count"], 1)

    def test_use_is_logged(self):
        """消耗型物品用掉也记一笔（以前只记「不消耗」的）。"""
        rpg.save_attr_def("体力", 0, 100, 50, "")
        rpg.save_item("POT", "药水", effects="体力+5", max_uses=1)
        rpg.adjust(self.a, "POT", 1)
        rpg.use_item(self.a, "药水")
        with get_conn() as c:
            row = c.execute("SELECT * FROM rpg_item_log WHERE action='use'").fetchone()
        self.assertEqual((row["item_code"], row["count"]), ("POT", 1))
        self.assertIn("体力 50→55", row["detail"])

    def test_attr_def_in_use_cannot_be_deleted(self):
        rpg.save_attr_def("体力", 0, 100, 50, "")
        rpg.save_item("POT", "药水", effects="体力+5")
        ok, why = rpg.delete_attr_def("体力")
        self.assertFalse(ok)
        self.assertIn("药水", why)
        rpg.save_item("POT", "药水", effects="")
        self.assertTrue(rpg.delete_attr_def("体力")[0])

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
