import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "img.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from changri.core import imgload, moments, precall, profiles, sheets  # noqa: E402


def enc(w, h, fmt):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(b, fmt)
    return b.getvalue()


class ImgLoadTests(unittest.TestCase):
    """玩家上传的图：先看像素数再解码。几百 KB 的超大 PNG 不能解出几百 MB；手机拍的大 JPEG 照样能用。"""

    def test_huge_png_rejected_before_decoding(self):
        big = enc(8900, 8900, "PNG")                 # 三百多 KB，解出来要几百 MB
        self.assertLess(len(big), 2 * 1024 * 1024)
        with self.assertRaises(ValueError) as cm:
            imgload.open_upload(big)
        self.assertIn("像素太多", str(cm.exception))
        for fn in (profiles.process_avatar, moments.process_image, precall.process_photo):
            self.assertRaises(ValueError, fn, big)
        self.assertIsNone(sheets.normalize_image(big))

    def test_big_phone_jpeg_still_works(self):
        jpg = enc(6000, 8000, "JPEG")               # 4800 万像素的手机照片
        img = imgload.open_upload(jpg, (1600, 1600))
        self.assertLess(img.size[0] * img.size[1], imgload.MAX_PIXELS)   # 缩着解的
        self.assertEqual(moments.process_image(jpg)[2:], (1200, 1600))
        self.assertTrue(profiles.process_avatar(jpg))
        self.assertTrue(precall.process_photo(enc(12000, 16000, "JPEG")))   # 2 亿像素也行

    def test_not_an_image(self):
        with self.assertRaises(ValueError) as cm:
            imgload.open_upload(b"not an image")
        self.assertIn("不是能识别的图片", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
