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

    def test_link_code_joins_two_groups(self):
        from unittest import mock
        a = trpg.player_for("MA", "G1")
        code = trpg.make_link_code(a)
        self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{6}$")
        ok, msg = trpg.use_link_code("MB", "G2", code.lower())                        # 不分大小写
        self.assertTrue(ok, msg)
        self.assertEqual(trpg.player_for("MB", "G2", create=False), a)
        self.assertFalse(trpg.use_link_code("MC", "G3", code)[0])                       # 用过就作废
        self.assertTrue(trpg.use_link_code("MB", "G2", trpg.make_link_code(a))[0])       # 本来就是同一位
        c = trpg.player_for("MC", "G3")                                                  # 新群里先有了一位空的：合并过去，空的删掉
        self.assertTrue(trpg.use_link_code("MC", "G3", trpg.make_link_code(a))[0])
        self.assertEqual(trpg.player_for("MC", "G3", create=False), a)
        with get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM trpg_players WHERE id=?", (c,)).fetchone())
        d = trpg.player_for("MD", "G4")                                                  # 这边已经有卡：不自动合并
        trpg.new_card(d, "老卡")
        ok, msg = trpg.use_link_code("MD", "G4", trpg.make_link_code(a))
        self.assertFalse(ok); self.assertIn("管理员", msg)
        code = trpg.make_link_code(a)
        with mock.patch("time.time", return_value=__import__("time").time() + trpg.LINK_TTL + 5):
            self.assertIn("过期", trpg.use_link_code("ME", "G5", code)[1])
        self.assertIn("不对", trpg.use_link_code("ME", "G5", "ZZZZZZ")[1])

    def test_player_for_sender_follows_role_to_new_group(self):
        """换了个人群（角色多了一条群绑定、openid 也换了）：靠角色认回原来那位玩家。"""
        import time as _t
        from changri.core import roles
        with get_conn() as c:
            c.execute("DELETE FROM roles")
        roles.create_role("甲", "B1", "G1", "M1")
        p = trpg.player_for_sender("B1", "G1", "M1")
        self.assertEqual(trpg.player_for_sender("B1", "G1", "M1"), p)
        rid = roles.get_role_by_name("甲")["id"]
        with get_conn() as c:
            c.execute("INSERT INTO role_groups(role_id,bot_id,group_openid,member_openid,kind,created_ts) VALUES (?,?,?,?,'backup',?)",
                      (rid, "B1", "G9", "M9", int(_t.time())))
        self.assertEqual(trpg.player_for_sender("B1", "G9", "M9"), p)
        self.assertEqual(trpg.display_name(p), "甲")
        self.assertNotEqual(trpg.player_for_sender("B1", "GX", "MX"), p)                 # 没角色、没认过：新的一位
        q = trpg.player_for_sender("B1", "G1", "MZ")
        self.assertEqual(trpg.display_name(q), "")

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
