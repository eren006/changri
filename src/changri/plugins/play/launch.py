"""发起房间：约会 / 电话 / 踩点 / 微信，发起官约 / 官电，计划官约 / 执行官约。"""

import json
import re

from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import messaging, roles, rooms, season, storycal
from changri.core.db import get_config, set_config
from changri.core.overview import group_line as _group_line, slot_text as _slot
from changri.core.buttons import CARD_PER_ROW, Btn
from changri.core.qqsend import finish_usage, make_send, reply_buttons, reply_long
from changri.core.textutil import hm_text, join_time_range

from .common import NEED_ROLE, NOT_ADMIN, _announce_in_room, _cmd, _invite, _is_admin, _me, _missed_line, _names, _push, _push_group, _qq_hint, _resolve, WECHAT_TIP
from .finish import NO_GROUP_TEXT, _ensure_clean


# ================= 发起房间 =================

async def start_room(kind: str, me, targets: list, time_text: str, place: str = "", note: str = "") -> tuple[bool, str, int]:
    """发起约会/电话/微信并通知对方（QQ 指令和网页小手机共用）。返回 (成功, 文字, 房间号)。"""
    from changri.core import letters                  # 写信综开着、设了「发起约会要几个写信币」：不够就不让发，开成了才扣
    why = letters.check_cost(me["id"], "date") if kind == "约会" else ""
    if why:
        return False, why, 0
    ok, info, room_id = rooms.create(kind, me["id"], [t["id"] for t in targets], time_text, place, note)
    if room_id and not await _ensure_clean(room_id):
        return False, NO_GROUP_TEXT.format(kind=kind), 0
    if not ok:
        miss = rooms.pop_missed(me["id"])              # 对方那个时间已经有安排：在对方个人群留一句，谁想约、什么时候、没约成
        if miss:
            await _push(miss["role_id"], f"📅 {me['name']} 想在 D{miss['day']} {hm_text(miss['start_hm'], miss['end_hm'])} 约你（{miss['kind']}），"
                                         "但这个时间你已经有安排了，没约成。")
        return False, info, 0
    room = rooms.get(room_id)
    for t in targets:
        await _invite(t["id"], room, me["name"])
    await _announce_in_room(room_id)
    refused = info.partition("\n")[2]           # 多人邀约里有人拉黑了你：create 在提示后面附了一行
    paid = letters.pay(me["id"], "date", f"发起约会 R{room_id}") if kind == "约会" else ""
    refused = "\n".join(x for x in (refused, paid) if x)
    hint = _qq_hint(me["id"], room)
    if kind == "微信":
        hint = "\n" + WECHAT_TIP + hint
    if kind in rooms.SOLO_KINDS:
        return True, f"✅ {kind}已发起，房间号 R{room_id}（只有你一个人）。\n{_slot(room)}\n{_group_line(room)}\n聊完在群里发「结束{kind}」。" + hint, room_id
    return True, f"✅ {kind}已发起，房间号 R{room_id}。\n{_slot(room)}\n{_group_line(room)}\n对方已收到通知。" + (f"\n{refused}" if refused else "") + hint, room_id


async def notify_join_request(me, room, qid: int) -> None:
    for m in rooms.members(room["id"]):
        await _push(m["role_id"], f"🙋 {me['name']} 想加入 R{room['id']} {room['kind']}（{_slot(room)}）。\n同意发「同意加入 {qid}」，拒绝发「拒绝加入 {qid}」（「加入请求」可以看全部）。")


async def notify_join_answer(me, q, accept: bool) -> None:
    room = rooms.get(q["room_id"])
    if accept:
        await _push(q["role_id"], f"✅ {me['name']} 同意了你加入 R{room['id']} {room['kind']}。\n{_slot(room)}" + _qq_hint(q["role_id"], room))
        await _push_group(q["role_id"], room)
    else:
        await _push(q["role_id"], f"❌ 你加入 R{room['id']} 的申请被拒绝了。")


