"""changri_api 版本 2 新加的接口（戏群、物品、地点、关系线、扩展自己存数据、每日次数），
以及 extdocs/现成插件/ 里的五个插件：按用户扩展的方式装上、打开，走真正的 NoneBot 事件分发跑一遍。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ.setdefault("CHANGRI_DB_PATH", str(_tmp / "api.db"))
os.environ["CHANGRI_EXT_DIR"] = str(_tmp / "extensions")
os.environ["CHANGRI_HOME"] = str(_tmp / "home")
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent, MessageSegment  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

from changri.core import entrance, roles, rpg  # noqa: E402
from changri.core import extensions as E  # noqa: E402
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

READY = ["daily_signin", "daily_fortune", "group_broadcast", "gold_rank", "soda_shake"]

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="WARNING")
    nonebot.get_driver().register_adapter(Adapter)
    init_db()
    for _id in READY:                                   # 和设置平台点「装上」+ 打开开关一样
        ok, msg = E.install_ready(_id)
        assert ok, msg
        E.set_enabled(_id, True)
    _cwd = os.getcwd()
    sys.path.insert(0, str(_tmp)); os.chdir(_tmp)                                      # 用户扩展按路径加载，要在当前目录下面（正式运行时当前目录就是程序目录）
    LOADED = E.load_all()
    os.chdir(_cwd)

from nonebot.message import handle_event  # noqa: E402

import changri_api as api  # noqa: E402

SENT: list = []
PUSHED: list = []


def _text(m) -> str:
    if isinstance(m, str):
        return m
    segs = [m] if isinstance(m, MessageSegment) else list(m)
    return "".join(sg.data.get("text", "<图>") if sg.type == "text" else "<图>" for sg in segs)


class FakeBot(QBot):
    async def send(self, event, message, **kw):
        SENT.append(_text(message))

    async def send_to_group(self, group_openid, message, **kw):
        PUSHED.append((group_openid, _text(message)))
        from types import SimpleNamespace
        return SimpleNamespace(id=f"P{len(PUSHED)}")


def _reset():
    init_db()
    with get_conn() as c:
        for t in ("rpg_inventory", "rpg_place_keys", "rpg_places", "roles"):
            c.execute(f"DELETE FROM {t}")
    import shutil
    shutil.rmtree(_tmp / "home" / "ext_data", ignore_errors=True)
    rpg.seed_defaults(force=True)


class ApiV2Tests(unittest.TestCase):
    def setUp(self):
        _reset()
        roles.create_role("甲", "B1", "G1", "M1")
        roles.create_role("乙", "B1", "G2", "M2")
        self.a = roles.get_role_by_name("甲")["id"]
        self.b = roles.get_role_by_name("乙")["id"]

    def test_version(self):
        self.assertEqual(api.API_VERSION, 4)

    def test_role_by_id(self):
        self.assertEqual(api.role_by_id(self.a)["name"], "甲")
        self.assertIsNone(api.role_by_id(99999))

    def test_stage_group(self):
        entrance.set_stage("", "")
        self.assertIsNone(api.stage_group())
        entrance.set_stage("B1", "GSTAGE")
        self.assertEqual(api.stage_group(), ("B1", "GSTAGE"))

    def test_items_and_gold(self):
        self.assertEqual(api.item_info("金币")["kind"], "currency")
        self.assertIsNone(api.item_info("不存在的东西"))
        self.assertIn("金币", [it["name"] for it in api.all_items()])
        self.assertEqual(api.item_count(self.a, "金币"), 0)
        self.assertEqual(api.give_item(self.a, "金币", 10, "测试"), (True, ""))
        self.assertEqual(api.give_item(self.a, "GOLD", 5), (True, ""))                  # 物品码也认
        self.assertEqual(api.item_count(self.a, "金币"), 15)
        self.assertEqual(api.inventory(self.a), [{"code": "GOLD", "name": "金币", "kind": "currency", "count": 15}])
        ok, msg = api.take_item(self.a, "金币", 20)
        self.assertFalse(ok); self.assertIn("只有 15", msg)
        self.assertEqual(api.item_count(self.a, "金币"), 15)                              # 不够扣就一个都不扣
        self.assertEqual(api.take_item(self.a, "金币", 15), (True, ""))
        self.assertEqual(api.item_count(self.a, "金币"), 0)
        self.assertEqual(api.give_item(self.a, "没有的", 1), (False, "找不到这个物品。"))
        for bad in (0, -3, "abc", 10000):
            self.assertFalse(api.give_item(self.a, "金币", bad)[0], bad)
            self.assertFalse(api.take_item(self.a, "金币", bad)[0], bad)

    def test_places(self):
        rpg.save_place("天台", "风很大", locked=True)
        rpg.save_place("花园")
        names = {p["name"]: p for p in api.all_places()}
        self.assertTrue(names["天台"]["locked"]); self.assertFalse(names["花园"]["locked"])
        self.assertEqual(api.place_keys(self.a), [])
        rpg.set_key(self.a, names["天台"]["id"], True)
        self.assertEqual(api.place_keys(self.a), ["天台"])
        set_config("place_system_enabled", "1")
        self.assertTrue(api.can_use_place(self.a, "天台")[0])
        ok, why = api.can_use_place(self.b, "天台")
        self.assertFalse(ok); self.assertIn("钥匙", why)
        set_config("place_system_enabled", "0")

    def test_relations(self):
        self.assertEqual(api.relations_of(self.a), [])
        from changri.core import relations
        relations.start(self.a, self.b, "乙")
        rels = api.relations_of(self.a)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["other_name"], "乙")
        self.assertEqual(set(rels[0]), {"other_id", "other_name", "confirmed", "count", "chars"})

    def test_data_store(self):
        self.assertIsNone(api.data_get("my_ext", "k"))
        self.assertEqual(api.data_get("my_ext", "k", 3), 3)
        api.data_set("my_ext", "k", {"a": [1, 2]})
        api.data_set("my_ext", "k2", "文字")
        self.assertEqual(api.data_get("my_ext", "k"), {"a": [1, 2]})
        self.assertEqual(api.data_keys("my_ext"), ["k", "k2"])
        self.assertEqual(api.data_keys("my_ext", "k2"), ["k2"])
        api.data_set("my_ext", "k", None)
        self.assertEqual(api.data_keys("my_ext"), ["k2"])
        self.assertIsNone(api.data_get("other_ext", "k2"))                              # 各扩展分开存
        self.assertTrue((_tmp / "home" / "ext_data" / "my_ext.json").is_file())
        with self.assertRaises(ValueError):
            api.data_get("../evil", "k")                                                 # 扩展 id 不对直接拒绝，不会写到别处
        with self.assertRaises(TypeError):
            api.data_set("my_ext", "bad", object())
        self.assertEqual(api.data_get("my_ext", "k2"), "文字")                           # 存失败不会写坏文件

    def test_today_counter(self):
        self.assertEqual(api.today_count("my_ext", "签到", self.a), 0)
        self.assertEqual(api.today_add("my_ext", "签到", self.a), 1)
        self.assertEqual(api.today_add("my_ext", "签到", self.a, 2), 3)
        self.assertEqual(api.today_count("my_ext", "签到", self.b), 0)
        from unittest import mock
        with mock.patch("changri.core.season.day_key", return_value="另一天"):          # 换天归零
            self.assertEqual(api.today_count("my_ext", "签到", self.a), 0)
            self.assertEqual(api.today_add("my_ext", "签到", self.a), 1)

    def test_season_data_cleared_on_wrap_up(self):
        """本季数据（season_data_*）和一直留着的数据分开存；收尾（season.wipe）只清本季的。"""
        from changri.core import season
        api.season_data_set("my_ext", f"连签_{self.a}", 4)
        api.data_set("my_ext", "累计", 9)
        self.assertEqual(api.season_data_get("my_ext", f"连签_{self.a}"), 4)
        self.assertEqual(api.season_data_keys("my_ext"), [f"连签_{self.a}"])
        self.assertNotIn(f"连签_{self.a}", api.data_keys("my_ext"))                    # 两种分开
        season.wipe()
        self.assertIsNone(api.season_data_get("my_ext", f"连签_{self.a}"))
        self.assertEqual(api.season_data_keys("my_ext"), [])
        self.assertEqual(api.data_get("my_ext", "累计"), 9)                             # 一直留着的不清
        api.data_set("my_ext", "累计", None)
        with self.assertRaises(ValueError):
            api.season_data_get("../evil", "k")

    def test_player_data_long_term(self):
        """按玩家本人存（版本 4）：单独一个文件，收尾不清，老插件按角色清理也不碰它。"""
        from changri.core import extdata, season
        api.player_data_set("my_ext", 7, "总天数", 3)
        api.player_data_set("my_ext", 8, "总天数", 5)
        api.player_data_set("my_ext", 8, "最高分", 99)
        api.player_data_set("my_ext", str(self.a), str(self.a), "看起来像角色编号的键")
        self.assertEqual(api.player_data_get("my_ext", 7, "总天数"), 3)
        self.assertEqual(api.player_data_get("my_ext", 9, "总天数", 0), 0)
        self.assertEqual(api.player_data_all("my_ext", "总天数"), {7: 3, 8: 5})
        self.assertIsNone(api.data_get("my_ext", "总天数"))                             # 跟 data_set 的分开
        self.assertTrue((extdata.folder() / "my_ext.player.json").is_file())
        season.wipe()
        self.assertEqual(api.player_data_all("my_ext", "总天数"), {7: 3, 8: 5})          # 收尾不清
        self.assertEqual(api.player_data_get("my_ext", self.a, str(self.a)), "看起来像角色编号的键")
        api.player_data_set("my_ext", 7, "总天数", None)
        self.assertEqual(api.player_data_all("my_ext", "总天数"), {8: 5})
        for pk, k in ((8, "总天数"), (8, "最高分"), (self.a, str(self.a))):
            api.player_data_set("my_ext", pk, k, None)
        self.assertEqual(api.player_data_all("my_ext", "最高分"), {})
        with self.assertRaises(TypeError):
            api.player_data_set("my_ext", 1, "bad", object())

    def test_each_team_runs_once_per_bot(self):
        """定时任务加 @each_team：每个启用的机器人各跑一遍，里面读到的是那个机器人的；同步函数也行，一个出错不影响别的。"""
        from unittest import mock
        seen = []

        @api.each_team
        def job():
            from changri.core import db
            seen.append(db.current_bot())
            if db.current_bot() == "222":
                raise RuntimeError("坏了")

        with mock.patch("changri.core.db.list_bot_ids", return_value=["111", "222", "333"]):
            asyncio.run(job())
        self.assertEqual(seen, ["111", "222", "333"])

    def test_wrap_up_clears_old_role_keys(self):
        """老插件按角色存进 data_set 的记录：收尾时清掉，先备份；不带角色的、下划线开头的不动。"""
        import json as _json
        from changri.core import extdata, season
        api.data_set("old_ext", f"streak_{self.a}", 5)
        api.data_set("old_ext", str(self.b), {"x": 1})
        api.data_set("old_ext", "连签:乙", 2)
        api.data_set("old_ext", "累计", 9)
        api.data_set("old_ext", "奖池_999999", 1)                                     # 不是这季角色的编号：不动
        api.today_add("old_ext", "签到", self.a)
        season.wipe()
        self.assertEqual(api.data_keys("old_ext"), ["_today:签到", "奖池_999999", "累计"])
        backup = extdata.folder() / ("old_ext" + extdata.BACKUP_SUFFIX)
        self.assertEqual(set(_json.loads(backup.read_text(encoding="utf-8"))), {f"streak_{self.a}", str(self.b), "连签:乙"})
        for k in api.data_keys("old_ext"):
            api.data_set("old_ext", k, None)
        backup.unlink()


class ApiReferenceTests(unittest.TestCase):
    def test_every_public_function_listed_with_doc_and_example(self):
        ref = E.api_reference()
        listed = {a["name"] for s in ref for a in s["entries"]}
        import inspect
        public = {n for n, f in vars(api).items() if inspect.isfunction(f) and f.__module__ == api.__name__ and not n.startswith("_")}
        self.assertEqual(public - listed, set())
        for s in ref[1:]:
            for a in s["entries"]:
                self.assertTrue(a["doc"], a["name"]); self.assertTrue(a["example"], a["name"])
        titles = [s["title"] for s in ref]
        for t in ("写指令要用的", "特殊的群", "物品与背包（金币也在这里）", "扩展自己存数据"):
            self.assertIn(t, titles)
        sigs = {a["name"]: a["sig"] for s in ref for a in s["entries"]}
        self.assertTrue(sigs["send_group"].startswith("await send_group("))
        self.assertIn("count: int=1", sigs["give_item"])

    def test_ready_made_pass_checks(self):
        ids = [m["id"] for m in E.ready_made()]
        self.assertEqual(sorted(ids), sorted(READY))
        for m in E.ready_made():
            self.assertEqual(E.validate_manifest(m), "", m["id"])
            d = E.READY_DIR / m["id"]
            errs, warns = E.check_code({"__init__.py": (d / "__init__.py").read_bytes()})
            self.assertEqual((errs, warns), ([], []), m["id"])
        self.assertIsNone(E.ready_zip("../x"))
        self.assertIsNone(E.ready_zip("nope_nope"))


class ReadyMadeE2E(unittest.TestCase):
    def setUp(self):
        _reset()
        SENT.clear(); PUSHED.clear()
        self.bot = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
        nonebot.get_bots()["B1"] = self.bot
        roles.create_role("甲", "B1", "G1", "M1")
        roles.create_role("乙", "B1", "G2", "M2")
        self.n = 0

    def tearDown(self):
        nonebot.get_bots().pop("B1", None)

    def say(self, text, member="M1", group="G1"):
        self.n += 1
        ev = GroupMessageCreateEvent.model_validate({
            "id": f"x{self.n}", "content": " " + text, "timestamp": "2026-11-19T00:00:00+08:00",
            "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
            "group_id": group, "group_openid": group})
        before = len(SENT)
        asyncio.new_event_loop().run_until_complete(handle_event(self.bot, ev))
        return SENT[before:]

    def test_all_loaded(self):
        for _id in READY:
            self.assertEqual(LOADED.get(_id, ""), "", _id)
            self.assertEqual(E.load_error(_id), "", _id)

    def test_signin(self):
        out = self.say("签到")
        self.assertIn("签到成功", out[-1]); self.assertIn("金币×10", out[-1]); self.assertIn("一共签到 1 天", out[-1])
        self.assertIn("已经签过", self.say("签到")[-1])
        a = roles.get_role_by_name("甲")["id"]
        self.assertEqual(api.item_count(a, "金币"), 10)
        out = self.say("签到", member="M9", group="G9")[-1]                              # 没有角色也能签：只记天数，不发金币
        self.assertIn("你签到成功", out); self.assertNotIn("金币", out)
        self.assertIn("已经签过", self.say("签到", member="M9", group="G9")[-1])
        from changri.core import pending
        self.assertIn("签到", "\n".join(pending.collect(roles.get_role_by_name("乙"))))           # 没签的人「我的待回」里有提醒
        self.assertNotIn("还没「签到」", "\n".join(pending.collect(roles.get_role_by_name("甲"))))

    def test_signin_streak(self):
        from unittest import mock
        mod = nonebot.get_plugin("daily_signin").module
        with mock.patch.object(mod, "_today_no", return_value=100):
            self.say("签到")
        with mock.patch("changri.core.season.day_key", return_value="次日"), mock.patch.object(mod, "_today_no", return_value=101):
            out = self.say("签到")
        self.assertIn("金币×12", out[-1]); self.assertIn("连续签到 2 天", out[-1])
        from changri.core import season
        season.wipe()                                                                  # 收尾：连签和总天数不清
        roles.create_role("甲", "B1", "G1", "M1")                                      # 新一季，同一个人重建角色
        with mock.patch("changri.core.season.day_key", return_value="新季第一天"), mock.patch.object(mod, "_today_no", return_value=102):
            out = self.say("签到")[-1]
        self.assertIn("连续签到 3 天", out); self.assertIn("一共签到 3 天", out)
        with mock.patch("changri.core.season.day_key", return_value="隔了一天"), mock.patch.object(mod, "_today_no", return_value=104):
            out = self.say("签到")[-1]
        self.assertIn("金币×10", out); self.assertIn("一共签到 4 天", out)              # 断签从头算，总天数照加

    def test_signin_carries_old_streak(self):
        """1.x 存在本季数据里的连签：第一次用 2.0 签到时接过来，旧记录删掉。"""
        a = roles.get_role_by_name("甲")["id"]
        api.season_data_set("daily_signin", f"streak_{a}", 4)
        api.season_data_set("daily_signin", f"last_{a}", "S1#3")
        out = self.say("签到")[-1]
        self.assertIn("连续签到 5 天", out); self.assertIn("金币×18", out)
        self.assertIsNone(api.season_data_get("daily_signin", f"streak_{a}"))

    def test_signin_bad_currency_setting(self):
        set_config(E.setting_key("daily_signin", "currency"), "不存在的币")
        try:
            self.assertIn("签到失败", self.say("签到")[-1])
            self.assertEqual(api.today_count("daily_signin", "签到", roles.get_role_by_name("甲")["id"]), 0)   # 没发成不算签过
        finally:
            set_config(E.setting_key("daily_signin", "currency"), "金币")

    def test_soda_shake(self):
        from unittest import mock
        self.assertIn("没有汽水", self.say("摇一下")[-1])
        with mock.patch("random.randint", return_value=3):                          # 第 3 下喷
            self.assertIn("甲 拿出一瓶汽水。最多 6 下", self.say("摇汽水")[-1])
        self.assertIn("已经有一瓶了", self.say("来瓶汽水")[-1])
        self.assertIn("第 1 下，甲 摇了摇", self.say("摇一下")[-1])
        self.assertIn("换个人", self.say("摇一下")[-1])                                # 不能连着摇
        self.assertIn("第 2 下，一位路人 摇了摇", self.say("摇一摇", member="M3")[-1])
        self.assertEqual(self.say("摇一下", group="G2", member="M2")[-1], "桌上没有汽水。发「摇汽水」拿一瓶。")   # 每个群各一瓶
        out = self.say("摇一下")[-1]
        self.assertIn("第 3 下——噗！汽水喷了 甲 一身！", out)
        self.assertIn("中招：", out)
        self.assertIn("没有汽水", self.say("摇一下", member="M3")[-1])                  # 喷完这瓶就没了
        self.say("摇汽水")
        self.assertIn("收走了", self.say("不摇了")[-1])
        api.data_set("soda_shake", "bottle_G1", {"pop": 5, "total": 6, "n": 1, "last": "", "ts": 0})
        self.assertIn("没有汽水", self.say("摇一下")[-1])                                # 放太久没人摇：作废
        api.data_set("soda_shake", "bottle_G1", {"pop": 6, "total": 6, "n": 4, "last": "", "ts": __import__("time").time()})
        self.assertIn("只剩最后一下", self.say("摇一下")[-1])

    def test_fortune_same_within_day(self):
        first = self.say("运势")[-1]
        self.assertIn("今日运势", first); self.assertIn("甲：", first)
        self.assertEqual(self.say("今日运势")[-1], first)

    def test_broadcast(self):
        self.assertIn("只有管理员", self.say("播报 戏群 开演")[-1])
        from unittest import mock
        with mock.patch("changri.core.admins.is_admin", return_value=True):
            entrance.set_stage("", "")
            self.assertIn("还没设戏群", self.say("播报 戏群 开演")[-1])
            entrance.set_stage("B1", "GSTAGE")
            self.assertIn("用法", self.say("播报 天台 开演")[-1])
            self.assertIn("已发到戏群", self.say("播报 戏群 第二幕开演")[-1])
        self.assertEqual(PUSHED[-1][0], "GSTAGE"); self.assertIn("第二幕开演", PUSHED[-1][1])

    def test_gold_rank(self):
        self.assertIn("还没有人有金币", self.say("富豪榜")[-1])
        a, b = (roles.get_role_by_name(n)["id"] for n in ("甲", "乙"))
        api.give_item(a, "金币", 5); api.give_item(b, "金币", 30)
        out = self.say("富豪榜")[-1]
        self.assertLess(out.index("乙"), out.index("甲"))


if __name__ == "__main__":
    unittest.main()
