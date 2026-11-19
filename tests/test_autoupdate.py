"""落后太多时自动更新：该不该更新（decide）、更新前检查（pre_check）、更新后检查（post_check）、后台提示和设置页。"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "a.db")
os.environ["QQ_BOTS"] = "[]"
import changri.admin as admin_mod  # noqa: E402

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import autoupdate, errlog, updater, webusers  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402
from changri.platform import layout  # noqa: E402


def at(hm: str) -> float:
    """今天本地时间 hm 的时间戳。"""
    h, m = map(int, hm.split(":"))
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, m, 0, 0, 0, -1))


def info(n=3, version="0.2.0", **flags):
    pend = [{"version": f"0.1.{i + 1}", "date": "", "auto": True, "urgent": False} for i in range(n)]
    for c in pend:
        c.update(flags)
    return {"version": version, "newer": True, "pending": pend, "size": 1000}


class Base(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("DELETE FROM room_lines")
        self.data = Path(tempfile.mkdtemp())


class DecideTests(Base):
    def test_behind_enough(self):
        self.assertEqual(autoupdate.decide(info(3), data_dir=self.data), (True, "落后 3 个版本"))
        ok, why = autoupdate.decide(info(2), data_dir=self.data)
        self.assertFalse(ok)
        self.assertIn("只落后 2 个", why)

    def test_setting_changes_threshold(self):
        set_config("auto_update_behind", "1")
        self.assertTrue(autoupdate.decide(info(1), data_dir=self.data)[0])

    def test_old_release_date(self):
        i = info(1)
        i["pending"][0]["date"] = "2026-01-01"
        ok, why = autoupdate.decide(i, now=at("05:00"), data_dir=self.data)
        self.assertTrue(ok)
        self.assertIn("天", why)

    def test_disabled(self):
        set_config("auto_update_enabled", "0")
        self.assertFalse(autoupdate.decide(info(5), data_dir=self.data)[0])

    def test_manual_only_version_blocks(self):
        i = info(5)
        i["pending"][2]["auto"] = False
        self.assertEqual(autoupdate.decide(i, data_dir=self.data), (False, "中间有作者要求手动更新的版本"))

    def test_urgent_skips_threshold_and_skip(self):
        i = info(1)
        i["pending"][0]["urgent"] = True
        set_config("update_skip", "0.2.0")
        self.assertEqual(autoupdate.decide(i, data_dir=self.data), (True, "有重要更新"))

    def test_skipped_version_not_auto(self):
        set_config("update_skip", "0.2.0")
        self.assertFalse(autoupdate.decide(info(5), data_dir=self.data)[0])

    def test_failed_version_not_retried(self):
        (self.data / "update_failed.json").write_text(json.dumps({"to": "0.2.0"}))
        self.assertFalse(autoupdate.decide(info(5), data_dir=self.data)[0])
        self.assertTrue(autoupdate.decide(info(5, version="0.3.0"), data_dir=self.data)[0])   # 出了更新的版本再试


class WindowTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(autoupdate.parse_window("04:00-06:00"), (240, 360))
        self.assertEqual(autoupdate.parse_window("23:30～1:00"), (1410, 60))
        self.assertIsNone(autoupdate.parse_window("4点到6点"))
        self.assertIsNone(autoupdate.parse_window("04:00-04:00"))
        self.assertIsNone(autoupdate.parse_window("25:00-26:00"))

    def test_in_window_across_midnight(self):
        win = (1410, 60)
        self.assertTrue(autoupdate.in_window(1420, win))
        self.assertTrue(autoupdate.in_window(30, win))
        self.assertFalse(autoupdate.in_window(60, win))
        self.assertFalse(autoupdate.in_window(600, win))


class PreCheckTests(Base):
    def test_ok_in_window(self):
        self.assertEqual(autoupdate.pre_check(info(), now=at("04:30"), data_dir=self.data), "")

    def test_outside_window(self):
        self.assertIn("不在自动更新时段", autoupdate.pre_check(info(), now=at("12:00"), data_dir=self.data))

    def test_recent_play_blocks(self):
        now = at("04:30")
        with get_conn() as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("INSERT INTO room_lines(room_id, ts, text) VALUES (1, ?, '戏')", (int(now) - 600,))
        self.assertIn("写戏", autoupdate.pre_check(info(), now=now, data_dir=self.data))
        self.assertEqual(autoupdate.pre_check(info(), now=now + 3600, data_dir=self.data), "")

    def test_lovemail_time_blocks(self):
        set_config("lovemail_delivery_time", "04:45")
        self.assertEqual(autoupdate.pre_check(info(), now=at("04:20"), data_dir=self.data), "心动信派送前后")
        set_config("lovemail_enabled", "0")
        self.assertEqual(autoupdate.pre_check(info(), now=at("04:20"), data_dir=self.data), "")

    def test_deliver_now_pending_blocks(self):
        set_config("lovemail_deliver_now", "1")
        self.assertEqual(autoupdate.pre_check(info(), now=at("04:30"), data_dir=self.data), "心动信派送前后")

    def test_disk_space(self):
        with mock.patch.object(autoupdate.shutil, "disk_usage", return_value=mock.Mock(free=1)):
            self.assertIn("磁盘空间不够", autoupdate.pre_check(info(), now=at("04:30"), data_dir=self.data))


class MaybeUpdateTests(Base):
    def test_installs_when_all_ok(self):
        with mock.patch.object(layout, "INSTALLED", True), mock.patch.object(updater, "check", return_value=info(3)), \
                mock.patch.object(autoupdate, "pre_check", return_value=""), mock.patch.object(autoupdate, "DATA_DIR", self.data), \
                mock.patch.object(updater, "update_now", return_value={"to": "0.2.0"}) as up:
            self.assertEqual(autoupdate.maybe_update({"bots_online": 1}), "0.2.0")
        up.assert_called_once_with(extra={"bots_online": 1, "auto": True})

    def test_nothing_when_not_idle(self):
        with mock.patch.object(layout, "INSTALLED", True), mock.patch.object(updater, "check", return_value=info(3)), \
                mock.patch.object(autoupdate, "pre_check", return_value="不在自动更新时段"), \
                mock.patch.object(updater, "update_now") as up:
            self.assertEqual(autoupdate.maybe_update({}), "")
        up.assert_not_called()

    def test_dev_checkout_never(self):
        with mock.patch.object(layout, "INSTALLED", False), mock.patch.object(updater, "check") as ck:
            self.assertEqual(autoupdate.maybe_update({}), "")
        ck.assert_not_called()

    def test_network_error_quiet(self):
        with mock.patch.object(layout, "INSTALLED", True), mock.patch.object(updater, "check", side_effect=updater.UpdateError("x")):
            self.assertEqual(autoupdate.maybe_update({}), "")


class PostCheckTests(Base):
    def run_check(self, pending, bots=1, http=True, jobs=("a", "b")):
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)
        fake_bots = {str(i): object() for i in range(bots)}
        sched = mock.Mock(get_jobs=lambda: [mock.Mock(id=j) for j in jobs])
        with mock.patch.object(updater, "read_pending", return_value=pending), \
                mock.patch.object(updater, "confirm_healthy") as ok, mock.patch.object(updater, "request_rollback") as rb, \
                mock.patch("nonebot.get_bots", return_value=fake_bots), \
                mock.patch.dict(sys.modules, {"nonebot_plugin_apscheduler": mock.Mock(scheduler=sched)}), \
                mock.patch.object(autoupdate, "_http_ok", return_value=http):
            res = asyncio.run(autoupdate.post_check(time.time() - 100, sleep=fake_sleep))
        return res, ok, rb, sleeps

    def test_pass_confirms(self):
        res, ok, rb, _ = self.run_check({"bots_online": 1, "jobs": ["a", "b"]})
        self.assertEqual(res, "通过")
        ok.assert_called_once()
        rb.assert_not_called()

    def test_no_pending_does_nothing(self):
        res, ok, rb, _ = self.run_check(None)
        self.assertEqual(res, "没有待确认的更新")
        ok.assert_not_called()
        rb.assert_not_called()

    def test_bots_missing_waits_then_rolls_back(self):
        res, ok, rb, sleeps = self.run_check({"bots_online": 2}, bots=1)
        self.assertIn("机器人没全连上", res)
        self.assertEqual(sum(sleeps), autoupdate.BOTS_WAIT)
        rb.assert_called_once()
        ok.assert_not_called()

    def test_admin_down_rolls_back(self):
        res, _, rb, _ = self.run_check({}, http=False)
        self.assertIn("设置平台打不开", res)
        rb.assert_called_once()

    def test_missing_job_rolls_back(self):
        res, _, rb, _ = self.run_check({"jobs": ["a", "lovemail_tick"]})
        self.assertIn("lovemail_tick", res)
        rb.assert_called_once()

    def test_error_burst_rolls_back(self):
        rows = [{"ts": int(time.time()), "kind": "error"}] * autoupdate.ERROR_LIMIT
        with mock.patch.object(errlog, "read_all", return_value=rows):
            res, _, rb, _ = self.run_check({})
        self.assertIn("报错", res)
        rb.assert_called_once()

    def test_old_pending_without_snapshot_still_checks(self):
        """从老版本手动更新上来的：待确认记录里没有更新前的样子，只查能对照不需要的几项。"""
        res, ok, _, _ = self.run_check({"from": "0.1.0", "to": "0.2.0"}, bots=0)
        self.assertEqual(res, "通过")
        ok.assert_called_once()


class NoteTests(Base):
    def test_note_and_seen(self):
        self.assertIsNone(autoupdate.note(self.data))
        (self.data / "update_auto.json").write_text(json.dumps({"from": "0.1.0", "to": "0.2.0", "ts": 1}))
        self.assertEqual(autoupdate.note(self.data)["kind"], "done")
        (self.data / "update_failed.json").write_text(json.dumps({"from": "0.1.0", "to": "0.3.0", "message": "回到 0.1.0"}))
        self.assertEqual(autoupdate.note(self.data)["kind"], "failed")            # 失败的先说
        autoupdate.mark_seen(self.data)
        self.assertIsNone(autoupdate.note(self.data))


def token(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)


class PageTests(Base):
    def setUp(self):
        super().setUp()
        with get_conn() as c:
            c.execute("DELETE FROM web_users")
        admin_mod._fails.clear()
        self.c = create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        self.c.post("/login", data={"username": "boss", "password": "longenough1", "_csrf": token(self.c)})

    def test_update_page_shows_and_saves_settings(self):
        html = self.c.get("/update").get_data(as_text=True)
        self.assertIn("自动更新", html)
        self.assertIn('value="04:00-06:00"', html)
        tok = token(self.c, "/update")
        html = self.c.post("/update/auto", data={"_csrf": tok, "auto_update_behind": "5", "auto_update_days": "7", "window": "3:00-5:30"},
                           follow_redirects=True).get_data(as_text=True)
        self.assertIn("已保存", html)
        self.assertFalse(autoupdate.enabled())                       # 没勾开关 = 关
        self.assertEqual((autoupdate.behind_limit(), autoupdate.days_limit(), autoupdate.window_text()), (5, 7, "03:00-05:30"))
        html = self.c.post("/update/auto", data={"_csrf": tok, "on": "1", "window": "早上"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("格式不对", html)
        self.assertTrue(autoupdate.enabled())
        self.assertEqual(autoupdate.window_text(), "03:00-05:30")

    def test_banner_after_auto_update(self):
        with mock.patch.object(autoupdate, "DATA_DIR", self.data):
            (self.data / "update_auto.json").write_text(json.dumps({"from": "0.1.0", "to": "0.2.0", "ts": int(time.time())}))
            html = self.c.get("/accounts").get_data(as_text=True)
            self.assertIn("自动从 0.1.0 更新到 0.2.0", html)
            tok = token(self.c, "/accounts")
            html = self.c.post("/update/auto/seen", data={"_csrf": tok, "next": "/accounts"}, follow_redirects=True).get_data(as_text=True)
            self.assertNotIn("自动从 0.1.0 更新到 0.2.0", html)
            r = self.c.post("/update/auto/seen", data={"_csrf": tok, "next": "//evil.example"})
            self.assertNotIn("evil", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
