import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402

from core.qqlink import QQLink, QQLinkError, parse_group_message, segments_to_text  # noqa: E402


def gev(text="你好", group=111, user=222, **kw):
    return {"post_type": "message", "message_type": "group", "self_id": 999, "group_id": group, "user_id": user,
            "message_id": 7, "time": 1700000000, "sender": {"nickname": "小明", "card": ""},
            "message": [{"type": "text", "data": {"text": text}}], **kw}


class JoinTests(unittest.TestCase):
    def test_group_increase_notice_calls_hook(self):
        got = []
        link = QQLink("ws://x", on_group_join=lambda g, u: got.append((g, u)))
        asyncio.run(link._handle(json.dumps({"post_type": "notice", "notice_type": "group_increase", "group_id": 111, "user_id": 222, "self_id": 9})))
        asyncio.run(link._handle(json.dumps({"post_type": "notice", "notice_type": "group_decrease", "group_id": 111, "user_id": 222, "self_id": 9})))
        self.assertEqual(got, [("111", "222")])


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_group_text_with_at(self):
        sent = []

        async def handler(ws):
            async for raw in ws:
                req = json.loads(raw)
                sent.append(req)
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": req["echo"]}))

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        link = QQLink(f"ws://127.0.0.1:{port}")
        task = asyncio.get_running_loop().create_task(link.run())
        try:
            for _ in range(100):
                if link.connected:
                    break
                await asyncio.sleep(0.02)
            await link.send_group_text("111111", "人齐了", ["555", "666"])
        finally:
            link.stop()
            task.cancel()
            server.close()
            await server.wait_closed()
        p = sent[0]
        self.assertEqual((p["action"], p["params"]["group_id"]), ("send_group_msg", 111111))
        segs = p["params"]["message"]
        self.assertEqual([x["data"].get("qq") for x in segs if x["type"] == "at"], ["555", "666"])
        self.assertEqual(segs[-1]["data"]["text"], "人齐了")


class ParseTests(unittest.TestCase):
    def test_group_message(self):
        m = parse_group_message(gev())
        self.assertEqual((m.group_id, m.user_id, m.nickname, m.text, m.self_id), ("111", "222", "小明", "你好", "999"))

    def test_card_preferred_and_not_group(self):
        ev = gev()
        ev["sender"]["card"] = "群名片"
        self.assertEqual(parse_group_message(ev).nickname, "群名片")
        self.assertIsNone(parse_group_message({"post_type": "meta_event"}))
        self.assertIsNone(parse_group_message({**gev(), "message_type": "private"}))

    def test_segments(self):
        seg = [{"type": "reply", "data": {}}, {"type": "at", "data": {"qq": "1"}}, {"type": "text", "data": {"text": " 看"}},
               {"type": "image", "data": {}}, {"type": "face", "data": {}}]
        self.assertEqual(segments_to_text(seg), "@1 看[图片][表情]")
        self.assertEqual(segments_to_text("纯字符串"), "纯字符串")


class LinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent, self.client_ws = [], None

        async def handler(ws):
            self.client_ws = ws
            await ws.send(json.dumps(gev("第一条")))
            async for raw in ws:
                req = json.loads(raw)
                self.sent.append(req)
                if req["action"] == "set_group_name" and req["params"]["group_name"] == "不行":
                    await ws.send(json.dumps({"status": "failed", "retcode": 100, "wording": "没有权限", "echo": req["echo"]}))
                else:
                    await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": [{"group_id": 111}], "echo": req["echo"]}))

        self.server = await websockets.serve(handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.got = []
        self.link = QQLink(f"ws://127.0.0.1:{self.port}", on_group_message=self.got.append)
        self.task = asyncio.create_task(self.link.run())
        for _ in range(100):
            if self.got:
                break
            await asyncio.sleep(0.02)

    async def asyncTearDown(self):
        self.link.stop()
        self.task.cancel()
        self.server.close()
        await self.server.wait_closed()

    async def test_receive_and_self_id(self):
        self.assertTrue(self.link.connected)
        self.assertEqual((self.got[0].text, self.link.self_id), ("第一条", "999"))

    async def test_rename_and_errors(self):
        await self.link.set_group_name("111", "新群名")
        self.assertEqual(self.sent[0], {"action": "set_group_name", "params": {"group_id": 111, "group_name": "新群名"}, "echo": "1"})
        with self.assertRaises(QQLinkError) as cm:
            await self.link.set_group_name("111", "不行")
        self.assertIn("没有权限", str(cm.exception))
        with self.assertRaises(QQLinkError):
            await self.link.set_group_name("111", "  ")
        self.assertEqual((await self.link.group_list())[0]["group_id"], 111)

    async def test_only_allowed_actions(self):
        with self.assertRaises(QQLinkError):
            await self.link.call("set_group_kick", {"group_id": 1, "user_id": 2})
        self.assertEqual(self.sent, [])

    async def test_reconnect(self):
        await self.client_ws.close()
        for _ in range(200):
            if len(self.got) >= 2:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(len(self.got), 2)               # 断开后自动重连，又收到一条


if __name__ == "__main__":
    unittest.main()
