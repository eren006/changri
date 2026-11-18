"""牌堆：长日格式、权重、套牌堆、掷骰、辅助牌堆、导入海豹 json/toml、存取和权限、小手机和后台页面。"""
import os
import random
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "decks.db")

from changri.core import decks, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


def _reset():
    init_db()
    with get_conn() as c:
        for t in ("decks", "deck_files", "phone_codes", "phone_seen", "roles", "role_groups", "config"):
            c.execute(f"DELETE FROM {t}")


class Parse(unittest.TestCase):
    def test_template_parses(self):
        d, errs = decks.parse(decks.TEMPLATE)
        self.assertEqual(errs, [])
        self.assertEqual(list(d), ["今晚吃什么", "_饮料"])
        self.assertIn((3, "火锅"), d["今晚吃什么"])

    def test_errors(self):
        self.assertTrue(decks.parse("米饭\n面条")[1])                      # 没有【名字】
        self.assertTrue(decks.parse("【a b】\nx")[1])                      # 名字有空格
        self.assertTrue(decks.parse("【空的】\n# 只有说明")[1])
        self.assertTrue(decks.parse("【A】\nx\n【a】\ny")[1])               # 同名（不分大小写）
        self.assertTrue(decks.parse("【A】\n101*x")[1])                    # 权重超了

    def test_sealdice_json(self):
        raw = '{"_meta": {"title": "x"}, "抽签": ["::9::小吉", "大吉", "{%__运势}"], "__运势": ["好", "坏"], "空": []}'
        d, why = decks.from_sealdice(raw)
        self.assertEqual(why, "")
        self.assertEqual(d["抽签"], [(9, "小吉"), (1, "大吉"), (1, "{_运势}")])
        self.assertIn("_运势", d)
        self.assertNotIn("空", d)

    def test_sealdice_toml(self):
        raw = '[meta]\ntitle = "t"\n[decks]\n"时间点" = ["早上", "晚上"]\n["论证"]\nexport = true\noptions = ["1", "2"]\n["私有"]\nexport = false\noptions = ["z"]\n'
        d, why = decks.from_sealdice(raw)
        self.assertEqual(why, "")
        self.assertEqual(set(d), {"时间点", "论证", "_私有"})

    def test_not_sealdice(self):
        self.assertEqual(decks.from_sealdice(decks.TEMPLATE), (None, ""))
        self.assertIsNone(decks.from_sealdice("{坏的 json")[0])


