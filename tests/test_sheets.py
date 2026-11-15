import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "sheets.db")

from PIL import Image  # noqa: E402

from changri.core import roles, sheets  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


def png(w=40, h=30, color=(200, 30, 30)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


class Sheets(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("sheet_images", "sheets", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B", "G1", "M1")
        self.a = roles.get_role_by_name("甲")["id"]

    def test_submit_overwrite_and_files(self):
        ok, info = sheets.submit(self.a, "gongzhi", "第一版", [png(), png(3000, 100)])
        self.assertTrue(ok, info)
        first = sheets.get(self.a, "gongzhi")
        self.assertEqual((first["text"], len(first["images"])), ("第一版", 2))
        for n in first["images"]:
            self.assertTrue((sheets.folder() / n).is_file())
        big = Image.open(io.BytesIO(sheets.image_bytes(first["images"][1])[0]))
        self.assertLessEqual(max(big.size), sheets.SIDE_MAX)               # 大图缩到 2000 以内
        ok, _ = sheets.submit(self.a, "gongzhi", "第二版", [])              # 重复提交 = 整份覆盖，旧图片文件删掉
        second = sheets.get(self.a, "gongzhi")
        self.assertEqual((second["text"], second["images"]), ("第二版", []))
        for n in first["images"]:
            self.assertFalse((sheets.folder() / n).exists())

    def test_rejects_bad_input_and_path_tricks(self):
        self.assertFalse(sheets.submit(self.a, "gongzhi", "", [b"not an image"])[0])    # 不是图片、也没文字
        ok, info = sheets.submit(self.a, "erbiao", "文字", [b"junk"])
        self.assertTrue(ok); self.assertIn("没能打开", info)                          # 图片坏了，文字照存
        self.assertFalse(sheets.submit(self.a, "别的", "x")[0])
        for bad in ("../x.jpg", "1_gongzhi_0_123.jpg/../../etc", "a.jpg", ""):
            self.assertIsNone(sheets.image_bytes(bad))
        self.assertEqual(len(sheets.get(self.a, "erbiao")["images"]), 0)

    def test_overview_public_flag_and_role_delete_cleans_files(self):
        sheets.submit(self.a, "gongzhi", "x", [png()])
        o = sheets.overview()[0]
        self.assertTrue(o["gongzhi"]); self.assertFalse(o["erbiao"])
        self.assertFalse(sheets.public())
        set_config("gongzhi_public", "1")
        self.assertTrue(sheets.public())
        name = sheets.get(self.a, "gongzhi")["images"][0]
        roles.delete_role(self.a)
        self.assertFalse((sheets.folder() / name).exists())


if __name__ == "__main__":
    unittest.main()
