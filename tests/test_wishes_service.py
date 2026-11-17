"""官网许愿池服务：许愿先进待审核、审核通过才上墙、我也想要每个 IP 只算一次、限流、管理要口令。"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wishes_service as s  # noqa: E402


class WishesService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CHANGRI_WISHES_DIR"] = self.tmp.name
        os.environ["CHANGRI_WISHES_TOKEN"] = "tok-for-test"
        s._hits.clear()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), s.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}/api/wishes"

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()
        os.environ.pop("CHANGRI_WISHES_TOKEN", None)

    def call(self, path="", data=None, token=None, ip="1.1.1.1", method=None):
        headers = {"Content-Type": "application/json", "X-Real-IP": ip}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wall(self):
        return self.call()[1]["wishes"]

    def test_wish_waits_for_review_then_shows(self):
        code, out = self.call(data={"text": "  想要按钮能变颜色  ", "name": "阿林"})
        self.assertEqual(code, 200)
        self.assertEqual(self.wall(), [])                                    # 待审核：墙上没有
        code, adm = self.call("/admin", token="tok-for-test")
        w = adm["wishes"][0]
        self.assertEqual((w["status"], w["text"], w["name"]), ("pending", "想要按钮能变颜色", "阿林"))
        self.assertEqual(self.call(f"/admin/{w['id']}", {"action": "approve"}, token="tok-for-test")[0], 200)
        self.assertEqual([x["text"] for x in self.wall()], ["想要按钮能变颜色"])
        self.call(f"/admin/{w['id']}", {"action": "done"}, token="tok-for-test")
        self.assertEqual(self.wall()[0]["status"], "done")

    def test_admin_needs_token(self):
        self.assertEqual(self.call("/admin")[0], 403)
        self.assertEqual(self.call("/admin", token="wrong")[0], 403)
        self.assertEqual(self.call("/admin/1", {"action": "approve"}, token="wrong")[0], 403)
        os.environ.pop("CHANGRI_WISHES_TOKEN")
        self.assertEqual(self.call("/admin", token="")[0], 403)                # 没设口令：不开放

    def test_upvote_once_per_ip_and_only_on_wall(self):
        self.call(data={"text": "想要许愿池"}, ip="9.9.9.9")
        wid = self.call("/admin", token="tok-for-test")[1]["wishes"][0]["id"]
        self.assertEqual(self.call(f"/{wid}/up", {}, ip="2.2.2.2")[0], 404)   # 还没通过审核：不能点
        self.call(f"/admin/{wid}", {"action": "approve"}, token="tok-for-test")
        self.assertEqual(self.wall()[0]["votes"], 1)                         # 许愿的人自己算一票
        self.call(f"/{wid}/up", {}, ip="2.2.2.2")
        self.call(f"/{wid}/up", {}, ip="2.2.2.2")
        self.call(f"/{wid}/up", {}, ip="9.9.9.9")
        self.assertEqual(self.wall()[0]["votes"], 2)
        self.assertNotIn("2.2.2.2", (Path(self.tmp.name) / "wishes.json").read_text())   # 不存 IP 原文

    def test_sort_by_votes_done_last(self):
        for i, t in enumerate(("一号愿望", "二号愿望", "三号愿望")):
            self.call(data={"text": t}, ip=f"5.5.5.{i}")
        for w in self.call("/admin", token="tok-for-test")[1]["wishes"]:
            self.call(f"/admin/{w['id']}", {"action": "approve"}, token="tok-for-test")
        by = {w["text"]: w["id"] for w in self.wall()}
        self.call(f"/{by['二号愿望']}/up", {}, ip="7.7.7.7")
        self.call(f"/admin/{by['一号愿望']}", {"action": "done"}, token="tok-for-test")
        self.assertEqual([w["text"] for w in self.wall()], ["二号愿望", "三号愿望", "一号愿望"])

    def test_bad_input_and_rate_limit(self):
        self.assertEqual(self.call(data={"text": "a"})[0], 400)
        self.assertEqual(self.call(data=["x"])[0], 400)
        long = self.call(data={"text": "长" * 500, "name": "名" * 40, }, ip="3.3.3.3")
        self.assertEqual(long[0], 200)
        w = self.call("/admin", token="tok-for-test")[1]["wishes"][0]
        self.assertEqual((len(w["text"]), len(w["name"])), (s.TEXT_MAX, s.NAME_MAX))
        codes = [self.call(data={"text": f"第{i}个愿望"}, ip="4.4.4.4")[0] for i in range(s.POST_PER_HOUR + 1)]
        self.assertEqual(codes[-1], 429)

    def test_reject_and_delete(self):
        self.call(data={"text": "广告广告"})
        wid = self.call("/admin", token="tok-for-test")[1]["wishes"][0]["id"]
        self.call(f"/admin/{wid}", {"action": "reject"}, token="tok-for-test")
        self.assertEqual(self.wall(), [])
        self.assertEqual(self.call(f"/admin/{wid}", {"action": "delete"}, token="tok-for-test")[0], 200)
        self.assertEqual(self.call("/admin", token="tok-for-test")[1]["wishes"], [])
        self.assertEqual(self.call(f"/admin/{wid}", {"action": "approve"}, token="tok-for-test")[0], 404)


if __name__ == "__main__":
    unittest.main()