async def _start_room(matcher, bot: Bot, event: GroupMessageCreateEvent, kind: str, time_text: str, place: str, names: list[str]):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    if re.fullmatch(r"[Dd]\d+", time_text):          # 「约会 D2 1400-1500 …」：玩家只能约当天，不然后面的字整体错位，报成「找不到角色」
        await matcher.finish(f"❌ {kind}只能约当天，前面不用写 {time_text.upper()}。\n"
                             f"想约到别的天：先约当天，再在房间里发「修改时间线 R房间号 {time_text.upper()} 新时间」，所有人同意后生效。")
    targets, missing = _resolve(names)
    if missing:
        await matcher.finish(f"❌ 找不到角色：{missing}。")
    ok, text, _rid = await start_room(kind, me, targets, time_text, place)
    await matcher.finish(text if ok else f"❌ {text}")


async def _pick(matcher, bot: Bot, event: GroupMessageCreateEvent, kind: str) -> None:
    """「约会」「电话」后面什么都不写：选人卡片（core/datepick.py），点名字把指令填进输入框。没有角色就照旧给用法。"""
    from changri.core import datepick
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    await _send_card(matcher, *datepick.card(kind, me["id"]))


SECONDARY = ("自己写", "帮助", "别的地方", "换个人")


async def _send_card(matcher, text: str, items, plain: str = "") -> None:
    await reply_buttons(matcher, text, [Btn(label, cmd, enter=send, style="secondary" if label in SECONDARY else "primary")
                                        for label, cmd, send in items], per_row=CARD_PER_ROW, plain=plain)
    await matcher.finish()


async def _pick_place(matcher, bot: Bot, event: GroupMessageCreateEvent, names_text: str) -> bool:
    """「约会 张三」（选人卡片第一步点出来的）：对象都认得、又有预设地点，就回地点卡片，返回 True；否则 False，照旧给用法。"""
    from changri.core import charprofile, datepick
    me = _me(bot, event)
    if not me:
        return False
    names = _names(names_text)
    targets, missing = _resolve(names)
    if missing or not targets or not datepick.places_for(me["id"]):
        return False
    label = "、".join(charprofile.btn_name(t["name"], charprofile.get(t["id"])["short"]) for t in targets)
    await _send_card(matcher, *datepick.place_card(me["id"], "/".join(t["name"] for t in targets), label))
    return True


private = _cmd("约会", {"私约"})                 # 0.21 起私约改名约会，老叫法照样认


@private.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    p = join_time_range(args.extract_plain_text()).split(None, 2)
    if not p:
        await _pick(private, bot, event, "约会")
    if len(p) == 1:
        await _pick_place(private, bot, event, p[0])
    if len(p) < 3:
        await finish_usage(private, f"用法：约会 时间 地点 对方（多人用 / 隔开）\n例：约会 {storycal.example_time()} 咖啡厅 张三/李四", "约会 ")
    await _start_room(private, bot, event, "约会", p[0], p[1], _names(p[2]))


phone = _cmd("电话")


@phone.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    p = join_time_range(args.extract_plain_text()).split(None, 1)
    if not p:
        await _pick(phone, bot, event, "电话")
    if len(p) < 2:
        await finish_usage(phone, f"用法：电话 时间 对方（多人用 / 隔开）\n例：电话 {storycal.example_time()} 张三", "电话 ")
    await _start_room(phone, bot, event, "电话", p[0], "", _names(p[1]))


stakeout = _cmd("踩点")


@stakeout.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not rooms.stakeout_enabled():
        await stakeout.finish("❌ 踩点没有开放。")
    p = join_time_range(args.extract_plain_text()).split(None, 1)
    if len(p) < 2:
        await finish_usage(stakeout, f"用法：踩点 时间 地点（一个人的群，不带别人）\n例：踩点 {storycal.example_time()} 大图书馆", "踩点 ")
    await _start_room(stakeout, bot, event, "踩点", p[0], p[1].strip(), [])


wechat = _cmd("微信")


@wechat.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not rooms.wechat_qq():
        await wechat.finish("多人群聊已经搬到小手机里了：小手机 → 发现 → 邀约 → 发起多人群聊（需要管理员在小手机设置里打开）。")
    names = args.extract_plain_text().strip()
    if not names:
        await finish_usage(wechat, "用法：微信 对方（多人用 / 隔开；不占时间线，不用写时间地点）\n例：微信 张三/李四", "微信 ")
    await _start_room(wechat, bot, event, "微信", "", "", _names(names))


_EMPTY_PLACE = {"无", "空", "-", "—"}


