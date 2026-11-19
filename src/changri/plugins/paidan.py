"""排单（个性化模块）的群指令和定时任务。规则在 core/paidan.py，设计见 docs/排单设计.md。

群里能用的：
- 绑定 绑定码        —— 工作群 / 客户群（客户本人发）/ 散单群 / owner
- 下单 [字母]        —— 客户群、散单群：给业务按钮和「获取格式」
- 【A下单】…         —— 提交下单格式（必须有「QQ号：数字」）
- 图片 + 单号 / 回复图片写单号 —— 补图，转到工作群
- 已上传所有图 单号   —— 锁定上传
- 补充 单号 内容      —— 下单后补充需求（也认「补充需求」），接在下单内容后面，转到工作群
- 发送图包           —— 私聊 owner 带上群号，叫 owner 去群里拿合并转发
- 我的额度           —— 客户群：剩几张、哪天到期、在排的单
- 发送 单号（带图或回复图片）—— 工作群里 owner 交付

这个模块只有排单开着才会加载（features/paidan 的 module）。
"""
import re
import time

import nonebot
from nonebot import logger, on_command, on_message
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from changri.core import paidan as pd
from changri.core.buttons import Btn
from changri.core.db import current_bot, get_config, per_bot, set_config
from changri.core.qqsend import AT_RE, reply_buttons, send_at, send_to_group

nonebot.require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

MAX_IMAGES = 9                  # 一条消息最多转几张
_LEAD = re.compile(r"^\s*(?:<@!?\w+>\s*)*[/.。]?\s*")


def _text(event: GroupMessageCreateEvent) -> str:
    return _LEAD.sub("", event.get_plaintext() or "").strip()


def _mid(event: GroupMessageCreateEvent) -> str:
    return event.author.member_openid


# ---------- 图片 ----------

def _image_urls(event: GroupMessageCreateEvent) -> list[str]:
    """这条消息里的图，没有就看它回复的那条（msg_elements）里的图。"""
    def pick(atts):
        out = []
        for a in atts or []:
            url = getattr(a, "url", "") or ""
            if url and (getattr(a, "content_type", "") or "image").startswith("image"):
                out.append(url if url.startswith("http") else "https://" + url)
        return out
    urls = pick(getattr(event, "attachments", None))
    if not urls:
        for el in getattr(event, "msg_elements", None) or []:
            urls += pick(getattr(el, "attachments", None))
    return urls[:MAX_IMAGES]


async def _fetch(urls: list[str]) -> list[bytes]:
    """QQ 的图片链接会过期，收到就下载；下不下来的跳过。"""
    import httpx
    out = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for u in urls:
            try:
                r = await client.get(u)
                if r.status_code == 200 and 0 < len(r.content) <= 10 * 1024 * 1024:
                    out.append(r.content)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[排单] 图片下载失败：{e}")
    return out


async def _post_images(bot: Bot, gid: str, imgs: list[bytes]) -> int:
    n = 0
    for data in imgs:
        try:
            await bot.send_to_group(group_openid=gid, message=MessageSegment.file_image(data))
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[排单] 发图失败：{e}")
    return n


# ---------- 发消息 ----------

async def _say(bot: Bot, gid: str, text: str) -> None:
    """发到群；开头是 {{AT:openid:名字}} 就真的 @ 这个人。"""
    try:
        m = AT_RE.match(text)
        if m:
            await send_at(bot, gid, m.group(1), m.group(2), text[m.end():])
        else:
            await send_to_group(bot, gid, text)
    except Exception as e:  # noqa: BLE001 一条通知发不出去不能卡住下单
        logger.warning(f"[排单] 发群消息失败：{e}")


async def _dm_owner(bot: Bot, text: str) -> bool:
    """私聊 owner。官方机器人只能私聊白名单里的人；发不出去记下来，后台「设置」页提示。"""
    oid = pd.owner_openid()
    if not oid:
        return False
    try:
        await bot.send_to_c2c(openid=oid, message=text)
        if get_config("pd_dm_fail", ""):
            set_config("pd_dm_fail", "")
        return True
    except Exception as e:  # noqa: BLE001
        set_config("pd_dm_fail", time.strftime("%m/%d %H:%M ") + str(e)[:80])
        logger.warning(f"[排单] 私聊 owner 失败：{e}")
        return False


