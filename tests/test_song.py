"""点歌：参数、被回复卡片的解析（用 2026-11-15 贾维斯上抓到的真实事件）、搜歌挑选、次数、公告群内容。不联网。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("CHANGRI_DB_PATH", str(Path(tempfile.mkdtemp()) / "song.db"))

from changri.core import roles, song  # noqa: E402
from changri.core.db import get_conn, init_db  # noqa: E402

NETEASE_CARD = ("[卡片消息] 图文H5\n摘要: [分享]L O V E (《天生一对》主题曲)\ntag: 网易云音乐\ntitle: L O V E (《天生一对》主题曲)\n"
                "desc: Nat King Cole\njump_url: https://y.music.163.com/m/song?id=5055675&fx-wxqd=&playerUIModeId=76001&dlt=0846")
QQ_CARD = ("[卡片消息] 图文H5\n摘要: [分享]蜃楼\ntag: QQ音乐\ntitle: 蜃楼\ndesc: 周深\n"
           "jump_url: https://i.y.qq.com/v8/playsong.html?hosteuin=x&songid=480580903&songmid=&type=0&media_mid=001S9bqW2A1erO")


class Args(unittest.TestCase):
    def test_search_style(self):
        self.assertEqual(song.parse_args("QQ 晴天 周杰伦 给 张三 生日快乐"),
                         {"platform": "qq", "keyword": "晴天 周杰伦", "to": "张三", "message": "生日快乐", "signer": ""})
        a = song.parse_args("网易 起风了 给大家 今天也要开心")
        self.assertEqual((a["platform"], a["keyword"], a["to"], a["message"]), ("163", "起风了", "", "今天也要开心"))
        a = song.parse_args("晴天 给张三")
        self.assertEqual((a["keyword"], a["to"]), ("晴天", "张三"))
        self.assertEqual(song.parse_args("给 张三 生日快乐")["keyword"], "")        # 回复卡片时只写给谁

    def test_old_style(self):
        a = song.parse_args("点歌人：小林 留言：这首歌送给你 歌名：晴天 送给：张三")
        self.assertEqual((a["signer"], a["message"], a["keyword"], a["to"]), ("小林", "这首歌送给你", "晴天", "张三"))


class Card(unittest.TestCase):
    def test_netease(self):
        s = song.parse_card_text(NETEASE_CARD)
        self.assertEqual((s["platform"], s["id"], s["title"], s["artist"]), ("163", "5055675", "L O V E (《天生一对》主题曲)", "Nat King Cole"))

    def test_qq(self):
        s = song.parse_card_text(QQ_CARD)
        self.assertEqual((s["platform"], s["id"], s["mid"], s["title"], s["artist"]), ("qq", "480580903", "", "蜃楼", "周深"))

    def test_not_music(self):
        self.assertIsNone(song.parse_card_text("今天天气不错"))
        self.assertIsNone(song.parse_card_text("[卡片消息]\ntitle: 某个小程序\njump_url: http://x.com/a"))

    def test_from_event(self):
        ev = SimpleNamespace(msg_elements=[SimpleNamespace(content=QQ_CARD, ark_data=None)])
        self.assertEqual(song.card_from_event(ev)["title"], "蜃楼")
        ev = SimpleNamespace(msg_elements=[{"ark_data": {"fields": {"tag": "网易云音乐", "title": "L O V E", "desc": "Nat King Cole",
                                                                     "jump_url": "https://y.music.163.com/m/song?id=5055675"}}}])
        self.assertEqual(song.card_from_event(ev)["id"], "5055675")
        self.assertIsNone(song.card_from_event(SimpleNamespace(msg_elements=None)))

    def test_lookup_offline_keeps_card(self):
        s = song.parse_card_text(NETEASE_CARD)
        with mock.patch.object(song, "_netease_detail", side_effect=OSError("断网")):
            out = song.lookup(s)
        self.assertEqual(out["url"], "https://music.163.com/song?id=5055675")     # 链接规整成干净的歌曲页
        self.assertEqual(out["title"], s["title"])

    def test_lookup_qq_fills_mid_and_cover(self):
        d = {"platform": "qq", "id": "480580903", "mid": "00103MrP45XbHm", "title": "蜃楼", "artist": "周深",
             "cover": "https://y.gtimg.cn/x.jpg"}
        with mock.patch.object(song, "_qq_detail", return_value=d):
            out = song.lookup(song.parse_card_text(QQ_CARD))
        self.assertEqual((out["url"], out["cover"]), ("https://y.qq.com/n/ryqq/songDetail/00103MrP45XbHm", "https://y.gtimg.cn/x.jpg"))


class Bili(unittest.TestCase):
    def test_find(self):
        share = "【【官方 MV】Never Gonna Give You Up - Rick Astley-哔哩哔哩】 https://b23.tv/AbCd123 给 张三 生日快乐"
        self.assertEqual(song.find_bili(share), ("https://b23.tv/AbCd123", "给 张三 生日快乐"))
        self.assertEqual(song.find_bili("BV1GJ411x7h7 给大家"), ("BV1GJ411x7h7", "给大家"))
        self.assertEqual(song.find_bili("https://www.bilibili.com/video/BV1GJ411x7h7?p=1")[0][:43], "https://www.bilibili.com/video/BV1GJ411x7h7")
        self.assertEqual(song.find_bili("晴天 周杰伦"), ("", "晴天 周杰伦"))
        self.assertEqual(song.parse_args(song.find_bili(share)[1])["to"], "张三")

    def test_lookup(self):
        body = b'{"code":0,"data":{"bvid":"BV1GJ411x7h7","title":"Never Gonna Give You Up","owner":{"name":"UP"},"pic":"http://i1.hdslb.com/x.jpg"}}'
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(song, "_bili_headers", return_value={}), \
             mock.patch.object(song, "_resolve_short", return_value="https://www.bilibili.com/video/BV1GJ411x7h7/"), \
             mock.patch("urllib.request.urlopen", return_value=resp):
            s = song.bili_lookup("https://b23.tv/AbCd123")
        self.assertEqual((s["platform"], s["title"], s["artist"]), ("bili", "Never Gonna Give You Up", "UP"))
        self.assertEqual((s["url"], s["cover"]), ("https://www.bilibili.com/video/BV1GJ411x7h7", "https://i1.hdslb.com/x.jpg"))
        with mock.patch.object(song, "_resolve_short", return_value="https://space.bilibili.com/123"):
            self.assertIsNone(song.bili_lookup("https://b23.tv/x"))            # 不是视频
        with mock.patch.object(song, "_resolve_short", side_effect=OSError):
            with self.assertRaises(RuntimeError):
                song.bili_lookup("https://b23.tv/x")


def _s(title, artist, pf="163", i="1"):
    return {"platform": pf, "id": i, "mid": "m" + i, "title": title, "artist": artist, "cover": ""}


class Search(unittest.TestCase):
    def test_best_match(self):
        songs = [_s("晴天 (原唱 周杰伦)", "RyaVocal"), _s("布拉格广场", "蔡依林 / 周杰伦"), _s("晴天", "梦里啥都有")]
        self.assertEqual(song.best_match("晴天", songs)["artist"], "梦里啥都有")        # 翻唱扣分
        self.assertIsNone(song.best_match("晴天 周杰伦", [_s("布拉格广场", "蔡依林 / 周杰伦")]))   # 歌名对不上的不要
        self.assertEqual(song.best_match("love", [_s("L O V E (《天生一对》主题曲)", "Nat King Cole")])["artist"], "Nat King Cole")

    def test_platform_order_and_artist(self):
        qq = [_s("晴天", "某翻唱", "qq", "1")]
        ne = [_s("晴天", "周杰伦", "163", "2")]
        with mock.patch.object(song, "_qq_search", return_value=qq), mock.patch.object(song, "_netease_search", return_value=ne):
            self.assertEqual(song.search("晴天")["platform"], "qq")                    # 只写歌名：先 QQ
            self.assertEqual(song.search("晴天 周杰伦")["artist"], "周杰伦")            # 写了歌手 QQ 没对上：去网易云
            self.assertEqual(song.search("晴天", "163")["platform"], "163")

    def test_all_down(self):
        with mock.patch.object(song, "_qq_search", side_effect=OSError), mock.patch.object(song, "_netease_search", side_effect=OSError):
            with self.assertRaises(RuntimeError):
                song.search("晴天")
        with mock.patch.object(song, "_qq_search", return_value=[]), mock.patch.object(song, "_netease_search", return_value=[]):
            self.assertIsNone(song.search("不存在的歌"))


class RecordAndBody(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_conn() as c:
            for t in ("song_requests", "role_groups", "roles", "config"):
                c.execute(f"DELETE FROM {t}")
        roles.create_role("甲", "B", "G0", "M0")
        self.me = roles.get_role_by_name("甲")

    def test_daily_limit(self):
        s = {"platform": "163", "id": "1", "title": "晴天", "artist": "周杰伦", "url": "https://music.163.com/song?id=1"}
        for _ in range(3):
            self.assertEqual(song.limit_reached(self.me["id"]), "")
            rid = song.record(self.me["id"], {"to": ""}, s, "search")
        self.assertIn("用完了", song.limit_reached(self.me["id"]))
        song.delete(rid)                                                   # 没发出去的不算
        self.assertEqual(song.limit_reached(self.me["id"]), "")

    def test_bodies(self):
        s = {"platform": "qq", "title": "蜃楼", "artist": "周深", "url": "https://y.qq.com/n/ryqq/songDetail/x", "cover": "https://y.gtimg.cn/c.jpg"}
        card = song.card_body({"to": "张三", "message": "生日快乐"}, s)
        self.assertEqual(card["content"]["title"], "🎵 蜃楼 - 周深")
        self.assertEqual(card["content"]["description"], "有人点给 张三：生日快乐")
        self.assertIsNone(song.card_body({}, dict(s, cover="")))                # 没封面发不了图文卡片
        self.assertEqual(song.headline({"signer": "小林"}), "小林点给大家")
        self.assertIn("🔗 https://y.qq.com", song.text_body({}, s))
        self.assertIn("去QQ音乐搜一搜", song.text_body({}, s, with_link=False))


if __name__ == "__main__":
    unittest.main()
