"""核心：拉黑、认管理员、公告群。这些是别的功能的地基，不属于任何可关闭的扩展。"""
import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from core import admins, blocks, roles
from core import announce as announce_core

NEED_ROLE = "请先在你的个人群里发「创建新角色 名字」绑定角色。"


def _bid() -> str:
    """当前处理这条消息的机器人 ID（openid 是每个机器人各一套，认人要带上它）。"""
    return current_bot.get().self_id


def _sender(event: GroupMessageCreateEvent) -> str:
    return event.author.member_openid


def _me(event):
    return roles.find_role_for_sender(_bid(), event.group_openid, _sender(event))


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _name_args(args) -> list[str]:
    return args.extract_plain_text().split()


def _admin_only(matcher_name: str):
    return "❌ 权限不足，仅管理员可用。"


# ================= 拉黑 =================

block_cmd = _cmd("拉黑")


@block_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await block_cmd.finish(NEED_ROLE)
    parts = _name_args(args)
    if not parts:
        await block_cmd.finish("⚠️ 格式：拉黑 角色名 [静默/不静默]\n例：拉黑 张三\n例：拉黑 张三 静默")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await block_cmd.finish(f"❌ 未找到角色「{parts[0]}」")
    if target["id"] == me["id"]:
        await block_cmd.finish("❌ 不能拉黑自己")
    silent = len(parts) > 1 and parts[1] == "静默"
    blocks.block(me["id"], target["id"], silent)
    await block_cmd.finish(
        f"🚫 已拉黑「{target['name']}」（{'静默' if silent else '不静默'}模式），TA 之后发起的短信/礼物都不会再送达你。"
        f"\n不影响你主动联系对方。拉黑满 {get_int('block_undo_hours', 2)} 小时后可用「取消拉黑 {target['name']}」解除。")


unblock_cmd = _cmd("取消拉黑")


@unblock_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await unblock_cmd.finish(NEED_ROLE)
    parts = _name_args(args)
    if not parts:
        await unblock_cmd.finish("⚠️ 格式：取消拉黑 角色名")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await unblock_cmd.finish(f"❌ 未找到角色「{parts[0]}」")
    ok, info = blocks.unblock(me["id"], target["id"])
    if ok:
        await unblock_cmd.finish(f"✅ 已取消拉黑「{target['name']}」")
    if info == "not_blocked":
        await unblock_cmd.finish(f"❓ 你并没有拉黑「{target['name']}」")
    await unblock_cmd.finish(f"⏳ 拉黑满 {get_int('block_undo_hours', 2)} 小时才能解除，还要等 {info.split(':')[1]} 分钟")


blocklist_cmd = _cmd("拉黑列表")


@blocklist_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    me = _me(event)
    if not me:
        await blocklist_cmd.finish(NEED_ROLE)
    rows = blocks.list_blocks(me["id"])
    if not rows:
        await blocklist_cmd.finish("你当前没有拉黑任何人")
    await blocklist_cmd.finish("【你拉黑的人】\n" + "\n".join(f"- {r['blocked_name']}（{'静默' if r['silent'] else '不静默'}）" for r in rows))


# ================= 管理员 =================

claim_admin = _cmd("认管理员")


@claim_admin.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    parts = _name_args(args)
    if not parts:
        await claim_admin.finish("用法：认管理员 6位管理员码（码在设置平台「管理员」页生成）")
    _, msg = admins.claim(parts[0], _bid(), _sender(event))
    await claim_admin.finish(msg)


def _admin_only(matcher_name: str):
    return f"❌ 权限不足，仅管理员可用。"


set_announce = _cmd("设为公告群")


@set_announce.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await set_announce.finish(_admin_only("设为公告群"))
    announce_core.set_announce(_bid(), event.group_openid)
    await set_announce.finish("✅ 已把这个群设为公告群：心动信的提醒、曝光、派送播报都会发到这里。")


unset_announce = _cmd("取消公告群")


@unset_announce.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await unset_announce.finish(_admin_only("取消公告群"))
    announce_core.set_announce("", "")
    await unset_announce.finish("✅ 已取消公告群。")