def _owner_at() -> str:
    return pd._at(pd.owner_openid(), "owner")


async def _to_work(bot: Bot, bid: int, text: str) -> None:
    """发到业务的工作群（@ owner）；工作群没绑定就私聊 owner。"""
    kind, target = pd.work_target(bid)
    if kind == "group":
        await _say(bot, target, _owner_at() + text)
    elif kind == "owner":
        await _dm_owner(bot, text)


# ---------- 只在排单自己的群里响应 ----------
# 指令不带前缀也认（「下单」「发送」开头的普通发言也会撞上），而且 block=True。
# 所以每个指令都先过这里：排单没开（多机器人时别的机器人开着也会加载本模块）、或者不是排单绑定的群，就根本不匹配，
# 不拦后面的复盘记录（play._record_official）、自定义回复、出场这些监听。

def _on(event: Event) -> bool:
    return isinstance(event, GroupMessageCreateEvent) and pd.enabled()


def _in_order_group(event: Event) -> bool:
    """客户群或散单群。"""
    return _on(event) and _where(event.group_openid)[0] != ""


def _in_client_group(event: Event) -> bool:
    return _on(event) and pd.client_by_group(event.group_openid) is not None


def _in_work_group(event: Event) -> bool:
    return _on(event) and pd.business_by_group(event.group_openid) is not None


def _where(gid: str):
    """这个群是谁的：('client', 客户) / ('sandan', None) / ('', None)。"""
    cl = pd.client_by_group(gid)
    if cl:
        return "client", cl
    if pd.is_sandan_group(gid):
        return "sandan", None
    return "", None


# ---------- 绑定 ----------

bind_cmd = on_command("绑定", rule=Rule(_on), priority=10, block=False)


@bind_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    code = args.extract_plain_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9]{6}", code):
        return                               # 不是排单的绑定码，留给别的功能
    ok, msg = pd.bind(code, event.group_openid, _mid(event))
    if ok:
        await bind_cmd.finish(msg)


# ---------- 下单 ----------

order_cmd = on_command("下单", rule=Rule(_in_order_group), priority=10, block=True)


@order_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    kind, cl = _where(event.group_openid)
    if pd.is_paused():
        await order_cmd.finish("今天暂停接单，明天恢复～")
    if kind == "client" and cl["archived"]:
        await order_cmd.finish("你的单口已经完结了，需要的话请联系 owner。")
    bizs = list(pd.client_businesses(cl["id"])) if kind == "client" else list(pd.businesses())
    if not bizs:
        await order_cmd.finish("你现在没有可用的额度，请联系 owner。" if kind == "client" else "还没有可以下单的业务。")
    want = args.extract_plain_text().strip().upper()
    pick = next((b for b in bizs if b["letter"].upper() == want), None) if want else (bizs[0] if len(bizs) == 1 else None)
    if not pick:
        text = "要下哪个业务的单？\n" + "\n".join(f"{b['letter']} · {b['name']}：发「下单 {b['letter']}」" for b in bizs)
        await reply_buttons(order_cmd, text, [Btn(f"{b['letter']} {b['name']}", f"下单 {b['letter']}") for b in bizs])
        await order_cmd.finish()
    fmt = pd.order_format(pick, sandan=kind == "sandan")
    text = (f"「{pick['name']}」下单：点「获取格式」，格式会填进输入框，填好发出就行。\n"
            "没有按钮的话复制下面这段：\n" + fmt +
            "\n\n※ 必须写 QQ号（数字），只能发文字；图片下单后再补。")
    await reply_buttons(order_cmd, text, [Btn("获取格式", fmt, enter=False)], tail="")
    await order_cmd.finish()


def _is_order_text(event: Event) -> bool:
    return _in_order_group(event) and _text(event).startswith("【") and pd.parse_order(_text(event)) is not None