def parse_official(text: str, call: bool = False):
    """「[D几] 时间 [地点] 名字/名字」→ (day, 时间, 地点, 名字列表)；地点可以不写（官电本来就没有）。格式不对返回 None。"""
    p = join_time_range(text).split()
    day = None
    if p and re.fullmatch(r"[Dd]\d+", p[0]):
        day, p = int(p[0][1:]), p[1:]
    if not p or not rooms.parse_hm(p[0], day):
        return None
    t, rest = p[0], p[1:]
    if not rest:
        return None
    place = ""
    if not call and len(rest) >= 2 and not roles.get_role_by_name(rest[0]):       # 第一段本身是角色名就当没写地点
        place, rest = rest[0], rest[1:]
    if place in _EMPTY_PLACE:
        place = ""
    names = _names(" ".join(rest))
    return (day, t, place, names) if names else None


def _official_fix(room) -> tuple[str, str]:
    return ("💌 电话", "📞 官电") if room["kind"] == "电话" else ("💌 官约", "🏢 官约")


async def _send_official_invites(room_id: int) -> list[tuple[str, str]]:
    """给官约 / 官电的每个人发邀请（带群号），再在房间群里发说明。返回没发到的 [(名字, 原因)]。"""
    room = rooms.get(room_id)
    missed = []
    for m in rooms.members(room_id):
        ok, info = await _invite(m["role_id"], room, fix=_official_fix(room))
        if not ok:
            missed.append((m["name"], info))
    await _announce_in_room(room_id)
    return missed


async def _launch_official(targets: list, time_text: str, place: str = "", day: int | None = None, call: bool = False) -> tuple[bool, str, int, list]:
    """launch_official 的内部版本，多返回没发到的 [(名字, 原因)]（执行官约的汇总要用）。
    排到以后几天的（D几 比今天大）：先只占时间线、发一条预告，到那天再领群、发邀请（_tick_deferred）。"""
    label = "官电" if call else "官约"
    if len(targets) < 2:
        return False, f"{label}至少要两位角色。", 0, []
    kind = "电话" if call else "官约"
    today = season.current_day()
    defer = day is not None and today is not None and day > today
    ok, info, room_id = rooms.create(kind, targets[0]["id"], [t["id"] for t in targets[1:]], time_text, "" if call else place, "官电" if call else "",
                                     official=True, day=day, defer=defer)
    if room_id and not await _ensure_clean(room_id):
        return False, NO_GROUP_TEXT.format(kind=kind), 0, []
    if not ok:
        return False, info, 0, []
    room = rooms.get(room_id)
    missed = []
    if defer:
        at = rooms.defer_open_hm(room)
        for t in targets:
            others = "、".join(x["name"] for x in targets if x["id"] != t["id"])
            ok_, why = await messaging.push_to_role(t["id"], f"📅 {label}预告：管理员给你排了一场{label}，和 {others}。\n{_slot(room)}\n房间号 R{room_id}\n"
                                                            f"这段时间已经记在你的时间线上；D{day} 当天 {at} 左右再发群号和邀请。\n时间由管理员安排，来不了请联系管理员。",
                                                    make_send(), True)
            if not ok_:
                missed.append((t["name"], why))
        sent = len(targets) - len(missed)
        text = (f"✅ {label}已排好，房间号 R{room_id}（还没到 D{day}）。\n{_slot(room)}\n"
                f"先只占时间线，D{day} 当天 {at} 左右再分群、发邀请。已发预告给 {sent} 人。")
    else:
        missed = await _send_official_invites(room_id)
        sent = len(targets) - len(missed)
        text = f"✅ {label}已发起，房间号 R{room_id}，已通知 {sent} 人。\n{_slot(room)}\n{_group_line(room)}"
    note = _missed_line(missed)
    return True, text + (f"\n{note}" if note else ""), room_id, missed


async def launch_official(targets: list, time_text: str, place: str = "", day: int | None = None, call: bool = False) -> tuple[bool, str, int]:
    """发起官约/官电并通知所有人（QQ 指令和后台页面共用）。返回 (成功, 给管理员看的文字, 房间号)。
    给管理员的回执按真正发到的人数说，没发到的点名。"""
    ok, text, rid, _missed = await _launch_official(targets, time_text, place, day, call)
    return ok, text, rid


OFFICIAL_USAGE = ("用法：发起官约 [D几] 时间 [地点] 名字/名字（至少两人；地点可以不写，D几 不写就是今天）\n"
                  f"例：发起官约 {storycal.example_time()} 后山 张三/李四\n例：发起官约 D3 {storycal.example_time()} 张三/李四（不写地点）")