class Store(unittest.TestCase):
    def setUp(self):
        _reset()
        roles.create_role("甲", "B1", "P0", "M0")
        roles.create_role("乙", "B1", "P1", "M1")
        self.a, self.b = roles.get_role_by_name("甲")["id"], roles.get_role_by_name("乙")["id"]

    def test_save_draw_and_expand(self):
        ok, msg, fid = decks.save(self.a, decks.TEMPLATE)
        self.assertTrue(ok, msg)
        self.assertEqual([x["name"] for x in decks.listing()], ["今晚吃什么"])      # 辅助牌堆不进列表
        rng = random.Random(1)
        seen = Counter()
        for _ in range(400):
            t = decks.draw("今晚吃什么", rng)
            self.assertTrue(t.startswith("🃏 抽卡「今晚吃什么」："))
            self.assertNotIn("{", t)
            self.assertNotIn("[1d3]", t)
            seen[t.split("：", 1)[1]] += 1
        self.assertGreater(seen["火锅"], seen["白米饭"])                          # 权重 3
        self.assertIn("辅助牌堆", decks.draw("_饮料"))
        three = decks.draw("今晚吃什么 3", rng).split("\n")[1:]
        self.assertEqual(len(set(three)), 3)                                       # 一次抽几张不重复
        self.assertIn("只有 4 张", decks.draw("今晚吃什么 6", rng))

    def test_listing_and_builtin(self):
        self.assertIn("还没有牌堆", decks.draw(""))
        self.assertIn("塔罗", decks.draw(""))
        self.assertTrue(decks.draw("扑克 2").startswith("抽到："))
        self.assertFalse(decks.save(self.a, "【塔罗】\nx")[0])                    # 不能占内置的名字
        self.assertIn("没有叫", decks.draw("不存在"))

    def test_cross_file_ref_and_names(self):
        self.assertTrue(decks.save(self.a, "【天气】\n晴")[0])
        ok, _m, _f = decks.save(self.b, "【出门】\n{天气}天出门，走了 [2d1] 步")
        self.assertTrue(ok)
        self.assertEqual(decks.draw("出门"), "🃏 抽卡「出门」：晴天出门，走了 2 步")
        ok, msg, _f = decks.save(self.b, "【天气】\n雨")                          # 名字被别人用了
        self.assertFalse(ok)
        self.assertIn("已经有叫「天气」的牌堆了", msg)

    def test_loop_does_not_hang(self):
        self.assertTrue(decks.save(self.a, "【甲】\n{乙}\n【乙】\n{甲}")[0])
        self.assertTrue(decks.draw("甲").startswith("🃏"))

    def test_edit_delete_permissions(self):
        ok, _m, fid = decks.save(self.a, "【签】\n上上签")
        self.assertFalse(decks.save(self.b, "【签】\n下下签", file_id=fid)[0])    # 不能改别人的
        self.assertTrue(decks.save(self.a, "【签】\n中签", file_id=fid)[0])
        self.assertEqual(decks.draw("签"), "🃏 抽卡「签」：中签")
        self.assertFalse(decks.delete(fid, self.b))
        self.assertTrue(decks.save(None, "【签】\n管理员改的", file_id=fid)[0])   # 管理员能改
        self.assertTrue(decks.delete(fid))
        self.assertIn("没有叫", decks.draw("签"))

    def test_limit_per_role(self):
        for i in range(decks.MAX_FILES_PER_ROLE):
            self.assertTrue(decks.save(self.a, f"【d{i}】\nx")[0])
        self.assertFalse(decks.save(self.a, "【more】\nx")[0])
        self.assertTrue(decks.save(None, "【admin】\nx")[0])                       # 管理员不限

    def test_import_sealdice_saves_as_our_format(self):
        ok, msg, fid = decks.save(self.a, '{"签": ["::2::吉", "凶"]}')
        self.assertTrue(ok, msg)
        self.assertEqual(decks.get_file(fid)["raw"], "【签】\n2*吉\n凶\n")


class AdminPage(unittest.TestCase):
    """后台「跑团 → 牌堆」页：模板下载、贴内容保存、上传文件、试抽、改、删。牌堆跟小手机没有关系。"""
    def setUp(self):
        _reset()
        os.environ["QQ_BOTS"] = "[]"
        import re
        import changri.admin as admin_mod
        from changri.core import webusers
        os.environ.pop("ADMIN_PASSWORD", None)
        os.environ.pop("FLASK_SECRET_KEY", None)
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        admin_mod._fails.clear()
        set_config("ext:decks", "1")
        self.c = admin_mod.create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        self._tok = lambda path="/login": re.search(r'name="_csrf" value="([0-9a-f]+)"', self.c.get(path).get_data(as_text=True)).group(1)
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self._tok()})

    def test_flow(self):
        page = self.c.get("/decks").get_data(as_text=True)
        self.assertIn("【今晚吃什么】", page)
        self.assertIn("长日牌堆模板", self.c.get("/decks/template.txt").get_data(as_text=True))
        self.c.post("/decks/save", data={"_csrf": self._tok("/decks"), "raw": "【签】\n上上签", "title": "抽签"})
        self.assertIn("抽签", self.c.get("/decks").get_data(as_text=True))
        self.assertIn("🃏 抽卡「签」：上上签", self.c.get("/decks?try=签").get_data(as_text=True))
        import io
        self.c.post("/decks/save", data={"_csrf": self._tok("/decks"), "raw": "", "file": (io.BytesIO('{"运势": ["::2::吉"]}'.encode("utf-8")), "seal.json")},
                    content_type="multipart/form-data")
        self.assertEqual(decks.draw("运势"), "🃏 抽卡「运势」：吉")
        fid = decks.files()[0]["id"]
        self.c.post(f"/decks/{fid}/delete", data={"_csrf": self._tok("/decks")})
        self.assertIn("没有叫", decks.draw("运势"))

    def test_phone_has_no_decks(self):
        from changri.phone.app import create_phone_app
        app = create_phone_app("k" * 32)
        self.assertFalse([r.rule for r in app.url_map.iter_rules() if "deck" in r.rule])


if __name__ == "__main__":
    unittest.main()