submit = on_message(rule=_is_order_text, priority=9, block=True)


@submit.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    kind, cl = _where(event.group_openid)
    text = _text(event)
    p = pd.parse_order(text)
    b = pd.business_by_letter(p["letter"])
    if not b:
        await submit.finish(f"没有「{p['letter']}」这个业务，发「下单」看看有哪些。")
    err = pd.check_submission(b, p)
    if err:
        await submit.finish(err)
    if kind == "sandan" and not p["code"]:
        await submit.finish("散单要写「兑换码：」，一个码下一单。")
    o, err = pd.place_order(b["id"], text, cid=cl["id"] if cl else None, code=p["code"] if kind == "sandan" else "",
                            orderer_openid=_mid(event), orderer_name=(p["values"].get("昵称", "") or p["values"].get("名字", ""))[:20],
                            qq=p["qq"])
    if err:
        await submit.finish(err)
    body = "\n".join(ln for ln in text.splitlines()[1:] if ln.strip())
    await _to_work(bot, b["id"], f"新单 {o['no']} · {pd.who(o)}\n{body}")
    await _dm_owner(bot, pd.new_order_dm(o))
    await submit.finish(f"下单成功，单号 {o['no']}（DDL {pd._md(o['deadline'])}）。\n"
                        f"补图：发图时带上单号，或者回复图片写「{o['no']}」。\n传完发「已上传所有图 {o['no']}」。")


# ---------- 补图 ----------

def _is_upload(event: Event) -> bool:
    if not _in_order_group(event):
        return False
    t = _text(event)
    if t.startswith(("已上传所有图", "发送")) or not pd.find_no(t):
        return False
    return bool(_image_urls(event))


upload = on_message(rule=_is_upload, priority=9, block=True)


@upload.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    no = pd.find_no(_text(event))
    o, err = pd.check_upload(no, event.group_openid)
    if err:
        await upload.finish(err)
    imgs = await _fetch(_image_urls(event))
    if not imgs:
        await upload.finish("图片没收到（可能链接过期了），请重新发一次。")
    kind, target = pd.work_target(o["business_id"])
    if kind != "group":
        await upload.finish("这个业务的工作群还没绑定，图先别发，请联系 owner。")
    n = await _post_images(bot, target, imgs)
    if n:
        pd.record_upload(o["id"], n)
        await _say(bot, target, f"{o['no']} · {pd.who(o)} · 补图 {n} 张")
    await upload.finish(f"{o['no']} 收到 {n} 张图～" if n else "图没转过去，请稍后再发一次。")


lock_cmd = on_command("已上传所有图", rule=Rule(_in_order_group), priority=10, block=True)


@lock_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    no = pd.find_no(args.extract_plain_text())
    if not no:
        await lock_cmd.finish("要写单号，例：已上传所有图 A-0012")
    o, err = pd.lock_uploads(no, event.group_openid)
    if err:
        await lock_cmd.finish(err)
    await _to_work(bot, o["business_id"], f"{o['no']} · {pd.who(o)} 图已传齐，共 {o['images_in']} 张")
    await lock_cmd.finish(f"{o['no']} 已锁定，不能再传图了。还要补请联系 owner。")


note_cmd = on_command("补充", aliases={"补充需求"}, rule=Rule(_in_order_group), priority=10, block=True)


@note_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    raw = args.extract_plain_text().strip()
    no = pd.find_no(raw)
    if not no:
        await note_cmd.finish("用法：补充 单号 内容，例：补充 A-0012 背景换成蓝色\n（补图照常回复图片写单号）")
    text = re.sub(r"^\s*[A-Za-z]\s*-\s*\d{1,6}\s*[：:，,]?", "", raw, count=1).strip()
    o, err = pd.add_note(no, event.group_openid, text)
    if err:
        await note_cmd.finish(err)
    await _to_work(bot, o["business_id"], f"{o['no']} · {pd.who(o)} 补充需求：\n{text}")
    await note_cmd.finish(f"{o['no']} 已补充，owner 会看到～")


