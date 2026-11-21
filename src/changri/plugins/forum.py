"""论坛（官方扩展，可在「功能与设置」里开关，改了要重启）：发帖、回复、点赞点踩、查看、删帖删楼层。
规则和数据在 core/forum.py；后台管理页是 /forum。开放时段用设置里的 window_forum。"""
import re

import nonebot
from nonebot import on_command
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.params import CommandArg

from changri.core import admins, announce, forum, godmode, roles, windows
from changri.core.buttons import Btn
from changri.core.qqsend import reply_buttons, send_to_group
from changri.core.textutil import parse_id

NEED_ROLE = "你还没有角色。在你的个人群里发「创建新角色 名字」就能创建。"
NOT_ADMIN = "❌ 这条指令只有管理员能用。"


def _cmd(name: str, aliases: set[str] | None = None):
    return on_command(name, aliases=aliases or set(), priority=10, block=True)


def _me(bot: Bot, event: GroupMessageCreateEvent):
    return roles.find_role_for_sender(bot.self_id, event.group_openid, event.author.member_openid)


def _is_admin(bot: Bot, event: GroupMessageCreateEvent) -> bool:
    return admins.is_admin(bot.self_id, event.author.member_openid)


async def _tell_announce(text: str) -> None:
    a = announce.get_announce()
    bot = nonebot.get_bots().get(a[0]) if a else None
    if bot is not None:
        try:
            await send_to_group(bot, a[1], text)
        except Exception:  # noqa: BLE001 公告发不出去不影响发帖
            pass


def _split_sign(text: str, role_name: str) -> tuple[str, str]:
    """「署名 内容」或只有「内容」。"""
    p = text.split(None, 1)
    return (p[0], p[1]) if len(p) == 2 else (role_name, text)


post_cmd = _cmd("发帖")
reply_cmd = _cmd("回复帖子")
up_cmd = _cmd("点赞")
down_cmd = _cmd("点踩")
up_floor = _cmd("点赞楼层")
down_floor = _cmd("点踩楼层")
view_cmd = _cmd("查看帖子")


@post_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await post_cmd.finish(NEED_ROLE)
    t = args.extract_plain_text().strip()
    if not t:
        await post_cmd.finish("用法：发帖 署名 内容（只写内容就用角色名署名）\n例：发帖 路人甲 今天天气真好！")
    sign, content = _split_sign(t, me["name"])
    if len(content) > 1000:
        await post_cmd.finish("帖子太长了（最多 1000 字）。")
    closed = windows.check("forum") or godmode.check(me["id"], "forum")
    if closed:
        await post_cmd.finish(closed)
    pid = forum.new_post(me["id"], sign, content)
    await _tell_announce(f"📰 新帖 #{forum.pid(pid)}  {sign}：{content[:40]}{'…' if len(content) > 40 else ''}")
    await post_cmd.finish(f"✅ 已发帖，贴号 {forum.pid(pid)}。")


@reply_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    me = _me(bot, event)
    if not me:
        await reply_cmd.finish(NEED_ROLE)
    p = args.extract_plain_text().split(None, 1)
    n = forum.parse_post_ref(p[0]) if p else None
    if not n or len(p) < 2:
        await reply_cmd.finish("用法：回复帖子 贴号 [引用N|署名] 内容\n例：回复帖子 001 你说得对！\n　　回复帖子 001 引用2 同意\n　　回复帖子 001 路人乙 我也觉得")
    rest = p[1].strip()
    quote = 0
    m = re.match(r"^引用(\d+)\s*(.*)$", rest, re.S)
    if m:
        quote, rest = int(m[1]), m[2].strip()
    sign, content = (me["name"], rest) if m else _split_sign(rest, me["name"])
    if not content:
        await reply_cmd.finish("回复内容不能为空。")
    ok, why, floor = forum.new_reply(n, me["id"], sign, content[:1000], quote)
    await reply_cmd.finish(f"✅ 已回复 {forum.pid(n)}，你在 {floor} 楼。" if ok else f"❌ {why}")


async def _vote_post(matcher, bot, event, args, val: int):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    n = forum.parse_post_ref(args.extract_plain_text())
    if not n or not forum.get_post(n):
        await matcher.finish("用法：点赞 贴号（点踩同理）。")
    ok, why = forum.vote(f"p{n}", me["id"], val)
    up, down = forum.tally(f"p{n}")
    await matcher.finish(f"{'👍' if val > 0 else '👎'} 已记下。{forum.pid(n)}：👍{up} 👎{down}" if ok else why)


