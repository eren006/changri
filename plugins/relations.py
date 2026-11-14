"""扩展：关系线（拉线、确认、撤回、查看、统计）。"""
import nonebot
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.matcher import current_bot
from nonebot.params import CommandArg

from plugins._long import make_send, reply_long  # noqa: E402
from core import admins, messaging, relations, roles
from core.db import get_config, get_int

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


# ================= 关系线 =================

async def _notify(bot: Bot, role_id: int, text: str) -> bool:
    ok, _ = await messaging.push_to_role(role_id, text, _send_fn())
    return ok


add_rel = _cmd("拉线")


@add_rel.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await add_rel.finish("⚠️ 找不到你的角色登记信息，请先在个人群里「创建新角色」。")
    parts = args.extract_plain_text().strip().split(None, 1)
    if len(parts) != 2:
        await add_rel.finish("格式：拉线 对方名 内容")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await add_rel.finish("❌ 找不到角色")
    ok, hint = relations.add_detail(me["id"], target["id"], target["name"], parts[1].strip())
    if not ok:
        await add_rel.finish(hint)
    new_line, _, stat = hint.partition("\n") if hint.startswith("✨") else ("", "", hint)
    delivered = await _notify(bot, target["id"], f"📝 来自「{me['name']}」的新关系细节：\n{parts[1].strip()}\n\n（使用「查看关系线 {me['name']}」查看完整记录）")
    head = (new_line + "\n") if new_line else ""
    if delivered:
        await add_rel.finish(f"{head}✅ 细节已同步至「{target['name']}」的绑定群\n{stat}")
    await add_rel.finish(f"{head}✅ 细节已记录，但「{target['name']}」尚未绑定可用的群，无法实时同步。\n{stat}")


confirm_rel = _cmd("确认关系线")


@confirm_rel.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    parts = _name_args(args)
    target = roles.get_role_by_name(parts[0]) if parts else None
    if not me or not target or not relations.confirm(me["id"], target["id"]):
        await confirm_rel.finish("未找到该关系线")
    await confirm_rel.finish(f"✅ 你已确认与「{target['name']}」的关系线为完成状态。")


withdraw_rel = _cmd("撤回关系")


@withdraw_rel.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    parts = args.extract_plain_text().strip().split(None, 1)
    if not me or len(parts) != 2:
        await withdraw_rel.finish("格式：撤回关系 对方角色名 要撤回的内容")
    target = roles.get_role_by_name(parts[0])
    if not target:
        await withdraw_rel.finish("没有可撤回的细节记录。")
    res = relations.withdraw(me["id"], target["id"], parts[1].strip())
    if res == "":
        await withdraw_rel.finish("没有可撤回的细节记录。")
    if res is None:
        await withdraw_rel.finish(f"未找到你发送的匹配内容：「{parts[1].strip()}」\n可使用「查看关系线 {target['name']}」查看所有细节。")
    await _notify(bot, target["id"], f"🗑️ 「{me['name']}」撤回了一条发给你的关系细节：\n\"{res}\"")
    await withdraw_rel.finish(f"✅ 已成功撤回你发送的细节：\n\"{res}\"")


view_rel = _cmd("查看关系线")


@view_rel.handle()
async def _(event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(event)
    if not me:
        await view_rel.finish("请先绑定角色")
    parts = _name_args(args)
    if parts:
        target = roles.get_role_by_name(parts[0])
        rel = relations.get_rel(me["id"], target["id"]) if target else None
        if not rel:
            await view_rel.finish(f"你与「{parts[0]}」之间暂无关系记录。")
        ds = relations.details(rel["id"])
        if not ds:
            await view_rel.finish(f"你与「{target['name']}」虽有关系线，但尚未添加任何细节。")
        text = f"📖 你与「{target['name']}」的关系线（{len(ds)} 段）\n" + "\n".join(f"\n【{d['from_name']}】\n{d['text']}" for d in ds)
        await reply_long(view_rel, text)
        await view_rel.finish()
    items = relations.list_for(me["id"])
    max_rel = get_int("max_relationships_per_user", 20)
    max_total = get_int("max_relationships_total", 0)
    reply = f"📚 「{me['name']}」的关系线列表：\n"
    if not items:
        await view_rel.finish(reply + "（暂无任何记录）")
    split = get_int("forward_split_threshold", 4000)
    pending = 0
    for it in items:
        tag = "【发起】" if it["mine"] else "【收到】"
        chars = f"⚠️{it['chars']}字·将拆分" if it["chars"] > split else f"{it['chars']}字"
        pending += bool(it["pending"])
        reply += f"{'✅' if it['confirmed'] else '⏳'} {tag} 与「{it['other_name']}」({it['count']}条 | {chars}){' 🔴待回' if it['pending'] else ''}\n"
    if pending:
        reply += f"\n🔴 待回复：{pending} 条（对方发了新细节，轮到你回复）"
    reply += f"\n📊 额度占用：发起 {sum(1 for i in items if i['mine'])}/{max_rel}"
    if max_total > 0:
        reply += f"　总数 {len(items)}/{max_total}（发起+收到）"
    reply += "\n💡 输入「查看关系线 名字」查看细节"
    await view_rel.finish(reply)


rel_stats = _cmd("关系线统计")


@rel_stats.handle()
async def _(event: GroupMessageCreateEvent):
    if not admins.is_admin(_bid(), _sender(event)):
        await rel_stats.finish(_admin_only("关系线统计"))
    rows = relations.stats()
    if not rows:
        await rel_stats.finish("📭 当前没有任何关系线记录")
    await rel_stats.finish("📊 关系线数量\n" + "\n".join(f"{r['name']}：{r['n']}" for r in rows[:50]))


# ---------- 往「我的待回」里登记 ----------
from core import pending  # noqa: E402


def _pending_rels(role) -> list[str]:
    if not relations.enabled():
        return []
    return [f"关系线：「{r['other_name']}」回了你，轮到你回（查看关系线 {r['other_name']}）" for r in relations.list_for(role["id"]) if r["pending"]]


pending.register("关系线", _pending_rels)
