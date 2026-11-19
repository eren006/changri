"""官约全流程模拟（不是测试，不会被 run.py 收进去）：走真正的 NoneBot 事件分发，把每一步各个群里看到的消息打印出来。

跑法（在 长日bot/ 目录下）：
    .venv/bin/python tests/sim_official_flow.py            # 全部场景
    .venv/bin/python tests/sim_official_flow.py 3 7        # 只跑第 3、7 个场景

群的叫法：P0/P1/P2 = 甲/乙/丙 的个人群，ADM = 管理员发指令的群，POOL0 = 房间群（官方机器人在里面），
STAFF = 后台群，ANN = 公告群，qq:100001 = QQ 小号管的房间群（卡拉不在里面）。
「（被拦）」= 机器人主动发的这条被 QQ 以「主动消息失败, 无权限」拒掉了，群里实际看不到。"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CHANGRI_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "sim.db")
os.environ["QQ_BOTS"] = "[]"
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter, Bot as QBot, GroupMessageCreateEvent  # noqa: E402
from nonebot.adapters.qq.config import BotInfo  # noqa: E402

nonebot.init(qq_bots=[], command_start={"/", ".", "。", ""}, log_level="ERROR")
nonebot.get_driver().register_adapter(Adapter)
from changri.core.db import get_conn, init_db, set_config  # noqa: E402

init_db()
from changri.core import extensions  # noqa: E402

extensions.load_all()
nonebot.load_plugin("changri.plugins.wishes")
from nonebot.message import handle_event  # noqa: E402

from changri.core import admins, announce, pool, qq_compat, qqmanager, roles, rooms, season  # noqa: E402

qq_compat.apply()
from changri.plugins import play  # noqa: E402

LABEL = {"P0": "甲的个人群", "P1": "乙的个人群", "P2": "丙的个人群", "ADM": "管理员群", "POOL0": "房间群POOL0",
         "STAFF": "后台群", "ANN": "公告群"}
BLOCKED: set[str] = set()          # 这些群没开「主动在群聊内发言」
LOG: list[str] = []


def _txt(m) -> str:
    if isinstance(m, str):
        return m
    try:
        t = getattr(m, "type", "")
        if t == "markdown":
            return m.data["markdown"].content.replace('<qqbot-at-user id="', "@<").replace('" />', ">")
        if t == "file_image":
            return "<图片>"
    except Exception:  # noqa: BLE001
        pass
    return str(m)


class FakeBot(QBot):
    async def send(self, event, message, **kw):                  # 回复指令（被动消息，不受主动发言权限影响）
        LOG.append(f"  ↩ 回复[{LABEL.get(event.group_openid, event.group_openid)}] {_txt(message)}")

    async def send_to_group(self, group_openid, message, **kw):  # 主动发
        where = LABEL.get(group_openid, group_openid)
        if group_openid in BLOCKED:
            LOG.append(f"  ✖ 主动[{where}]（被拦）{_txt(message)[:60]}…")
            raise RuntimeError("主动消息失败, 无权限")
        LOG.append(f"  → 主动[{where}] {_txt(message)}")


QQ_SAID: list = []


async def fake_room_say(app, gid, text, ats):
    LOG.append(f"  → 小号在[qq:{gid}]说{('（@' + ','.join(ats) + '）') if ats else ''} {text}")
    return ""


async def fake_room_rename(bot_id, g, name):
    LOG.append(f"  ✎ 小号把群 {g['real_gid']} 改名为「{name}」")


QQ_MEMBERS: dict[str, set[str]] = {}


async def fake_group_members(app, gid):
    return set(QQ_MEMBERS.get(gid, set()))


async def fake_group_others(app, gid):
    return set()


qqmanager.room_say = fake_room_say
qqmanager.room_rename = fake_room_rename
qqmanager.group_members = fake_group_members
qqmanager.group_others = fake_group_others
qqmanager.link_status = lambda app_id, link_id: {"running": True, "connected": True, "online": True, "self_id": "1", "error": "", "count": 0, "last": 0}

BOT = None
N = 0
TABLES = ("person_qq", "room_lines", "votes", "join_requests", "replies", "posts", "wishes", "room_members", "rooms", "pool_groups",
          "seasons", "entrance", "role_groups", "roles", "admins", "admin_codes", "config", "blocks")


def flush():
    for x in LOG:
        print(x)
    LOG.clear()


def say(text, who="甲", group=None):
    """who：甲/乙/丙/管理员；group 不写 = 自己的个人群（管理员是 ADM）。"""
    global N
    N += 1
    member = {"甲": "M0", "乙": "M1", "丙": "M2", "管理员": "ADMIN"}[who]
    group = group or {"甲": "P0", "乙": "P1", "丙": "P2", "管理员": "ADM"}[who]
    print(f"\n【{who} 在 {LABEL.get(group, group)} 发】{text}")
    ev = GroupMessageCreateEvent.model_validate({
        "id": f"e{N}", "content": " " + text, "timestamp": "2026-11-13T00:00:00+08:00",
        "author": {"id": member, "member_openid": member, "union_openid": member, "bot": False},
        "group_id": group, "group_openid": group})
    asyncio.new_event_loop().run_until_complete(handle_event(BOT, ev))
    flush()


def run(coro, title):
    print(f"\n【后台】{title}")
    asyncio.new_event_loop().run_until_complete(coro)
    flush()


def rng(back=1, fwd=5):
    t = time.time()
    return time.strftime("%m%d", time.localtime(t - back * 86400)) + "-" + time.strftime("%m%d", time.localtime(t + fwd * 86400))


def reset(qq_only=False, pools=1):
    global BOT
    BLOCKED.clear()
    QQ_MEMBERS.clear()
    with get_conn() as c:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence")
    BOT = FakeBot(nonebot.get_adapter(Adapter), "B1", BotInfo(id="B1", token="", secret="x" * 16))
    nonebot.get_bots()["B1"] = BOT
    admins.claim(admins.make_code(), "B1", "ADMIN")
    set_config("qq_buttons", "0")
    for cmd in (f"预订季度 甲季 {rng()}", "开始季度 确认", "设置天数 D1"):
        asyncio.new_event_loop().run_until_complete(handle_event(BOT, GroupMessageCreateEvent.model_validate({
            "id": f"s{cmd}", "content": " " + cmd, "timestamp": "2026-11-13T00:00:00+08:00",
            "author": {"id": "ADMIN", "member_openid": "ADMIN", "union_openid": "ADMIN", "bot": False},
            "group_id": "ADM", "group_openid": "ADM"})))
    LOG.clear()
    for i, name in enumerate(["甲", "乙", "丙"]):
        roles.create_role(name, "B1", f"P{i}", f"M{i}")
    announce.set_backstage("B1", "STAFF") if hasattr(announce, "set_backstage") else set_config("backstage_group", "B1|STAFF")
    announce.set_announce("B1", "ANN")
    if qq_only:
        from changri.core import qqpool
        qqpool.add_rooms("B1", "100001", "A组")
    else:
        for i in range(pools):
            pool.register("B1", f"POOL{i}", real_gid=f"12345{i}", set_name="A组")
            pool.set_in_pool(pool.is_pool_group("B1", f"POOL{i}")["id"], True)
            LABEL[f"POOL{i}"] = f"房间群POOL{i}"


def head(n, title):
    print("\n" + "=" * 80 + f"\n场景 {n}：{title}\n" + "=" * 80)


def state(rid):
    r = rooms.get(rid)
    if not r:
        print(f"  [状态] R{rid} 已不存在")
        return
    mem = ", ".join(f"{m['name']}{'(到场)' if m['entered_ts'] else '(未到)'}" for m in rooms.members(rid))
    g = pool.get(r["pool_id"]) if r["pool_id"] else None
    print(f"  [状态] R{rid} {r['kind']} D{r['day']} {r['start_hm']}-{r['end_hm']} status={r['status']} 群={g['real_gid'] + '/' + g['state'] if g else '无'} 成员：{mem}")


# ---------------------------------------------------------------- 场景
def s1():
    head(1, "官方机器人·正常流程：发起 → 进群 → 人齐 → 结束 → 清场放回")
    reset()
    say("发起官约 1400-1500 后山 甲/乙/丙", "管理员")
    state(1)
    say("我到了", "甲", "POOL0")
    say("我也到了", "乙", "POOL0")
    say("丙：来晚了", "丙", "POOL0")
    state(1)
    say("我的待回", "甲")
    say("结束官约", "乙", "POOL0")
    state(1)
    say("放回备用群", "管理员", "POOL0")
    say("时间线", "丙")


def s2():
    head(2, "玩家照邀请里写的「修改时间」「不想参加 ➜ 拒绝时间线」去做")
    reset()
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("修改时间线 R1 D1 1600-1700", "乙")
    say("拒绝时间线 R1", "乙")
    say("取消官约 R1", "乙")
    say("结束官约 R1", "乙")          # 在个人群里带房间号结束：一个人就能把官约结束掉
    state(1)


def s3():
    head(3, "有人不到场：丙一直没进群，甲乙聊完结束")
    reset(pools=2)
    say("发起官约 1400-1500 后山 甲/乙/丙", "管理员")
    say("甲：开场", "甲", "POOL0")
    say("乙：接戏", "乙", "POOL0")
    say("结束官约", "甲", "POOL0")
    state(1)
    say("时间线", "丙")
    say("复盘", "丙")
    print("\n  [补充] 甲在房间还没人到时就发「结束官约」：")
    say("发起官约 1600-1700 后山 甲/乙", "管理员")
    say("结束官约 R2", "甲")
    state(2)


def s4():
    head(4, "重复发指令：同一条发起官约发两次；计划官约 / 执行官约 发两次")
    reset(pools=3)
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("计划官约 D2 2000-2100 后山 甲/丙", "管理员")
    say("计划官约 D2 2000-2100 后山 甲/丙", "管理员")
    say("计划官约", "管理员")
    say("执行官约", "管理员")
    say("执行官约", "管理员")
    say("结束官约", "甲", pool.get(rooms.get(1)["pool_id"])["group_openid"])
    say("结束官约 R1", "甲")
    say("结束官约 R1", "甲")


def s5():
    head(5, "被取消：管理员取消官约；取消后再取消；用「取消房间」取消玩家自己约的约会")
    reset(pools=2)
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("甲：到了", "甲", pool.get(rooms.get(1)["pool_id"])["group_openid"])
    say("取消官约 R1", "管理员")
    state(1)
    say("取消官约 R1", "管理员")
    say("时间线", "甲")
    say("约会 1600-1700 咖啡厅 乙", "甲")
    say("取消官约 R2", "管理员")


def s6():
    head(6, "跨天：提前排 D3 的官约；计划官约不写 D 几，第二天才执行；23:59 推进；D 几 写成过去的天")
    reset(pools=3)
    set_config("room_expire_enabled", "1")
    say("发起官约 D3 2000-2100 后山 甲/乙", "管理员")
    state(1)
    print("\n  [结戏限时开着 24 小时] 把创建时间往前拨 21 小时 → 提醒；拨 25 小时 → 自动结束：")
    with get_conn() as c:
        c.execute("UPDATE rooms SET created_ts=? WHERE id=1", (int(time.time()) - 21 * 3600,))
    run(play._warn_expiring(), "定时任务：结戏限时提醒")
    with get_conn() as c:
        c.execute("UPDATE rooms SET created_ts=? WHERE id=1", (int(time.time()) - 25 * 3600,))
    run(play._tick_expired(), "定时任务：到时间强制结群")
    state(1)
    set_config("room_expire_enabled", "0")
    say("计划官约 2000-2100 后山 甲/丙", "管理员")
    say("计划官约 D0 2100-2200 后山 乙/丙", "管理员")
    say("计划官约", "管理员")
    print("\n  [23:59 自动推进到 D2]")
    from changri.core.db import get_config
    set_config("auto_day", "1")
    set_config("auto_day_last", "")
    lt = time.localtime()
    now = time.struct_time((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 10, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    sends = []

    async def send(b, g, t):
        sends.append(t)
    r = asyncio.new_event_loop().run_until_complete(season.tick(send, ("B1", "ANN"), now))
    print(f"  tick → {r}；当前 D{season.current_day()}；公告群：{sends}")
    print(f"  （注意：game_day={get_config('game_day')}）")
    say("执行官约", "管理员")
    say("时间线 丙", "管理员")
    say("发起官约 D0 1000-1100 后山 甲/乙", "管理员")
    say("发起官约 D99 1000-1100 后山 甲/乙", "管理员")


def s7():
    head(7, "管理员介入：强结、删除时间线、后台网页结束")
    reset(pools=3)
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("甲：在", "甲", pool.get(rooms.get(1)["pool_id"])["group_openid"])
    say("强结约会 R1", "管理员")
    state(1)
    say("发起官约 1600-1700 后山 甲/乙", "管理员")
    gid = pool.get(rooms.get(2)["pool_id"])["group_openid"]
    say("删除时间线 甲 R2", "管理员")
    state(2)
    say("发起官约 1800-1900 后山 甲/乙", "管理员")
    pid = rooms.get(3)["pool_id"]
    rooms.end(3)                         # 后台「结束」按钮做的事（features/play/admin.room_end）
    print("\n【后台网页】点 R3 的「结束」 → 提示：R3 已结束（网页上结束不会通知玩家，群放回池里）")
    print(f"  实际群状态：{pool.get(pid)['state']}（cleaning = 等人退群，不是放回池里）；房间群和后台群都没收到任何消息")


def s8():
    head(8, "QQ 小号管的房间群：没绑 QQ 的人、到场靠查成员、在群里结束")
    reset(qq_only=True)
    roles.bind_qq(roles.get_role_by_name("甲")["id"], "555555")
    roles.bind_qq(roles.get_role_by_name("乙")["id"], "666666")
    say("发起官约 1400-1500 后山 甲/乙/丙", "管理员")
    state(1)
    QQ_MEMBERS["100001"] = {"555555", "666666", "777777"}       # 丙（777777）进群了但没绑 QQ
    run(play._check_presence(), "定时任务：查房间群成员")
    state(1)
    from changri.core.qqlink import GroupMsg
    run(play._on_listened("B1", GroupMsg("9", "100001", "777777", "丙", "丙：我到了", 1, "1")), "小号听到丙说话")
    state(1)
    run(play._on_listened("B1", GroupMsg("9", "100001", "777777", "丙", "结束官约", 1, "2")), "小号听到丙发「结束官约」")
    run(play._on_listened("B1", GroupMsg("9", "100001", "555555", "甲", "修改时间线 D1 1600-1700", 1, "3")), "小号听到甲发「修改时间线」")
    run(play._on_listened("B1", GroupMsg("9", "100001", "555555", "甲", "结束官约", 1, "4")), "小号听到甲发「结束官约」")
    state(1)


def s9():
    head(9, "主动发言被拦：房间群没开主动发言")
    reset()
    BLOCKED.add("POOL0")
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("甲：到了", "甲", "POOL0")
    say("乙：到了", "乙", "POOL0")
    say("结束官约", "甲", "POOL0")
    state(1)


def s10():
    head(10, "主动发言被拦：乙的个人群没开主动发言（邀请发不到）")
    reset()
    BLOCKED.add("P1")
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("我的待回", "乙")
    print("\n  [补充] 两个人的个人群都被拦：")
    BLOCKED.add("P0")
    say("发起官约 1600-1700 后山 甲/乙", "管理员")


def s11():
    head(11, "名单写法：空格隔开名字、地点写在名单后、地点恰好是角色名")
    reset(pools=3)
    say("发起官约 1400-1500 甲 乙", "管理员")
    say("发起官约 1400-1500 甲/乙 后山", "管理员")
    say("发起官约 1400-1500 后山 甲、乙", "管理员")


def s12():
    head(12, "地点系统开着：后山要钥匙，名单第一个人没有钥匙")
    reset()
    from changri.core import rpg
    set_config("place_system_enabled", "1")
    rpg.save_place("后山", locked=True)
    pid = next(p["id"] for p in rpg.places() if p["name"] == "后山")
    rpg.set_key(roles.get_role_by_name("乙")["id"], pid, True)
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("发起官约 1400-1500 后山 乙/甲", "管理员")
    set_config("place_system_enabled", "0")
    _ = rpg


def s13():
    head(13, "超时提醒（扩展开着）：提前排的官约，名单第一个人 3 小时后被催")
    reset()
    from changri.core import overdue
    set_config("ext:overdue", "1")
    say("发起官约 D3 2000-2100 后山 乙/甲", "管理员")
    with get_conn() as c:
        c.execute("UPDATE rooms SET created_ts=? WHERE id=1", (int(time.time()) - 4 * 3600,))
    items = overdue.due()
    for it in items:
        print("  会发给", roles.get_role(it["role_id"])["name"], "：", overdue.text_for([it]))


def s14():
    head(14, "人数不够 / 不在进行中的季度 / 群池用完")
    reset(pools=1)
    say("发起官约 1400-1500 后山 甲", "管理员")
    say("发起官约 1400-1500 后山 甲/乙", "管理员")
    say("发起官约 1600-1700 后山 乙/丙", "管理员")
    run(play._tick_pool_alert(), "定时任务：群池用完提醒后台群")


SCENES = [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14]

if __name__ == "__main__":
    pick = [int(x) for x in sys.argv[1:]] or range(1, len(SCENES) + 1)
    for i in pick:
        try:
            SCENES[i - 1]()
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"\n!!! 场景 {i} 出错：{e}")
            traceback.print_exc()
