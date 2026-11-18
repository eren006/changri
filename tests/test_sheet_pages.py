import asyncio
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "sp.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")
import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter  # noqa: E402

nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
nonebot.get_driver().register_adapter(Adapter)
from PIL import Image  # noqa: E402

from changri.core.db import init_db  # noqa: E402

init_db()
R = nonebot.load_plugin("changri.plugins.relations").module


def png(h: int, w: int = 760) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 200, 200)).save(b, "PNG")
    return b.getvalue()


class FakeMatcher:
    def __init__(self):
        self.sent = []

    async def send(self, m):
        self.sent.append(m)


class SheetPagesTests(unittest.TestCase):
    """「查看公知」看全员：按页发（一页 4 张图），后面还有就提示「第2页」，谁都不省略；
    不再把所有人拼成一整张大图（20 人填满时内存涨 1.5 GB）。"""

    def people(self, n, prefix="P"):
        return [{"name": f"{prefix}{i}", "text": "x", "images": [], "updated_ts": 0} for i in range(n)]

    def test_short_sheets_share_images(self):
        with mock.patch.object(R, "_render_one", side_effect=lambda sh, kind: png(1000)):
            parts, more = R._pages(self.people(9), "gongzhi", 0, 10)
        self.assertEqual((len(parts), more), (3, False))            # 每张 4 人：4 + 4 + 1
        self.assertTrue(all(Image.open(io.BytesIO(p)).height <= R.PAGE_STEP + R.PAGE_SLACK for p in parts))

    def test_paging_covers_everyone_and_stops_early(self):
        drawn = []

        def render(sh, kind):
            drawn.append(sh["name"])
            return png(4000)                                        # 一人一张
        with mock.patch.object(R, "_render_one", side_effect=render):
            p1, more1 = R._pages(self.people(10), "gongzhi", 0, 4)
            self.assertEqual((len(p1), more1), (4, True))
            self.assertEqual(drawn, [f"P{i}" for i in range(5)])   # 只多画了一个确认后面还有，没把 10 个人都画了
            drawn.clear()
            p3, more3 = R._pages(self.people(10), "gongzhi", 8, 4)
        self.assertEqual((len(p3), more3), (2, False))              # 第 3 页：P8、P9，没有省略

    def test_tall_sheet_continues_on_next_page(self):
        with mock.patch.object(R, "_render_one", return_value=png(4200 * 6)):
            a, more_a = R._pages(self.people(1), "gongzhi", 0, 4)
            b, more_b = R._pages(self.people(1), "gongzhi", 4, 4)
        self.assertEqual((len(a), more_a, len(b), more_b), (4, True, 2, False))   # 一个人的长表也不截掉，翻页接着看

    def test_show_pages_with_hint(self):
        where = []

        def render(sh, kind):
            where.append(threading.current_thread() is threading.main_thread())
            return png(4000)
        with mock.patch.object(R, "_render_one", side_effect=render):
            m = FakeMatcher()
            asyncio.run(R._show(m, self.people(6), "gongzhi", 1, "查看公知"))
            self.assertEqual(len(m.sent), 5)
            self.assertIn("发「查看公知 第2页」", m.sent[-1])
            m2 = FakeMatcher()
            asyncio.run(R._show(m2, self.people(6), "gongzhi", 2, "查看公知"))
            self.assertEqual(len(m2.sent), 2)                         # P4、P5，没有提示
            self.assertFalse(any(isinstance(x, str) for x in m2.sent))
            m3 = FakeMatcher()
            asyncio.run(R._show(m3, self.people(6), "gongzhi", 9, "查看公知"))
            self.assertIn("没有第 9 页", m3.sent[0])
        self.assertNotIn(True, where)                               # 画图不在主线程（事件循环）里


if __name__ == "__main__":
    unittest.main()
