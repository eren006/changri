"""跑团 log（第 2 步）：/log new|off|on|end|halt|list|get|stat|export|del、/说、/ob，以及记录群消息的钩子。
状态和规则在 core/trpglog.py，写入口在 plugins/_logtap.py。只在跑团模式的群里生效。"""
from nonebot import logger, on_command, on_message
from nonebot.adapters.qq import GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg
from nonebot.rule import Rule

from changri.core import admins, trpg, trpglog
from changri.core import qqmanager, qqpool
from changri.plugins._logtap import record_incoming, record_listened, record_say
from changri.core.qqsend import reply_long

EXPORT_CHARS = 6000


async def _trpg_rule(event) -> bool:
    return isinstance(event, GroupMessageCreateEvent) and trpg.get_mode(event.group_openid) == "trpg"


def _cmd(name, aliases=None):
    return on_command(name, aliases=aliases or set(), rule=Rule(_trpg_rule), priority=15, block=True)


# 记录钩子：优先级最前、不拦截，所有 @ 机器人的群消息都先过这里
tap = on_message(rule=Rule(_trpg_rule), priority=1, block=False)


@tap.handle()
async def _(event: GroupMessageCreateEvent):
    try:
        record_incoming(event)
    except Exception as e:  # noqa: BLE001 记 log 出错不能影响指令
        logger.warning(f"[跑团log] 记录消息出错：{e}")


# 自动关联：所有群消息（不限跑团模式）都过一下，好把「监听的群」和官方机器人那边的群对上
link_tap = on_message(rule=Rule(lambda event: isinstance(event, GroupMessageCreateEvent)), priority=1, block=False)


@link_tap.handle()
async def _(event: GroupMessageCreateEvent):
    try:
        gid = qqpool.note_official(event.group_openid, event.get_plaintext())
        if gid:
            logger.info(f"[跑团log] 自动关联：群 {gid} ⇄ {event.group_openid}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[跑团log] 自动关联出错：{e}")


def _on_listened(app_id, msg):
    try:
        gid = qqpool.note_heard(msg.group_id, msg.text)
        if gid:
            logger.info(f"[跑团log] 自动关联：群 {gid}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[跑团log] 自动关联出错：{e}")
    try:
        record_listened(app_id, msg)
    except Exception as e:  # noqa: BLE001 听到的消息记不下来不能影响别的
        logger.warning(f"[跑团log] 记录 QQ 号听到的消息出错：{e}")


if _on_listened not in qqmanager._listeners:
    qqmanager.add_listener(_on_listened)


def _may_manage(event: GroupMessageCreateEvent, log: dict) -> bool:
    """强制停止 / 删除：开启者或机器人管理员。"""
    return log["started_by"] == event.author.member_openid or admins.is_admin(current_bot.get().self_id, event.author.member_openid)


def _link_text(log: dict) -> str:
    url = trpglog.share_url(log)
    return f"网页查看（有链接的人都能看）：{url}" if url else "网页链接：还没设置玩家访问网址，先在后台「小手机」页开启并填好网址。"


HELP = ("【log】记录一场跑团\n/log new 名字　开始\n/log off　暂停　/log on　继续\n/log end　结束　/log halt　强制停止\n"
        "/log on 名字　接着记以前的\n/log list　/log stat [名字]　/log get 名字（网页链接）　/log export 名字（文字）　/log del 名字\n"
        "/说 台词　记一条角色发言（开了自动听取就不用）　/ob　切换旁观（旁观者的话不记）\n括号开头的话默认不记。")

log_cmd = _cmd("log")


