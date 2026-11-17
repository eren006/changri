import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")

from changri.core import extensions as ec, rooms, season, storycal as cal  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class StoryCalTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            c.execute("DELETE FROM config")
        set_config("game_day", "1")

    def on(self):
        ec.set_enabled("storycal", True)

    def test_default_off_and_parse_unchanged(self):
        self.assertFalse(ec.is_enabled("storycal"))
        self.assertIsNone(rooms.parse_hm("3月5日"))                        # 关着时不认
        self.assertEqual(rooms.parse_hm("1400-1500"), ("14:00", "15:00"))

    def test_span_parsing(self):
        self.assertEqual(cal.parse_span("3月1日-3月31日"), ((3, 1), (3, 31)))
        self.assertEqual(cal.parse_span("3月5日到7日"), ((3, 5), (3, 7)))
        self.assertEqual(cal.parse_span("3月5号"), ((3, 5), (3, 5)))
        self.assertIsInstance(cal.parse_span("3月32日"), str)
        self.assertIsInstance(cal.parse_span("3月9日-3月1日"), str)

    def test_conversion_and_pace(self):
        self.on()
        self.assertEqual(cal.save("康熙十四年", {0: "3月1日-3月30日", 1: "6月1日-6月30日", 2: "9月1日-9月30日"}), [])
        self.assertEqual(len(cal.day_list(0)), 30)
        self.assertEqual(cal.day_minutes(0), 48)
        self.assertEqual(cal.stage_line(0), "D0 = 康熙十四年3月1日-3月30日")   # 公告里不带「1 故事日 = 几分钟」
        self.assertEqual(cal.label_at(0, 0), "康熙十四年3月1日")
        self.assertEqual(cal.label_at(0, 48), "康熙十四年3月2日")
        self.assertEqual(cal.label_at(0, 1439), "康熙十四年3月30日")
        self.assertEqual(cal.parse_when("3月2日", 0)[0], ("00:48", "01:36"))
        self.assertEqual(cal.parse_when("3月30日", 0)[0], ("23:12", "23:59"))
        self.assertIn("只能写一天", cal.parse_when("3月29日-3月30日", 0)[1])
        self.assertIn("只到", cal.parse_when("6月1日", 0)[1])              # 不在这个 D 里
        cal.save("康熙十四年", {0: "3月1日-3月31日", 1: "6月1日-6月20日"})   # 每个 D 流速可以不同
        self.assertAlmostEqual(cal.day_minutes(0), 1440 / 31)
        self.assertEqual(cal.day_minutes(1), 72)

    def test_rooms_accept_story_date(self):
        self.on()
        cal.save("康熙十四年", {1: "6月1日-6月30日"})
        self.assertEqual(rooms.parse_hm("6月5日"), ("03:12", "04:00"))
        self.assertIsNone(rooms.parse_hm("1400-1500"))                      # 开着且这个 D 填了日期：不能写钟点
        self.assertIn("故事里的某一天", cal.format_error())
        set_config("game_day", "5")                                          # 这个 D 没填：照旧写钟点，免得谁都约不了
        self.assertEqual(rooms.parse_hm("1400-1500"), ("14:00", "15:00"))
        self.assertIsNone(rooms.parse_hm("6月5日"))

    def test_save_rejects_overlap(self):
        errs = cal.save("康熙", {0: "3月1日-3月31日", 1: "3月31日-6月20日"})        # D1 开头没在 D0 结束之后
        self.assertTrue(errs and "不能重叠" in errs[0])
        self.assertEqual(cal.stages(), {})                                          # 有错整体不存
        self.assertEqual(cal.save("康熙", {0: "3月1日-3月31日", 1: "4月1日-6月20日", 2: "9月1日-9月30日"}), [])   # 紧接着、中间留空都行

    def test_bad_save_keeps_old(self):
        cal.save("康熙", {0: "3月1日-3月31日"})
        self.assertTrue(cal.save("明", {0: "乱写"}))
        self.assertEqual(cal.era(), "康熙")


    def test_story_labels_for_display_and_examples(self):
        from changri.core import overview
        room = {"kind": "私约", "day": 1, "start_hm": "12:00", "end_hm": "13:00", "place": "花园"}
        self.assertEqual(overview.slot_text(room), "D1 12:00-13:00 @花园")           # 关着：和以前一样
        self.assertEqual(cal.example_time(), "1400-1500")
        self.on()
        cal.save("康熙十四年", {1: "6月1日-6月30日"})
        self.assertEqual(cal.label_for(1, 12 * 60), "康熙十四年6月16日")
        self.assertEqual(cal.label_for_slot(1, "12:00"), "康熙十四年6月16日")
        self.assertEqual(cal.label_for(2, 0), "")                                   # 这个 D 没填：不显示
        self.assertEqual(overview.slot_text(room), "D1（康熙十四年6月16日） 12:00-13:00 @花园")
        self.assertEqual(cal.example_time(), "6月1日")


if __name__ == "__main__":
    unittest.main()
