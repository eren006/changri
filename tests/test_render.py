import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "r.db"))
from changri.core.db import init_db, set_config  # noqa: E402
from changri.core.render import forward_image as F  # noqa: E402


class RenderTests(unittest.TestCase):
    def test_bundled_fonts_exist_and_draw_names(self):
        fonts = Path(__file__).resolve().parent.parent / "src" / "changri" / "assets" / "fonts"
        for n in ("SansSC-Regular.otf", "SansSC-Bold.otf", "SerifSC-Regular.otf"):
            self.assertTrue((fonts / n).exists(), n)
        pngs, cut = F.render_forward([{"name": "长日将尽", "text": "【关系线】\n曦玥 ↔ 昱珩\n1. 初见：雨里借了一把伞"}])
        self.assertTrue(pngs and pngs[0].startswith(b"\x89PNG"))
        self.assertFalse(cut)

    def test_very_long_text_is_paginated_not_dropped_silently(self):
        text = "\n".join(f"第 {i} 行：" + "很长的一段话" * 6 for i in range(400))
        pngs, cut = F.render_forward([{"name": "长日将尽", "text": text}])
        self.assertGreater(len(pngs), 1)
        self.assertLessEqual(len(pngs), F.MAX_PAGES)


class LongReplyTests(unittest.TestCase):
    def setUp(self):
        init_db()
        set_config("long_as_image", "1"); set_config("image_min_chars", "50")
        from changri.core import qqsend as _long
        self.long = _long

    class Matcher:
        def __init__(self): self.sent = []
        async def send(self, m): self.sent.append(m)

    def run_(self, coro): return asyncio.new_event_loop().run_until_complete(coro)

    def test_short_goes_as_text(self):
        m = self.Matcher(); self.run_(self.long.reply_long(m, "短短一句"))
        self.assertEqual(m.sent, ["短短一句"])

    def test_long_goes_as_image(self):
        m = self.Matcher(); self.run_(self.long.reply_long(m, "很长的内容。" * 30))
        self.assertTrue(m.sent and all(not isinstance(x, str) for x in m.sent))

    def test_render_failure_falls_back_to_text(self):
        m = self.Matcher()
        with mock.patch.object(self.long, "_render", side_effect=RuntimeError("no font")):
            self.run_(self.long.reply_long(m, "很长的内容。" * 30))
        self.assertTrue(m.sent and all(isinstance(x, str) for x in m.sent))

    def test_switch_off_means_text(self):
        set_config("long_as_image", "0")
        m = self.Matcher(); self.run_(self.long.reply_long(m, "很长的内容。" * 30))
        self.assertTrue(all(isinstance(x, str) for x in m.sent))


if __name__ == "__main__":
    unittest.main()