@log_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    scope = event.group_openid
    parts = args.extract_plain_text().split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub in ("new", "新建", "开始"):
        log, err = trpglog.new_log(scope, rest, event.author.member_openid)
        await log_cmd.finish(f"开始记录「{log['name']}」（编号 {trpglog.short(log)}）。暂停 /log off，结束 /log end。" if log else (err or "格式：/log new 名字"))
    if sub in ("off", "暂停"):
        await log_cmd.finish(trpglog.pause(scope)[1])
    if sub in ("on", "继续"):
        await log_cmd.finish(trpglog.resume(scope, rest)[1])
    if sub in ("end", "结束", "halt", "强制停止", "stop"):
        halt = sub in ("halt", "强制停止", "stop")
        cur = trpglog.current(scope)
        if halt and cur and not _may_manage(event, cur):
            await log_cmd.finish("强制停止只有开启这个 log 的人或机器人管理员能用。")
        log, err = trpglog.finish(scope, halt)
        if not log:
            await log_cmd.finish(err)
        tail = f"\n接着记：/log on {log['name']}　文字全文：/log export {log['name']}"
        if not halt:
            trpglog.mark_shared(log["id"])
            tail = "\n" + _link_text(log) + tail
        await reply_long(log_cmd, ("已强制停止（不生成网页链接）。" if halt else "已结束。") + "\n" + trpglog.stats_text(log) + tail)
        return
    if sub in ("list", "列表"):
        logs = trpglog.list_logs(scope)
        if not logs:
            await log_cmd.finish("这个群还没有 log。/log new 名字 开始一个。")
        await reply_long(log_cmd, "本群的 log：\n" + "\n".join(
            f"#{trpglog.short(l)} {l['name']}　{trpglog.STATUS_TEXT[l['status']]}　{l['n']} 条" for l in logs))
        return
    if sub in ("stat", "统计"):
        log = trpglog.find(scope, rest) if rest else (trpglog.current(scope) or (trpglog.list_logs(scope) or [None])[-1])
        if not log:
            await log_cmd.finish("没有找到 log，看 /log list。")
        await reply_long(log_cmd, trpglog.stats_text(log))
        return
    if sub in ("get", "链接"):
        log = trpglog.find(scope, rest) if rest else (trpglog.current(scope) or (trpglog.list_logs(scope) or [None])[-1])
        if not log:
            await log_cmd.finish("格式：/log get 名字（看 /log list）。")
        trpglog.mark_shared(log["id"])
        await log_cmd.finish(f"「{log['name']}」\n" + _link_text(log))
    if sub in ("export", "导出"):
        log = trpglog.find(scope, rest) if rest else None
        if not log:
            await log_cmd.finish("格式：/log export 名字（看 /log list）。")
        text = trpglog.export_txt(log["id"])
        if not text:
            await log_cmd.finish(f"「{log['name']}」里还没有内容。")
        cut = len(text) > EXPORT_CHARS
        await reply_long(log_cmd, text[-EXPORT_CHARS:] + ("\n……（太长，只发了最后一段，完整内容在后台「跑团日志」里）" if cut else ""))
        return
    if sub in ("del", "delete", "删除"):
        log = trpglog.find(scope, rest) if rest else None
        if not log:
            await log_cmd.finish("格式：/log del 名字（看 /log list）。")
        if not _may_manage(event, log):
            await log_cmd.finish("删除只有开启这个 log 的人或机器人管理员能用。")
        trpglog.delete(log["id"])
        await log_cmd.finish(f"已删除「{log['name']}」。")
    await reply_long(log_cmd, HELP)


say_cmd = _cmd("说", {"s", "演"})


@say_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        await say_cmd.finish("格式：/说 台词（只记进 log，不会回复）。")
    if not trpglog.recording_id(event.group_openid):
        await say_cmd.finish("现在没有在记的 log。先 /log new 名字。")
    record_say(event, text)


ob_cmd = _cmd("ob", {"旁观"})


@ob_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    pid = trpg.player_for(event.author.member_openid, event.group_openid)
    on = trpglog.toggle_observer(event.group_openid, pid)
    await ob_cmd.finish("你现在是旁观者，发言不会进 log。再发一次 /ob 取消。" if on else "已取消旁观，你的发言会正常记录。")
