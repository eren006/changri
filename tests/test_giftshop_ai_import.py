"""后台礼品店「让 AI 帮忙编礼物」：提示词带规则和已上架的名字；AI 写的（代码块、行首编号、横杠）粘进批量导入能直接认。"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "gai.db")

import changri.admin as admin_mod  # noqa: E402
from changri.admin import create_app  # noqa: E402
from changri.core import extensions, gifts, webusers  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402


def token(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


class GiftAiImport(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("gift_unlocks", "gift_shop", "web_users", "config"):
                c.execute(f"DELETE FROM {t}")
        extensions.set_enabled("giftshop", True)

    def test_prompt_lists_rules_and_existing_names(self):
        p = gifts.ai_import_prompt()
        self.assertIn("礼物名｜说明", p)
        self.assertIn("30 字以内", p)
        self.assertIn("100 字以内", p)
        self.assertNotIn("已经有了", p)                     # 还没上架：不列
        gifts.add("白玫瑰"); gifts.add("黄铜怀表", "旧的")
        self.assertIn("别重复：\n白玫瑰、黄铜怀表", gifts.ai_import_prompt())

    def test_import_ai_output(self):
        gifts.add("白玫瑰")
        ai = ("```\n1. 黄铜怀表｜背面刻着一行看不清的小字\n2、旧唱片|七十年代的爵士\n- 手写信\n* 白玫瑰｜重名\n"
              "99朵玫瑰｜数字开头的名字不能被当成编号去掉\n```")
        n, skipped = gifts.import_text(ai)
        self.assertEqual(n, 4)
        self.assertEqual(len(skipped), 1)                    # 白玫瑰已经有了
        got = {x["name"]: x["note"] for x in gifts.items()}
        self.assertEqual(got["黄铜怀表"], "背面刻着一行看不清的小字")
        self.assertEqual(got["旧唱片"], "七十年代的爵士")
        self.assertIn("手写信", got)
        self.assertIn("99朵玫瑰", got)
        self.assertNotIn("```", "".join(got))

    def test_export_still_round_trips(self):
        gifts.add("白玫瑰", "带露水"); gifts.add("手写信")
        text = gifts.export_text()
        gifts.clear()
        self.assertEqual(gifts.import_text(text)[0], 2)
        self.assertEqual({x["name"]: x["note"] for x in gifts.items()}, {"白玫瑰": "带露水", "手写信": ""})

    def test_admin_page_shows_prompt(self):
        admin_mod._fails.clear()
        c = create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(c, "/login")})
        gifts.add("白玫瑰")
        html = c.get("/giftshop").get_data(as_text=True)
        self.assertIn("让 AI 帮忙编礼物", html)
        self.assertIn('id="gsAiCopy"', html)
        self.assertIn("别重复：\n白玫瑰", html)


if __name__ == "__main__":
    unittest.main()
