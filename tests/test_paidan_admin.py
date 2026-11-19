"""排单后台：关着时页面 404、菜单不显示；打开后建业务、客户、额度，补录、预览扣法、完成，兑换码，暂停，设置。"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["QQ_BOTS"] = "[]"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

import changri.core.db as db  # noqa: E402
from changri.core import botconf, webusers  # noqa: E402

B = "/b/111111"


class PaidanAdmin(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.saved = (db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE, set(db._inited))
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = d / "changri.db", d / "bots", False
        db._inited.clear()
        db.forget_bots()
        from changri.admin import create_app
        self.app = create_app()
        webusers.create_user("boss", "longenough1", "owner")
        botconf.add_account("111111", "a" * 20, "甲号")
        self.c = self.app.test_client()
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": self._csrf("/login")})
        self.tok = db.enter_bot("111111")         # 测试里直接读写这个机器人的库

    def tearDown(self):
        db.leave_bot(self.tok)
        db.DB_PATH, db.BOTS_DIR, db.LEGACY_SINGLE = self.saved[:3]
        db._inited.clear()
        db._inited.update(self.saved[3])
        db.forget_bots()

    def _csrf(self, path):
        html = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

    def post(self, path, **data):
        data["_csrf"] = self._csrf(B + "/paidan/settings")
        return self.c.post(B + path, data=data, follow_redirects=True)

    def on(self):
        self.c.post(B + "/extensions/toggle", data={"id": "paidan", "on": "1", "_csrf": self._csrf(B + "/extensions")})

    def test_off_hidden(self):
        self.assertEqual(self.c.get(B + "/paidan").status_code, 404)
        self.assertNotIn("附属功能", self.c.get(B + "/").get_data(as_text=True))
        self.assertIn("排单", self.c.get(B + "/extensions?tab=custom").get_data(as_text=True))

    def test_full_flow(self):
        from changri.core import paidan as pd
        self.on()
        home = self.c.get(B + "/paidan").get_data(as_text=True)
        self.assertIn("附属功能", home)
        self.assertIn("还没有业务", home)

        html = self.post("/paidan/biz/save", letter="a", name="溶图", days="7", slots="2", template="名称：\n尺寸：").get_data(as_text=True)
        self.assertIn("记得在下单格式里加上", html)
        bid = pd.business_by_letter("A")["id"]
        self.assertIn("QQ号：", self.c.get(B + "/paidan/biz").get_data(as_text=True))
        self.assertIn("工作群绑定码", self.post(f"/paidan/biz/{bid}/code").get_data(as_text=True))

        html = self.post("/paidan/clients/new", name="小明", qq="123456", real_gid="987654321", **{f"slots_{bid}": "3"}).get_data(as_text=True)
        self.assertIn("绑定码", html)
        cid = pd.clients()[0]["id"]
        self.assertEqual((pd.client(cid)["qq"], pd.client(cid)["real_gid"], pd.slots_of(cid, bid)), ("123456", "987654321", 3))
        self.post(f"/paidan/clients/{cid}/quota", bid=str(bid), total="1", expires="2099-01-01")
        self.post(f"/paidan/clients/{cid}/quota", bid=str(bid), total="5", expires="2099-06-01")
        self.assertIn("A 6 张", self.c.get(B + "/paidan/clients").get_data(as_text=True))
        q1 = pd.quotas_of(cid)[0]["id"]
        self.assertIn("已延期", self.post(f"/paidan/quota/{q1}/extend", expires="2099-02-01").get_data(as_text=True))

        self.post("/paidan/orders/new", bid=str(bid), cid=str(cid), content="名称：猫")
        o = pd.orders("open")[0]
        self.assertIn(o["no"], self.c.get(B + "/paidan/orders").get_data(as_text=True))
        prev = self.c.get(B + f"/paidan/orders/{o['id']}?n=3").get_data(as_text=True)
        self.assertIn("扣 1 张", prev)
        self.assertIn("扣 2 张", prev)
        self.assertIn("已改好下单内容", self.post(f"/paidan/orders/{o['id']}/edit", content="名称：狗").get_data(as_text=True))
        self.assertIn("客户群没绑定", self.post(f"/paidan/orders/{o['id']}/complete", n="3").get_data(as_text=True))
        self.assertEqual(pd.order(o["id"])["status"], "done")
        self.assertEqual(pd.quota_left(cid, bid), 3)
        self.post(f"/paidan/orders/{o['id']}/complete", n="1")                 # 改扣法
        self.assertEqual(pd.quota_left(cid, bid), 5)

        html = self.post("/paidan/sandan/gen", bid=str(bid), n="3", expires="2099-01-01").get_data(as_text=True)
        self.assertIn("已生成 3 个兑换码", html)
        self.assertIn("复制全部", html)
        self.assertEqual(len(pd.codes()), 3)

        o2, _ = pd.place_order(bid, "x", code=pd.codes()[0]["code"])
        self.assertIn("兑换码作废", self.c.get(B + f"/paidan/orders/{o2['id']}").get_data(as_text=True))
        self.post(f"/paidan/orders/{o2['id']}/cancel")
        self.assertEqual(pd.order(o2["id"])["status"], "cancelled")

        self.assertIn("已标记已完结", self.post(f"/paidan/clients/{cid}/archive", on="1").get_data(as_text=True))
        self.assertIn("已完结的客户", self.c.get(B + "/paidan/clients").get_data(as_text=True))
        self.post(f"/paidan/clients/{cid}/archive", on="0")
        self.assertFalse(pd.client(cid)["archived"])
        html = self.post("/paidan/pause/today").get_data(as_text=True)
        self.assertIn("今天已暂停", html)                                       # 机器人不在线：通知没发出去也不报错
        self.assertTrue(pd.is_paused())
        self.assertIn("今天暂停中", self.c.get(B + "/paidan").get_data(as_text=True))
        self.post("/paidan/pause/add", day="2099-01-02")
        self.assertIn("2099-01-02", self.c.get(B + "/paidan/pause").get_data(as_text=True))
        self.post("/paidan/pause/cancel", day="2099-01-02")
        self.assertFalse(pd.is_paused("2099-01-02"))

        self.assertIn("时间要写成", self.post("/paidan/settings", remind="25:00", pause_hm="00:00").get_data(as_text=True))
        self.post("/paidan/settings", remind="11:30", pause_hm="07:00")
        self.assertEqual((db.get_config("pd_remind_hm"), db.get_config("pd_pause_hm")), ("11:30", "07:00"))
        self.assertIn("owner 绑定码", self.post("/paidan/settings/owner").get_data(as_text=True))
        for page in ("", "/biz", "/clients", f"/clients/{cid}", "/orders?st=all", "/sandan", "/pause", "/settings"):
            self.assertEqual(self.c.get(B + "/paidan" + page).status_code, 200, page)


if __name__ == "__main__":
    unittest.main()