async def _vote_floor(matcher, bot, event, args, val: int):
    me = _me(bot, event)
    if not me:
        await matcher.finish(NEED_ROLE)
    p = args.extract_plain_text().split()
    n = forum.parse_post_ref(p[0]) if p else None
    floor = parse_id(p[1]) if len(p) >= 2 else None
    if not n or not floor:
        await matcher.finish("用法：点赞楼层 贴号 楼层，例：点赞楼层 001 3（点踩楼层同理）。")
    if floor == 1:
        await matcher.finish("1 楼就是主帖，直接用「点赞 贴号」。")
    rid = forum.reply_id(n, floor)
    if not rid:
        await matcher.finish(f"{forum.pid(n)} 没有第 {floor} 楼。")
    ok, why = forum.vote(f"r{rid}", me["id"], val)
    up, down = forum.tally(f"r{rid}")
    await matcher.finish(f"{'👍' if val > 0 else '👎'} 已记下。{forum.pid(n)} {floor} 楼：👍{up} 👎{down}" if ok else why)


@up_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_post(up_cmd, bot, event, args, 1)


@down_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_post(down_cmd, bot, event, args, -1)


@up_floor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_floor(up_floor, bot, event, args, 1)


@down_floor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    await _vote_floor(down_floor, bot, event, args, -1)


del_post = _cmd("删帖")
del_floor = _cmd("删楼层")


@del_post.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await del_post.finish(NOT_ADMIN)
    n = forum.parse_post_ref(args.extract_plain_text())
    await del_post.finish(f"✅ 已删除帖子 {forum.pid(n)}（连同回复和点赞）。" if n and forum.delete_post(n) else "用法：删帖 贴号（没有这个帖子也会提示这条）。")


@del_floor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not _is_admin(bot, event):
        await del_floor.finish(NOT_ADMIN)
    p = args.extract_plain_text().split()
    n = forum.parse_post_ref(p[0]) if p else None
    if not n or len(p) < 2 or not parse_id(p[1]) or not forum.delete_floor(n, parse_id(p[1])):
        await del_floor.finish("用法：删楼层 贴号 楼层（1 楼是主帖，要删整帖用「删帖」）。")
    await del_floor.finish(f"✅ 已删除 {forum.pid(n)} 的 {p[1]} 楼（楼号保留，内容改成「该楼层已被删除」）。")


@view_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    t = args.extract_plain_text().strip()
    if not t:
        ps = forum.list_posts()
        if not ps:
            await view_cmd.finish("论坛还没有帖子。")
        lines = [f"{forum.pid(p['id'])}  {p['sign']}：{p['content'][:30]}{'…' if len(p['content']) > 30 else ''}（{p['n']} 回复 👍{forum.tally('p%d' % p['id'])[0]}）" for p in ps]
        await reply_buttons(view_cmd, "📰 最近的帖子\n" + "\n".join(lines) + "\n看某个帖子发「查看帖子 贴号」",
                            [Btn(f"帖子 {forum.pid(p['id'])}", f"查看帖子 {forum.pid(p['id'])}") for p in ps], per_row=5)
        await view_cmd.finish()
    n = forum.parse_post_ref(t)
    th = forum.thread(n) if n else None
    if not th:
        await view_cmd.finish("没有这个帖子。")
    p, rs = th
    up, down = forum.tally(f"p{p['id']}")
    out = [f"📰 {forum.pid(p['id'])}  {p['sign']}  👍{up} 👎{down}", f"1楼  {p['content']}"]
    for r in rs:
        u, d = forum.tally(f"r{r['id']}")
        out.append(f"{r['floor']}楼  {r['sign']}  👍{u} 👎{d}" + (f"\n　　↳ 引用 {r['quote']}" if r["quote"] else "") + f"\n　　{r['content']}")
    n3 = forum.pid(p["id"])
    await reply_buttons(view_cmd, "\n".join(out), [Btn("点赞", f"点赞 {n3}"), Btn("点踩", f"点踩 {n3}", style="secondary"),
                                                    Btn("回复", f"回复帖子 {n3} ", enter=False, style="secondary")], per_row=3)
    await view_cmd.finish()
