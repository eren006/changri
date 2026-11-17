import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from changri.features import registry  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_lists_are_collected_in_order(self):
        core = [f["id"] for f in registry.core_cards()]
        self.assertEqual(core, ["base", "season", "social", "gift", "play"])
        off = registry.official()
        self.assertEqual([f["order"] for f in off], sorted(f["order"] for f in off))
        self.assertEqual(off[0]["id"], "entrance")
        self.assertIn("precall", [f["id"] for f in off])

    def test_command_modules_exist(self):
        for f in registry.official():
            if f.get("module"):
                self.assertIsNotNone(importlib.util.find_spec(f["module"]), f["module"])

    def test_url_guards(self):
        g = dict(registry.url_guards())
        self.assertEqual(g["/sheets"], "relations")
        self.assertEqual(g["/sightings"], "sighting")
        self.assertEqual(g["/moments"], "moments")
        self.assertEqual(len(g), 16)          # 原来 guard() 里手写的 15 条 + 故事历

    def test_pages_have_register(self):
        admin, phone = registry.admin_pages(), registry.phone_pages()
        self.assertIn("changri.features.play.admin", admin)
        self.assertIn("changri.features.play.phone", phone)
        for mod in admin + phone:
            src = Path(importlib.util.find_spec(mod).origin).read_text(encoding="utf-8")
            self.assertIn("\ndef register(app, ", src, mod)


if __name__ == "__main__":
    unittest.main()