bundle_cmd = on_command("发送图包", rule=Rule(_in_order_group), priority=10, block=True)


@bundle_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    dm, reply = pd.bundle_request(event.group_openid)
    if not reply:
        await bundle_cmd.finish()
    if dm and not await _dm_owner(bot, dm):
        reply = "已记下，但没能私聊到 owner，请直接联系 owner。"
    await bundle_cmd.finish(reply)


mine_cmd = on_command("我的额度", rule=Rule(_in_client_group), priority=10, block=True)


@mine_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    cl = pd.client_by_group(event.group_openid)
    if not cl:
        await mine_cmd.finish()
    await mine_cmd.finish(pd.my_status(cl["id"]))


# ---------- 交付 ----------

send_cmd = on_command("发送", rule=Rule(_in_work_group), priority=10, block=True)


@send_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, args=CommandArg()):
    if not pd.is_owner(_mid(event)):
        await send_cmd.finish("只有 owner 能发送。")
    no = pd.find_no(args.extract_plain_text())
    if not no:
        await send_cmd.finish("用法：发图时写「发送 A-0012」，或者回复图片写「发送 A-0012」。")
    o, gid, at, name, err = pd.deliver_target(no)
    if err:
        await send_cmd.finish(err)
    urls = _image_urls(event)
    if not urls:
        await send_cmd.finish("没看到图：发图时带上「发送 单号」，或者回复那张图写「发送 单号」。")
    imgs = await _fetch(urls)
    if not imgs:
        await send_cmd.finish("图片没下载下来（可能链接过期了），请重新发一次。")
    await _say(bot, gid, pd._at(at, name) + f"{o['no']} 的图来啦～")
    n = await _post_images(bot, gid, imgs)
    if n:
        pd.record_delivery(o["id"], n)
    await send_cmd.finish(f"{o['no']} 已发送 {n} 张给 {name or pd.who(o)}。完成后到网页上扣张数。" if n else "没发出去，请稍后再试。")


# ---------- 定时：0 点暂停生效、每天提醒 ----------

def _hm(now: time.struct_time) -> str:
    return f"{now.tm_hour:02d}:{now.tm_min:02d}"


async def tick(bot: Bot, now: time.struct_time | None = None) -> list[str]:
    """每分钟跑一次（也给测试直接调）。返回做了什么（日志用）。"""
    now = now or time.localtime()
    day = time.strftime("%Y-%m-%d", now)
    did = []
    for d in pd.due_pauses(day):                          # 到了的暂停：顺延（0 点后第一次跑就做）
        res = pd.apply_pause(d)
        if res:
            did.append(f"暂停 {d} 生效：{res}")
    if pd.is_paused(day) and get_config("pd_pause_notified", "") != day and _hm(now) >= get_config("pd_pause_hm", "00:00"):
        set_config("pd_pause_notified", day)
        for cid, gid in pd.notify_groups():
            await _say(bot, gid, pd.pause_notice(cid, day))
        did.append("暂停通知")
    if get_config("pd_last_remind", "") != day and _hm(now) >= get_config("pd_remind_hm", "12:00"):
        set_config("pd_last_remind", day)
        for r in pd.quota_reminders(day):
            cl = pd.client(r["client_id"])
            await _say(bot, cl["group_openid"], pd._at(cl["member_openid"], cl["name"]) + pd.quota_reminder_text(r["quota"], r["left"]))
        for bid, entry in pd.work_reminders(day).items():
            await _to_work(bot, bid, pd.work_reminder_text(entry))
        did.append("每日提醒")
    return did


@scheduler.scheduled_job("interval", seconds=60, id="paidan_tick", max_instances=1, coalesce=True)
@per_bot
async def _tick():
    if not pd.enabled():
        return
    bot = nonebot.get_bots().get(current_bot())
    if bot is None:
        return
    try:
        did = await tick(bot)
        if did:
            logger.info(f"[排单] {'；'.join(did)}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[排单] 定时任务出错：{e}")