OFFICIAL_CALL_USAGE = "用法：发起官电 [D几] 时间 名字/名字（至少两人）\n例：发起官电 D2 2000-2100 张三/李四"

official = _cmd("发起官约")


@official.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await official.finish(NOT_ADMIN)
    parsed = parse_official(args.extract_plain_text())
    if not parsed:
        await official.finish(OFFICIAL_USAGE)
    day, t, place, names = parsed
    targets, missing = _resolve(names)
    if missing:
        await official.finish(f"❌ 找不到角色：{missing}。")
    ok, text, _rid = await launch_official(targets, t, place, day)
    await official.finish(text if ok else f"❌ {text}")


official_phone = _cmd("发起官电")


@official_phone.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await official_phone.finish(NOT_ADMIN)
    parsed = parse_official(args.extract_plain_text(), call=True)
    if not parsed:
        await official_phone.finish(OFFICIAL_CALL_USAGE)
    day, t, _place, names = parsed
    targets, missing = _resolve(names)
    if missing:
        await official_phone.finish(f"❌ 找不到角色：{missing}。")
    ok, text, _rid = await launch_official(targets, t, "", day, call=True)
    await official_phone.finish(text if ok else f"❌ {text}")


def _plans() -> list[dict]:
    try:
        return json.loads(get_config("official_plans", "[]"))
    except ValueError:
        return []


def _plan_line(i: int, p: dict) -> str:
    return f"{i}. {'D' + str(p['day']) + ' ' if p.get('day') else ''}{p['time']}{' @' + p['place'] if p['place'] else ''} {p['names']}"


plan_cmd = _cmd("计划官约")
run_plan = _cmd("执行官约")


@plan_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await plan_cmd.finish(NOT_ADMIN)
    t = args.extract_plain_text().strip()
    if t in ("", "查看"):
        ps = _plans()
        await plan_cmd.finish("📝 已计划的官约\n" + "\n".join(_plan_line(i + 1, p) for i, p in enumerate(ps)) + "\n发「执行官约」一次性发起。" if ps else "还没有计划的官约。写法：计划官约 [D几] 时间 [地点] 名字/名字")
    if t.startswith("清空"):
        set_config("official_plans", "[]")
        await plan_cmd.finish("✅ 已清空计划。")
    parsed = parse_official(t)
    if not parsed:
        await plan_cmd.finish(f"用法：计划官约 [D几] 时间 [地点] 名字/名字\n例：计划官约 D2 {storycal.example_time()} 后山 张三/李四\n（先攒着，发「执行官约」一起发起；「计划官约 清空」清掉）")
    day, tm, place, names = parsed
    targets, missing = _resolve(names)
    if missing or len(targets) < 2:
        await plan_cmd.finish(f"❌ {'找不到角色：' + missing if missing else '官约至少要两位角色。'}")
    set_config("official_plans", json.dumps(_plans() + [{"day": day, "time": tm, "place": place, "names": "/".join(names)}], ensure_ascii=False))
    await plan_cmd.finish(f"✅ 已记下，现在共 {len(_plans())} 条计划。")


@run_plan.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not _is_admin(bot, event):
        await run_plan.finish(NOT_ADMIN)
    ps = _plans()
    if not ps:
        await run_plan.finish("没有计划的官约。")
    done, fail, left = [], [], []
    for p in ps:
        targets, _ = _resolve(_names(p["names"]))
        ok, info, rid, missed = await _launch_official(targets, p["time"], p["place"], p.get("day"))
        if ok:
            done.append(f"R{rid} {_plan_line(0, p)[3:]}" + ("（还没到那天，先占时间线）" if rooms.is_deferred(rooms.get(rid)) else "")
                        + (f"\n　{_missed_line(missed)}" if missed else ""))
        else:
            fail.append(f"{_plan_line(0, p)[3:]}：{info}")
            left.append(p)
    set_config("official_plans", json.dumps(left, ensure_ascii=False))     # 失败的留着，改好再执行
    await reply_long(run_plan, f"✅ 已发起 {len(done)} 场\n" + "\n".join(done) + (f"\n\n❌ 失败 {len(fail)} 场（保留在计划里）\n" + "\n".join(fail) if fail else ""))
