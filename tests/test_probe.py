import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import probe_service as p  # noqa: E402


class Probe(unittest.TestCase):
    def test_rejects(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "abc", "::1"):
            self.assertEqual(p.check(ip, 5092), "bad_ip", ip)
        self.assertEqual(p.check("8.8.8.8", 80), "bad_port")
        self.assertEqual(p.check("8.8.8.8", 70000), "bad_port")

    def test_rate(self):
        for i in range(6):
            self.assertTrue(p.rate_ok("9.9.9.9", 100 + i))
        self.assertFalse(p.rate_ok("9.9.9.9", 106))
        self.assertTrue(p.rate_ok("9.9.9.9", 200))


if __name__ == "__main__":
    unittest.main()
