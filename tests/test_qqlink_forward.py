"""读合并转发：几种 OneBot 实现的返回格式都能认；收到的消息里认出「回复」和「合并转发」。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from changri.core.qqlink import forward_nodes, parse_group_message  # noqa: E402


class ForwardTests(unittest.TestCase):
    def test_napcat_style(self):
        data = {"messages": [{"sender": {"user_id": 555, "nickname": "阿甲", "card": "甲"}, "time": 100,
                              "message": [{"type": "text", "data": {"text": "甲：你好"}}, {"type": "image", "data": {}}]}]}
        self.assertEqual(forward_nodes(data), [{"ts": 100, "qq": "555", "nickname": "甲", "text": "甲：你好[图片]"}])

    def test_node_wrapped_and_content_style(self):
        data = [{"type": "node", "data": {"user_id": 666, "nickname": "阿乙", "time": 200, "content": "乙：嗯"}},
                {"type": "node", "data": {"uin": "777", "name": "丙", "time": 300, "content": [{"type": "forward", "data": {"id": "x"}}]}}]
        out = forward_nodes(data)
        self.assertEqual(out[0], {"ts": 200, "qq": "666", "nickname": "阿乙", "text": "乙：嗯"})
        self.assertEqual(out[1]["text"], "[合并转发]")                                # 套娃的转发不展开
        self.assertEqual(forward_nodes(None), [])

    def test_parse_reply_and_forward_ids(self):
        ev = {"post_type": "message", "message_type": "group", "group_id": 1, "user_id": 2, "message_id": 3, "time": 4,
              "message": [{"type": "reply", "data": {"id": "99"}}, {"type": "text", "data": {"text": "补录 R3"}}]}
        m = parse_group_message(ev)
        self.assertEqual((m.reply_id, m.text, m.forward_ids), ("99", "补录 R3", []))
        ev["message"] = [{"type": "forward", "data": {"id": "F7"}}]
        self.assertEqual(parse_group_message(ev).forward_ids, ["F7"])


if __name__ == "__main__":
    unittest.main()
