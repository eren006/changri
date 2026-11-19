"""扩展：心动信（投递、撤回、派送、信箱统计、定时派送）。"""
import re

import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from changri.core.qqsend import make_send, reply_long  # noqa: E402
from changri.core import admins, lovemail, messaging, roles, season
from changri.core.announce import get_announce
from changri.core.db import get_config, get_int, per_bot, set_config
from changri.core.schedule import run_lovemail_tick

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402
from changri.core.textutil import parse_id

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"


def _bid() -> str:
    """当前处理这条消息的机器人 ID（openid 是每个机器人各一套，认人要带上它）。"""
    return current_bot.get().self_id


def _sender(event: GroupMessageCreateEvent) -> str:
    return event.author.member_openid


def _me(event):
    return roles.find_role_for_sender(_bid(), event.group_openid, _sender(event))


def _send_fn() -> messaging.Send:
    return make_send()


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _name_args(args) -> list[str]:
    return args.extract_plain_text().split()


def _admin_only(matcher_name: str):
    return "❌ 权限不足，仅管理员可用。"


# ================= 心动信 =================

post_mail = _cmd("发送心动信", {"心动信"})


@post_mail.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await post_mail.finish("✨ 你还不是本系统的会员，请先使用「创建新角色」来认领你的身份吧。")
    receiver, content, signature = lovemail.parse_post(args.extract_plain_text())
    if not receiver:
        await post_mail.finish("⚠️ 格式错误！请指定发送对象。\n简单写法（署名是你的真名）：发送心动信 角色名 想说的话\n"
                               "想换署名用这种：\n发送心动信\n【发送对象】角色名\n【内容】想说的话\n【署名】自定义昵称（不写就是「匿名」）")
    target = roles.get_role_by_name(receiver)
    if not target:
        await post_mail.finish(f"⚠️ 找不到角色「{receiver}」的投递地址，请确认名字是否正确。")
    signature = signature or me["name"]
    ok, msg, left = lovemail.add_mail(me["id"], target["id"], signature, content)
    if not ok:
        await post_mail.finish(msg)
    limit = lovemail.today_limit()
    await post_mail.finish(
        f"💌 心动信已成功投递至「{target['name']}」的信箱！\n📝 署名：{signature}\n"
        + (f"{msg}\n📅 明天剩余次数：{left}/{lovemail.limit_for(lovemail.post_key())}" if msg else f"📅 今日剩余次数：{left}/{limit}")
        + "\n✨ 提示：派送前你仍可以使用「撤回心动信」取消本次投递。" + season.prep_note("还没派送的心动信"))


my_mails = _cmd("我的心动信")


@my_mails.handle()
async def _(event: GroupMessageCreateEvent):
    me = _me(event)
    if not me:
        await my_mails.finish(NEED_ROLE)
    pool = lovemail.my_pool(me["id"])
    if not pool:
        await my_mails.finish("📭 你目前没有待投递的信件。")
    text = "📄 你待投递的信件如下：\n" + "".join(f"\n#{i} | 接收者: {m['to_name']}\n内容: {m['content']}\n" for i, m in enumerate(pool, 1))
    await reply_long(my_mails, text)
    await my_mails.finish()


revoke_mail = _cmd("撤回心动信")


@revoke_mail.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await revoke_mail.finish("✨ 你还不是本系统的会员，请先使用「创建新角色」来认领你的身份吧。")
    parts = _name_args(args)
    n = parse_id(parts[0]) if parts else None
    if not n:
        await revoke_mail.finish("⚠️ 请输入正确的序号，例如：撤回心动信 1")
    _, msg = lovemail.revoke(me["id"], n)
    await revoke_mail.finish(msg)


deliver_cmd = _cmd("统一送心动信")


