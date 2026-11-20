"""changelog 的 internal（内部改动）：只给作者看，打包时去掉，不进 latest.json、不在用户的「更新」页出现（作者 2026-11-20）。"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _build():
    sp = importlib.util.spec_from_file_location("build_for_test", ROOT / "packaging" / "build.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


class ChangelogInternal(unittest.TestCase):
    def test_internal_stripped_for_users(self):
        b = _build()
        rows = [{"version": "0.9.1", "date": "", "features": ["新外观"], "fixes": ["修 a"], "internal": ["上报改成每小时"]},
                {"version": "0.9.0", "date": "2026-01-01", "features": [], "fixes": ["修 b"]}]
        out = b.public_changes(rows)
        self.assertEqual(out[0], {"version": "0.9.1", "date": "", "features": ["新外观"], "fixes": ["修 a"]})
        self.assertEqual(out[1], rows[1])
        self.assertIn("internal", rows[0])                      # 不改原来的
        self.assertNotIn("上报", b.notes_from(rows[0]))           # 老版本看的纯文字说明也不带

    def test_real_changelog_shape(self):
        """仓库里的 changelog：每条都有 version；internal 是列表；features / fixes 里不写维护性质的上报改动。"""
        rows = json.loads((ROOT / "packaging" / "changelog.json").read_text(encoding="utf-8"))
        for r in rows:
            self.assertTrue(r.get("version"))
            self.assertIsInstance(r.get("internal", []), list)
        top = rows[0]
        self.assertFalse(any("匿名使用情况上报" in x for x in top.get("features", []) + top.get("fixes", [])))


class MaintenanceCheck(unittest.TestCase):
    def test_release_flags_maintenance_lines(self):
        """release.py 发版前：features / fixes 里像维护改动的列出来提醒（不拦）。"""
        import sys
        sp = importlib.util.spec_from_file_location("release_tool_ci", ROOT / "tools" / "release.py")
        r = importlib.util.module_from_spec(sp)
        sys.modules["release_tool_ci"] = r
        sp.loader.exec_module(r)
        e = {"features": ["新外观「新春」", "匿名使用情况上报改成每小时"], "fixes": ["报错日志不再记断线", "「知道了」点不了"],
             "internal": ["重构 play.py"]}
        self.assertEqual(r.maintenance_suspects(e), ["匿名使用情况上报改成每小时", "报错日志不再记断线"])
        self.assertEqual(r.maintenance_suspects(None), [])


if __name__ == "__main__":
    unittest.main()
