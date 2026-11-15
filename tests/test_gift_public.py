import asyncio, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "g.db"))
from changri.core import messaging, roles  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402


class GiftPublic(unittest.TestCase):
    def test_broadcast(self):
        init_db()
        with get_conn() as c:
            for t in ("messages", "role_groups", "roles"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B1", "P0", "M0"); roles.create_role("乙", "B1", "P1", "M1")
        a, b = roles.get_role_by_name("甲"), roles.get_role_by_name("乙")
        set_config("announce_group", "B1|ANN"); set_config("gift_public_send", "1"); set_config("gift_public_chance", "100")
        sent = []

        async def send(bot, grp, text):
            sent.append((grp, text)); return "id"
        mid = messaging.queue_message("gift", a["id"], b["id"], "玫瑰", "给你", "")
        self.assertTrue(asyncio.run(messaging.deliver(mid, send)))
        self.assertTrue(any(g == "ANN" and "玫瑰" in t and "乙" in t for g, t in sent))
        set_config("drop_hide_receiver", "1"); sent.clear()
        mid = messaging.queue_message("gift", a["id"], b["id"], "百合", "", "")
        asyncio.run(messaging.deliver(mid, send))
        self.assertTrue(any("某人" in t for g, t in sent if g == "ANN"))
        set_config("gift_public_send", "0"); sent.clear()
        mid = messaging.queue_message("gift", a["id"], b["id"], "兰花", "", "")
        asyncio.run(messaging.deliver(mid, send))
        self.assertFalse(any(g == "ANN" for g, t in sent))


if __name__ == "__main__":
    unittest.main()
