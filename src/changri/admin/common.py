"""设置平台公用的东西：设置项定义、登录锁、机器人卡片等。各页面从这里 import。"""
import json
import logging
import os
import re
import secrets
import time
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, flash, g, jsonify, make_response, redirect, render_template, request, session, url_for
from markupsafe import Markup

from changri.core import admins as admins_core, lovemail as lovemail_core, relations as relations_core, roles as roles_core, webusers
from changri.core.announce import get_announce
from changri.core import forum as forum_core, sheets as sheets_core, rewards as rewards_core, godmode as god_core, msgimages as msgimages_core, npc as npc_core, alarm as alarm_core, roomnotify as roomnotify_core, arclen as arclen_core, imagemgr as imagemgr_core, gifts as gifts_core
from changri.core.lockstore import FailStore
from changri.core import autoreply as autoreply_core, botconf, qqmanager as qqmanager_core, qqpool as qqpool_core, loopbridge, netcheck, setupcode, phonecodes, extensions as ext_core, sighting as sighting_core, pool as pool_core, rooms as rooms_core, rpg as rpg_core, season as season_core, trpg as trpg_core, trpglog as trpglog_core, trpgrender as trpgrender_core, trpgexport as trpgexport_core, trpgimport as trpgimport_core, updater
from changri.core.botconf import configured_bot_ids
from changri.core import collect as collect_core
from changri.core import precall as precall_core
from changri.core import gacha as gacha_core
from changri.core import moments as moments_core
from changri.core import masquerade as masq_core
from changri.core.db import DB_PATH, current_bot, enter_bot, get_config, get_conn, init_db, leave_bot, list_bot_ids, set_config, use_bot

from changri import config as _config  # noqa: E402
from changri.paths import LEGACY_ENV  # noqa: E402

load_dotenv(LEGACY_ENV)          # 老版本 .env 里的 ADMIN_PASSWORD（只在第一次建账号时导入）

