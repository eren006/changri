"""静态检查：用了却没导入的名字（这类错误只有走到那一行才会报，平时的测试不一定覆盖到）。"""
import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


class StaticTests(unittest.TestCase):
    def test_no_undefined_names(self):
        try:
            import pyflakes  # noqa: F401
        except ImportError:
            self.skipTest("没装 pyflakes（pip install pyflakes）")
        r = subprocess.run([sys.executable, "-m", "pyflakes", str(SRC)], capture_output=True, text=True)
        bad = [x for x in r.stdout.splitlines() if "undefined name" in x]
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
