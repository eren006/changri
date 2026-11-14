"""第 3 步：出场（指令）。规则在 core/entrance.py。"""
import nonebot
from nonebot import logger, on_command, on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from core import admins, entrance, roles
from plugins._long import download_images, make_send, make_send_image, reply_long

NO = "❌ 权限不足，仅管理员可用。"


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _bid() -> str:
    return current_bot.get().self_id


def _is_admin(event: GroupMessageCreateEvent) -> bool:
    return admins.is_admin(_bid(), event.author.member_openid)


async def _advance(announce_current: bool = False) -> None:
    try:
        await entrance.advance(make_send(), announce_current, make_send_image())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[出场] 发到戏群失败：{e}")


set_stage = _cmd("设为戏群")


@set_stage.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await set_stage.finish(NO)
    entrance.set_stage(_bid(), event.group_openid)
    await set_stage.finish("✅ 已把这个群设为戏群：出场会发到这里。")


unset_stage = _cmd("取消戏群")


@unset_stage.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await unset_stage.finish(NO)
    entrance.set_stage("", "")
    await unset_stage.finish("✅ 已取消戏群。")


draw_cmd = _cmd("抽取出场顺序")


@draw_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(event):
        await draw_cmd.finish(NO)
    if entrance.active() and args.extract_plain_text().strip() != "确认":
        await draw_cmd.finish("⚠️ 出场正在进行中，重抽会丢掉当前进度和已提交的内容。确认请发「抽取出场顺序 确认」。")
    rows = entrance.draw()
    if not rows:
        await draw_cmd.finish("❌ 没有可以出场的角色（NPC 不参与）。")
    text = f"出场顺序已抽取（共 {len(rows)} 人）：\n" + "\n".join(f"{i}. {r['name']}" for i, r in enumerate(rows, 1))
    text += "\n\n玩家可以在开始前就在自己的个人群发「提交出场 角色名 内容」预存，轮到时自动发到戏群。"
    await reply_long(draw_cmd, text)


start_cmd = _cmd("开始出场")


@start_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await start_cmd.finish(NO)
    ok, msg = entrance.start()
    if not ok:
        await start_cmd.finish("❌ " + msg if "进行中" not in msg else "⚠️ " + msg)
    await start_cmd.send(msg)
    await _advance(announce_current=True)


skip_cmd = _cmd("跳过出场")


@skip_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await skip_cmd.finish(NO)
    ok, msg = entrance.skip()
    await skip_cmd.send(msg if ok else "❌ " + msg)
    if ok:
        await _advance(announce_current=True)


status_cmd = _cmd("查看出场状态")


@status_cmd.handle()
async def _(event: GroupMessageCreateEvent):
    if not _is_admin(event):
        await status_cmd.finish(NO)
    await reply_long(status_cmd, entrance.status_text())


submit_cmd = _cmd("提交出场")


@submit_cmd.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    role = roles.find_role_for_sender(_bid(), event.group_openid, event.author.member_openid)
    if not role:
        await submit_cmd.finish("❌ 请先创建角色，或在自己的个人群里发。")
    text = entrance.split_name_text(args.extract_plain_text(), role["name"])
    if text is None:
        await submit_cmd.finish(f"❌ 格式不对，或名字和你的角色「{role['name']}」不符。\n正确格式：提交出场 {role['name']} 内容（可以带图片）")
    images = await download_images(args)
    ok, msg = entrance.submit(role["id"], text, images)
    await submit_cmd.send(msg if ok else "❌ " + msg)
    if ok:
        await _advance()


async def _stage_rule(bot: Bot, event: GroupMessageCreateEvent) -> bool:
    st = entrance.get_stage()
    return bool(st and entrance.active() and st == (bot.self_id, event.group_openid))


stage_msg = on_message(rule=_stage_rule, priority=60, block=False)


@stage_msg.handle()
async def _(event: GroupMessageCreateEvent):
    """出场进行中：戏群里轮到的人自己发「角色名 内容」，算他已出场，机器人不再重发，直接轮到下一位。必须本人就是当前轮到的人。"""
    cur = entrance.current()
    if not cur:
        return
    role = roles.find_role_for_sender(_bid(), event.group_openid, event.author.member_openid)
    if not role or role["id"] != cur["role_id"]:
        return
    if entrance.split_name_text(event.get_plaintext(), cur["name"]) is None:
        return
    if entrance.mark_posted(cur["role_id"]):          # 本人已经自己发在戏群了，机器人不重发
        await _advance(announce_current=True)
