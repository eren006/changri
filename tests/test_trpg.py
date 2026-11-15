import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "trpg.db")
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""

from changri.admin import create_app  # noqa: E402
from changri.core import trpg, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


class TrpgCore(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("trpg_attr_history", "trpg_active", "trpg_card_attrs", "trpg_cards", "trpg_identities", "trpg_players", "config"):
                c.execute(f"DELETE FROM {t}")

    def test_mode_default_and_per_group(self):
        self.assertEqual(trpg.get_mode("G1"), "romance")
        trpg.set_mode("G1", "trpg")
        self.assertEqual(trpg.get_mode("G1"), "trpg")
        self.assertEqual(trpg.get_mode("G2"), "romance")

    def test_parse_and_apply_st(self):
        items = trpg.parse_st("力量60 敏捷 70 SAN:55 hp+5")
        self.assertEqual(items, [("力量", "60"), ("敏捷", "70"), ("理智", "55"), ("生命", "+5")])
        pid = trpg.player_for("M1", "G1")
        card = trpg.active_card("G1", pid, create=True)
        ok, _ = trpg.apply_st(card["id"], [("力量", "60"), ("理智", "55"), ("生命", "10")])
        self.assertTrue(ok)
        ok, lines = trpg.apply_st(card["id"], [("生命", "+5"), ("理智", "-3")])
        self.assertTrue(ok)
        self.assertEqual(trpg.get_attr(card["id"], "HP"), 15)       # 别名 HP = 生命
        self.assertEqual(trpg.get_attr(card["id"], "SAN"), 52)      # 别名 SAN = 理智
        ok, err = trpg.apply_st(card["id"], [("意志", "+1")])
        self.assertFalse(ok)                                        # 没有数值不能增减
        ok, _ = trpg.apply_st(card["id"], [("理智", "-1d6")])
        self.assertTrue(ok)
        self.assertTrue(0 <= trpg.get_attr(card["id"], "理智") <= 51)

    def test_history_records_changes_only(self):
        pid = trpg.player_for("M1", "G1")
        card = trpg.active_card("G1", pid, create=True)
        trpg.apply_st(card["id"], [("力量", "60")])
        trpg.apply_st(card["id"], [("力量", "60")])                 # 没变，不记
        trpg.apply_st(card["id"], [("力量", "+5")], source="后台手改")
        trpg.delete_attr(card["id"], "力量")
        h = trpg.card_history(card["id"])
        self.assertEqual([(x["old_value"], x["new_value"], x["source"]) for x in reversed(h)],
                         [(None, 60, "st"), (60, 65, "后台手改"), (65, None, "st")])

    def test_cards_are_per_player_and_shared_across_groups(self):
        pid = trpg.player_for("M1", "G1")
        self.assertEqual(trpg.player_for("M1", "G2"), pid)           # 同一个 openid = 同一个人
        card, _ = trpg.new_card(pid, "阿明")
        trpg.set_active("G1", pid, card["id"])
        self.assertEqual(trpg.active_card("G2", pid)["id"], card["id"])    # 别的群沿用
        other, _ = trpg.new_card(pid, "阿红")
        trpg.set_active("G2", pid, other["id"])
        self.assertEqual(trpg.active_card("G1", pid)["id"], card["id"])
        self.assertEqual(trpg.active_card("G2", pid)["id"], other["id"])
        self.assertIsNone(trpg.new_card(pid, "阿明")[0])
        self.assertIsNone(trpg.new_card(pid, "有 空格")[0])

    def test_romance_attrs_untouched(self):
        from changri.core import attrs, roles
        roles.create_role("甲", "B", "G1", "M9")
        rid = roles.get_role_by_name("甲")["id"]
        attrs.set_attrs(rid, [("力量", 40)])
        pid = trpg.player_for("M9", "G1")
        card = trpg.active_card("G1", pid, create=True)
        trpg.apply_st(card["id"], [("力量", "80")])
        self.assertEqual(attrs.get_attr(rid, "力量"), 40)
        self.assertEqual(trpg.get_attr(card["id"], "力量"), 80)


class TrpgAdmin(unittest.TestCase):
    def test_pages(self):
        init_db()
        with get_conn() as c:
            for t in ("trpg_attr_history", "trpg_active", "trpg_card_attrs", "trpg_cards", "trpg_identities", "trpg_players", "web_users"):
                c.execute(f"DELETE FROM {t}")
        webusers.create_user("boss", "longenough1", "owner")
        pid = trpg.player_for("M1", "G1")
        trpg.set_player_name(pid, "小明")
        card = trpg.active_card("G1", pid, create=True)
        trpg.apply_st(card["id"], [("力量", "60")])
        c = create_app().test_client()
        tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": tok("/login")})
        html = c.get("/trpg").get_data(as_text=True)
        self.assertIn("小明", html)
        self.assertIn("详情", html)
        detail = c.get(f"/trpg/card/{card['id']}").get_data(as_text=True)
        self.assertIn("力量", detail)
        self.assertIn("60", detail)
        t = tok(f"/trpg/card/{card['id']}")
        c.post(f"/trpg/card/{card['id']}/attr", data={"_csrf": t, "text": "力量+5"})
        self.assertEqual(trpg.get_attr(card["id"], "力量"), 65)
        self.assertEqual(trpg.card_history(card["id"])[0]["source"], "后台手改")
        self.assertIn("后台手改", c.get(f"/trpg/card/{card['id']}").get_data(as_text=True))
        self.assertIn("小明", c.get("/trpg?q=小明").get_data(as_text=True))
        self.assertNotIn("小明", c.get("/trpg?q=不存在").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