@deliver_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await deliver_cmd.finish(_admin_only("统一送心动信"))
    res = await lovemail.deliver_all(_send_fn(), get_announce())
    if res["empty"]:
        await deliver_cmd.finish("🕊️ 此时的邮局静悄悄的，还没有待投递的心意。")
    await deliver_cmd.finish(f"✅ 派送完成：成功 {res['success']} 封，失败 {res['fail']} 封（失败的留在信池里）。")


limit_cmd = _cmd("设置信箱上限")


@limit_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    """。设置信箱上限 D0:3 D1:5 / 默认 4 / 清空：按游戏天数设每天的投稿上限，没设的天用默认。"""
    if not admins.is_admin(_bid(), _sender(event)):
        await limit_cmd.finish(_admin_only("设置信箱上限"))
    arg = args.extract_plain_text().strip()
    default = lovemail.get_int("lovemail_default_limit", 3)
    if not arg:
        days = lovemail.day_limits()
        lines = [f"📮 心动信投稿上限：", f"• 默认：{default} 封/天"]
        lines += [f"• D{d}：{n} 封" for d, n in sorted(days.items())] or ["• 暂无按天的特殊设置"]
        await limit_cmd.finish("\n".join(lines) + "\n\n用法：。设置信箱上限 D0:3 D1:5 ｜ 默认 4 ｜ 清空")
    if arg == "清空":
        set_config("lovemail_day_limits", "")
        await limit_cmd.finish("✅ 已清空按天设置，只用默认上限。")
    if arg.startswith("默认"):
        n = arg[2:].strip()
        if not n.isdigit():
            await limit_cmd.finish("❌ 默认上限要写数字，例如：。设置信箱上限 默认 4")
        set_config("lovemail_default_limit", n)
        await limit_cmd.finish(f"✅ 默认上限改为 {n} 封/天。")
    parts = arg.replace("：", ":").split()
    new = {}
    for part in parts:
        m = re.fullmatch(r"[Dd](\d+):(\d+)", part)
        if not m:
            await limit_cmd.finish("❌ 格式错误，例如：。设置信箱上限 D0:3 D1:5")
        new[int(m.group(1))] = int(m.group(2))
    days = lovemail.day_limits()
    days.update(new)
    set_config("lovemail_day_limits", " ".join(f"D{d}:{n}" for d, n in sorted(days.items())))
    await limit_cmd.finish("✅ 已更新：" + "、".join(f"D{d} → {n} 封" for d, n in sorted(new.items())))


stat_mail = _cmd("信箱统计")


@stat_mail.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await stat_mail.finish(_admin_only("信箱统计"))
    pool = lovemail.pool_all()
    if not pool:
        await stat_mail.finish("🕊️ 此时的邮局静悄悄的，还没有待投递的心意。")
    per: dict[str, int] = {}
    for m in pool:
        per[m["to_name"]] = per.get(m["to_name"], 0) + 1
    lines = "\n".join(f"· {n}：{c} 封" for n, c in sorted(per.items(), key=lambda x: -x[1]))
    await stat_mail.finish(f"🌸 此时此刻，共有 {len(pool)} 份心意正在等待传递\n📮 {len(per)} 位收件人：\n{lines}")


# ================= 心动信定时派送（对齐长日系统：10 分钟前提醒个人群，5 分钟前提醒公告群，到点派送） =================

@scheduler.scheduled_job("interval", seconds=30, id="lovemail_tick", max_instances=1, coalesce=True)
@per_bot
async def _lovemail_tick():
    if not nonebot.get_bots():
        return
    try:
        await run_lovemail_tick(_send_fn())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[心动信] 定时任务出错：{e}")


# ---------- 往「我的待回」里登记 ----------
from changri.core import pending  # noqa: E402


def _pending_mail(role) -> list[str]:
    if not lovemail.enabled():
        return []
    n = lovemail.day_count(role["id"])
    mine = len(lovemail.my_pool(role["id"]))
    return [f"心动信：今天你已投 {n} 封，投递池里还有 {mine} 封没派送（我的心动信）"] if (n or mine) else []


pending.register("心动信", _pending_mail)