# 设置平台管的项：分组 -> {key: (显示名, 默认值)}。开关类填 1（开）/ 0（关）
SECTIONS = [
    ("基础", {
        "hello_text": ("/ping 的回复", "收到。"),
        "welcome_text": ("机器人被拉进新群时发的欢迎语（留空用默认）", ""),
        "error_report_url": ("报错接收地址（http/https 网址；留空 = 报错只存在本机。程序崩溃或出错、恢复后会自动把报错文字发到这里，不含聊天内容和群号。默认发到主站 system.changri.work）", "https://system.changri.work/api/errors"),
        "max_backup_groups": ("每个角色最多几个备用群（短信、礼物、心动信、通知默认发到角色的主群；主群发不出去时依次改发备用群。玩家在自己群里生成码就能加备用群，这里限制最多加几个）", "3"),
        "season_report": ("季末报告（1 开 / 0 关；开了以后，结束季度时会给每位有互动的玩家的个人群发一份本季小结：约会场次、短信礼物心动信的数量、互动最多的人。机器人要还在个人群里才发得出去）", "0"),
        "block_undo_hours": ("拉黑满几小时才能解除", "2"),
        "brand_name": ("系统名称（画在长图左上角，留空用默认）", ""),
        "long_as_image": ("长内容发图片（1 开 / 0 关，关了就拆成文字）", "1"),
        "image_min_chars": ("超过多少字才发图片", "200"),
        "require_prefix": ("指令必须带前缀（1 开 / 0 关；打开后所有指令都要以 / . 。 开头，不带的不响应；关闭时带不带都行）", "0"),
    }),
    ("短信", {
        "sms_cooldown_sec": ("短信冷却（秒，0 = 不限）", "60"),
        "sms_daily_limit": ("每人每天最多发几条短信（0 = 不限，按游戏天数算）", "0"),
        "msg_max_len": ("短信/留言最大字数", "200"),
        "sms_image_enabled": ("短信/礼物留言可以带图片（1 开 / 0 关；玩家在 QQ 里发带图片的消息，或在小手机里选图，对方的群和小手机里都能看到。图片存在服务器上，在「短信图片」页管理）", "1"),
        "sms_image_max_mb": ("单张图片最大几 MB", "5"),
        "allow_custom_sign": ("允许自定义署名，如「神秘人短信 张三 你好」（1 开 / 0 关，关了署名只能是自己的角色名）", "0"),
        "window_sms": ("短信与礼物开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放；礼物也按这个）", ""),
    }),
    ("礼物", {
        "gift_cooldown_sec": ("送礼冷却（秒，0 = 不限）", "60"),
        "gift_daily_limit": ("每人每天最多送几次礼（0 = 不限）", "0"),
        "gift_public_send": ("礼物公开播报：送达后按概率把「谁送了什么给谁」发到公告群（1 开 / 0 关）", "0"),
        "gift_public_chance": ("礼物公开概率（%）", "50"),
    }),
    ("约会房间", {
        "window_date": ("私约/电话/微信开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "room_dedup_similarity": ("复盘里同一个人 30 分钟内发的、和自己上一条相似度达到多少 % 的发言，只留后一条（8 字以上才算；0 = 不处理）", "90"),
        "arc_max_hours": ("最长弧长（小时）：房间里谁回得比这更慢，或正在等的回复已经超过它，「弧长与复盘」页顶上标红；0 = 不标", "12"),
        "window_forum": ("论坛开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "limit_私约": ("每人每天最多发起几次私约（0 = 不限）", "0"),
        "limit_电话": ("每人每天最多发起几次电话（0 = 不限）", "0"),
        "limit_微信": ("每人每天最多发起几次微信（0 = 不限）", "0"),
        "stakeout_enabled": ("踩点（1 开 / 0 关；开了以后玩家可以发「踩点 时间 地点」开一个只有自己的群，占时间线、要地点，结束发「结束踩点」）", "0"),
        "limit_踩点": ("每人每天最多发起几次踩点（0 = 不限）", "0"),
        "phone_open_cap": ("每人最多同时有几个没结束的电话（0 = 不限）", "5"),
        "room_expire_enabled": ("到时间强制结群（1 开 / 0 关；开了以后，房间开满下面的「结戏限时」会自动结束，群放回群池）", "0"),
        "room_expire_hours": ("结戏限时（小时，从房间开始算；要打开上面的「到时间强制结群」才生效）", "24"),
        "room_expire_warn_hours": ("结戏前提醒（小时，到时间前这么久在房间群里 @ 所有参与者，告诉大家可以收尾了；0 = 不提醒）", "4"),
        "pool_auto_free": ("房间结束后群自动放回池（1 是 / 0 否，否 = 要管理员确认群里的人清空了才放回；开着时，下次分配前会查一遍群里有没有玩家）", "1"),
    }),
    ("礼品店", {
        "gift_only_preset": ("只能送礼品店里的东西（1 是 / 0 否；开了送礼只能写礼品店的编号，不能自定义）", "0"),
    }),
    ("漂流瓶", {
        "bottle_daily_limit": ("每人每天最多扔瓶/回信几次（0 = 不限）", "3"),
    }),
    ("目击", {
        "sighting_chance": ("看见的概率（0–100，每个人对每个重叠的约会各掷一次）", "50"),
        "sighting_daily_limit": ("每人每天最多看见几次（0 = 不限）", "5"),
        "sighting_overlap_pct": ("时间重叠到较短那场的百分之几才算撞见（0–100）", "30"),
        "sighting_seen_mode": ("被看见的人收到的提示：匿名 / 点名 / 随机（填这三个字之一；随机 = 每条各一半）", "随机"),
        "sighting_notify_seen": ("要不要通知被看见的人（1 要 / 0 不要，不要就只有看见的人收到）", "1"),
        "sighting_include_ended": ("已经结束的约会也算（1 算 / 0 不算）", "0"),
    }),
    ("前置电话", {
        "precall_dial_limit": ("每人最多主动拨出（选）几次（0 = 不限）", "3"),
        "precall_total_limit": ("每人一共最多通几次电话（拨出 + 被拨；0 = 不限；对方满了别人也拨不进去）", "0"),
        "precall_mutual": ("双方共同选择才接通（1 开 / 0 关；开着时你拨了对方，对方也选了你电话才接通）", "0"),
    }),
    ("点歌", {
        "song_daily_limit": ("每人每天最多点几首歌（0 = 不限；没发到公告群的不算）", "3"),
        "song_card": ("公告群发图文卡片（1 开 / 0 关；关了只发带链接的文字）", "1"),
    }),
    ("信件", {
        "letter_daily_limit": ("每人每天最多寄几封信（0 = 不限）", "5"),
        "letter_cooldown_min": ("寄信冷却（分钟，0 = 不冷却）", "2"),
        "letter_public_send": ("信出了岔子时公开播报到公告群（1 开 / 0 关；要先开「混乱寄信」才会出岔子）", "1"),
        "letter_public_chance": ("公开播报的概率（%）", "50"),
    }),
    ("混乱寄信", {
        "chaos_typo": ("错字概率（%，0–100，下面几项一样）", "10"),
        "chaos_lose": ("丢失后半截概率（%）", "5"),
        "chaos_blackout": ("涂黑概率（%）", "10"),
        "chaos_reverse": ("句子乱序概率（%）", "5"),
        "chaos_sign": ("落款写错概率（%）", "5"),
        "chaos_mis": ("误投给别人的概率（%）", "5"),
        "chaos_torn": ("信被撕成两半的概率（%，后半页飘到第三个人手里）", "5"),
    }),
    ("晚餐", {
        "dinner_poke_cooldown_min": ("「戳你一下」冷却（分钟，0 = 不冷却）", "5"),
    }),
    ("心愿", {
        "window_wish": ("心愿开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "wish_max_concurrent": ("每人同时最多挂几条没被摘走的心愿（0 = 不限）", "0"),
        "limit_挂心愿": ("每人每天最多挂几条心愿（0 = 不限）", "0"),
        "limit_摘心愿": ("每人每天最多摘几条心愿（0 = 不限）", "0"),
        "wish_public_send": ("挂心愿时同步发到公告群（1 开 / 0 关）", "0"),
    }),
    ("心动信", {
        "lovemail_enabled": ("心动信开关（1 开 / 0 关）", "1"),
        "lovemail_default_limit": ("每人每天投稿上限（<=0 关闭投稿）", "3"),
        "lovemail_day_limits": ("按天特殊上限（写成 D0:3 D1:5，没写到的天用上面的默认上限；D 后面是游戏天数，0 = 当天不能投）", ""),
        "window_lovemail": ("心动信投稿开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "lovemail_delivery_time": ("每天派送时间（HH:MM）", "22:00"),
        "lovemail_max_len": ("单封信最大字数", "500"),
        "lovemail_expose": ("曝光开关（1 开 / 0 关）", "0"),
        "lovemail_expose_chance": ("曝光概率（%）", "10"),
        "drop_hide_receiver": ("曝光时隐藏收信人（1 是 / 0 否）", "0"),
        "daily_tip": ("每日指令提示（派送后发公告群，留空或填「关闭」= 不发）", ""),
    }),
    ("跑团骰子", {
        "dice_enabled": ("跑团骰子开关（1 开 / 0 关）", "1"),
        "dice_default_rule": ("群里默认规则（coc、dnd 或 dg＝绿色三角洲，群里可以用「切换规则」单独改）", "coc"),
        "dice_card": ("检定结果发 Markdown 小卡片（1 开 / 0 关；QQ 没开 Markdown 权限时会自动退回文字，一小时后再试）", "1"),
    }),
    ("地点", {
        "place_system_enabled": ("只能使用预设地点约会（1 开 / 0 关；开了之后，私约等约会的地点必须是「地点」页里建好的，需要钥匙的还要持有钥匙；关了随便填地点）", "0"),
        "allow_private_rooms": ("允许使用「某某的房间」（1 开 / 0 关）", "1"),
    }),
    ("房间复盘", {
        "room_auto_review": ("房间结束时自动把本场戏文（复盘）发给每个参与者（1 开 / 0 关，默认关）", "0"),
    }),
    ("关系线", {
        "rel_enabled": ("关系线开关（1 开 / 0 关）", "1"),
        "rel_alert_hours": ("关系线多少小时没人回复就在后台提醒（0 = 不提醒）", "24"),
        "max_detail_chars": ("单条拉线字数上限", "500"),
        "max_detail_count": ("单条关系线段数上限", "20"),
        "max_rel_total_chars": ("单条关系线总字数上限", "3000"),
        "max_relationships_per_user": ("每人发起关系线上限", "20"),
        "max_relationships_total": ("每人关系线总数上限（发起+收到，0 = 不限）", "0"),
    }),
]
# 外层的功能卡片已经有开关了，设置里不再重复显示（关系线、跑团骰子就是外层的开关；心动信的「信箱开放」挪到心动信管理页）
HIDDEN_FIELDS = {"rel_enabled", "dice_enabled", "lovemail_enabled"}
SETTINGS = {k: v for _, group in SECTIONS for k, v in group.items()}


log = logging.getLogger("changri.admin")

LOCK_AFTER = 5          # 连续输错几次就锁
LOCK_SECONDS = 300      # 锁多久
_fails = FailStore("admin")      # 存数据库，重启不清零
ONLINE_WITHIN = 90      # 机器人心跳多少秒内算在线


def _secret_key() -> str:
    """优先用 .env 里的 FLASK_SECRET_KEY，没有就在 data/ 下生成一个存起来，别人装好不用自己编。"""
    key = os.getenv("FLASK_SECRET_KEY", "")
    if key:
        return key
    path = DB_PATH.parent / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key)
    path.chmod(0o600)
    return key


def _csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def _bot_status() -> list[dict]:
    now = time.time()
    out = []
    for bot_id in configured_bot_ids():
        try:
            beat = float(get_config(f"heartbeat:{bot_id}", "0"))
        except ValueError:
            beat = 0
        with use_bot(bot_id), get_conn() as conn:
            groups = conn.execute("SELECT COUNT(*) FROM role_groups WHERE bot_id=? AND group_openid IS NOT NULL", (bot_id,)).fetchone()[0]
            admins_n = conn.execute("SELECT COUNT(*) FROM admins WHERE bot_id=?", (bot_id,)).fetchone()[0]
        out.append({"id": bot_id, "online": now - beat <= ONLINE_WITHIN, "seen": int(beat) if beat else 0, "groups": groups, "admins": admins_n})
    return out


def _fmt_ts(v) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(v)) if v else "—"


def _dashboard() -> dict:
    """总览页要的数据：今日通告 + 需要处理的事 + 概况。"""
    now = time.localtime()
    cur = now.tm_hour * 60 + now.tm_min
    try:
        h, m = (get_config("lovemail_delivery_time", "22:00").replace('"', "").strip() or "22:00").split(":")
        target = int(h) * 60 + int(m)
    except ValueError:
        target = 22 * 60
    fmt = lambda mins: f"{(mins % 1440) // 60:02d}:{(mins % 1440) % 60:02d}"
    pool = lovemail_core.pool_all()
    announce = get_announce()
    with get_conn() as conn:
        n_main = conn.execute("SELECT COUNT(*) FROM role_groups WHERE kind='main' AND group_openid IS NOT NULL").fetchone()[0]
        n_slots = conn.execute("SELECT COUNT(*) FROM role_groups WHERE group_openid IS NULL").fetchone()[0]
        n_roles = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
        n_rel = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        n_admins = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
        day0 = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
        n_msg_today = conn.execute("SELECT COUNT(*) FROM messages WHERE created_ts>=?", (day0,)).fetchone()[0]
        n_undelivered = conn.execute("SELECT COUNT(*) FROM messages WHERE created_ts>=? AND status!='sent' AND error NOT LIKE '被拉黑%'", (int(time.time()) - 86400,)).fetchone()[0]
    enabled = get_config("lovemail_enabled", "1") != "0"
    steps = [
        (target - 10, "截止预告", f"提醒 {n_main} 个个人群：10 分钟后截止收稿"),
        (target - 5, "公告群提醒", "公告群：邮差正在整理信箱" if announce else "还没设公告群，这条不会发出"),
        (target, "心动信派送", f"信池里 {len(pool)} 封，发给 {len({m['to_role_id'] for m in pool})} 位收信人" if pool else "信池现在是空的"),
    ]
    sheet, found_next = [], False
    for mins, name, desc in steps:
        mins %= 1440
        state = "done" if mins < cur else ("next" if not found_next else "later")
        found_next = found_next or state != "done"
        sheet.append({"time": fmt(mins), "name": name, "desc": desc, "state": state})
    todo = []
    for b in _bot_status():
        if b["id"] == current_bot() and not b["online"]:
            todo.append(("bad", f"机器人 {b['id']} 不在线", url_for("bots_page"), "看看服务有没有在运行"))
    from changri.core import httpsproxy
    if httpsproxy.active() and not httpsproxy.cert_ok(httpsproxy.subject(), timeout=2):
        todo.append(("bad", "HTTPS 证书失效了", url_for("https_page"), "玩家的小手机网址是 https，现在打不开；多半是 80 端口被关了，证书续不上"))
    if not enabled:
        todo.append(("warn", "心动信开关是关的", url_for("settings"), "玩家现在不能投信"))
    if not announce:
        todo.append(("warn", "还没设置公告群", url_for("admins_page"), "管理员在目标群里发「设为公告群」"))
    if not n_admins:
        todo.append(("warn", "还没有 QQ 管理员", url_for("admins_page"), "没有人能在群里用管理指令"))
    if n_slots:
        todo.append(("warn", f"有 {n_slots} 个群位等着认群", url_for("dashboard") + "#roles", "登记了群号，但还没人在群里发认群码"))
    if n_undelivered:
        todo.append(("warn", f"最近 24 小时有 {n_undelivered} 条消息没送达", url_for("reviews_page", tab="sms"), "收件人的群可能没授权机器人发言"))
    late = relations_core.overdue()
    if late:
        top = late[0]
        todo.append(("warn", f"有 {len(late)} 条关系线超过 {relations_core.alert_hours():g} 小时没人回复", url_for("relations_page", view="relations"),
                     f"最久的：{top['a_name']} ⇄ {top['b_name']}，{top['waiting_on']} 已经 {top['hours']:.0f} 小时没回"))
    latest = get_config("update_latest", "")
    if latest and updater.is_newer(latest, updater.current_version()):
        todo.append(("warn", f"有新版本 {latest} 可以更新", url_for("update_page"), "到「更新」页点一下就行，会备份并自动重启"))
    # 房间群：池子里空着、而且所属 QQ 号连着的，才是现在能马上分给新房间的
    pool_in = [g for g in pool_core.all_groups() if g["in_pool"]]
    free = [g for g in pool_in if g["state"] == "free"]
    n_free = sum(1 for g in free if pool_core.link_ready(g))
    notes = [f"{len(free) - n_free} 个的 QQ 号没连上" if len(free) > n_free else "",
             f"{sum(1 for g in pool_in if g['state'] == 'cleaning')} 个等人退群" if any(g["state"] == "cleaning" for g in pool_in) else ""]
    notes = "，".join(n for n in notes if n)
    facts = [("角色", n_roles), ("空闲的房间群" + (f"（{notes}）" if notes else ""), n_free), ("今天的短信和礼物", n_msg_today),
             ("待派送的心动信", len(pool)), ("关系线", n_rel)]
    return {"sheet": sheet, "todo": todo, "facts": facts, "now": time.strftime("%H:%M"), "date": time.strftime("%Y年%m月%d日")}


# 不属于任何一个机器人的页面（账号、更新、远程访问、机器人清单、登录）；其余页面都在 /b/<AppID>/ 下
GLOBAL_ENDPOINTS = {"theme_set", "wizard", "bots_erase", "login", "logout", "setup", "static", "bots_page", "bots_add", "bots_test", "bots_secret", "bots_toggle", "bots_delete",
                    "accounts_page", "accounts_generate", "accounts_reset", "accounts_disable", "accounts_password", "accounts_delete",
                    "update_page", "update_check", "update_apply", "remote_page", "remote_save", "dashboard",
                    "https_page", "https_status", "https_enable", "https_disable"}


def field_view(key: str, label: str, default: str, value: str) -> dict:
    """把「标签（说明）」形式的设置项变成界面要的样子：1/0 的变开关，数字的变数字框（带单位），其余是文本框。"""
    import re
    main, inner = label, ""
    if label.endswith("）") and "（" in label:
        i = label.rindex("（")
        main, inner = label[:i], label[i + 1:-1]
    f = {"key": key, "label": main.strip(), "hint": inner, "value": value, "kind": "text", "unit": ""}
    m = re.match(r"^1\s*(?:开|是|要|算)\s*/\s*0\s*(?:关|否|不要|不算)\s*[，,；;]?\s*(.*)$", inner)      # 「1 开 / 0 关」这类都做成开关
    if m:
        f.update(kind="bool", hint=m.group(1), checked=value.strip() not in ("", "0"))
    elif re.fullmatch(r"-?\d+", str(default).strip()) and re.fullmatch(r"-?\d*", value.strip()):
        unit = "秒" if inner.startswith("秒") else "小时" if inner.startswith("小时") else "%" if "%" in inner else ""
        hint = re.sub(r"^(秒|小时|%)\s*[，,]?\s*", "", inner) if unit else inner
        f.update(kind="number", unit=unit, hint=hint)
    return f


def _bot_label(a: dict) -> str:
    return a.get("note") or f"机器人 {a['app_id'][-4:]}"


def _bot_cards() -> list[dict]:
    """选机器人页：每个机器人一张卡片。"""
    from changri.core import season as _s
    status = {b["id"]: b for b in _bot_status()}
    cards = []
    for a in botconf.list_accounts():
        with use_bot(a["app_id"]), get_conn() as conn:
            n_roles = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
            running = _s.running()
            phone_on = get_config("phone_enabled", "0") == "1"
            from changri.core.phoneserver import port as _phone_port
            port = str(_phone_port())
        st = status.get(a["app_id"], {})
        cards.append({"id": a["app_id"], "label": _bot_label(a), "enabled": bool(a["enabled"]), "online": st.get("online", False),
                      "roles": n_roles, "season": running["name"] if running else "", "phone_on": phone_on, "phone_port": port})
    return cards
