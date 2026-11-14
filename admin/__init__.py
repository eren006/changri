import json
import logging
import os
import re
import secrets
import time
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, make_response, redirect, render_template, request, session, url_for
from markupsafe import Markup

from core import admins as admins_core, lovemail as lovemail_core, relations as relations_core, roles as roles_core, webusers
from core.announce import get_announce
from core import forum as forum_core
from core.lockstore import FailStore
from core import autoreply as autoreply_core, botconf, qqmanager as qqmanager_core, qqpool as qqpool_core, loopbridge, netcheck, setupcode, phonecodes, extensions as ext_core, pool as pool_core, rooms as rooms_core, rpg as rpg_core, season as season_core, trpg as trpg_core, trpglog as trpglog_core, trpgrender as trpgrender_core, trpgexport as trpgexport_core, trpgimport as trpgimport_core, updater
from core.botconf import configured_bot_ids
from core.db import DB_PATH, current_bot, enter_bot, get_config, get_conn, init_db, leave_bot, list_bot_ids, set_config, use_bot

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 设置平台管的项：分组 -> {key: (显示名, 默认值)}。开关类填 1（开）/ 0（关）
SECTIONS = [
    ("基础", {
        "hello_text": ("/ping 的回复", "收到。"),
        "sms_cooldown_sec": ("短信冷却（秒，0 = 不限）", "60"),
        "gift_cooldown_sec": ("送礼冷却（秒，0 = 不限）", "60"),
        "msg_max_len": ("短信/留言最大字数", "200"),
        "max_backup_groups": ("每个角色最多几个备用群", "3"),
        "block_undo_hours": ("拉黑满几小时才能解除", "2"),
        "brand_name": ("系统名称（画在长图左上角，留空用默认）", ""),
        "long_as_image": ("长内容发图片（1 开 / 0 关，关了就拆成文字）", "1"),
        "image_min_chars": ("超过多少字才发图片", "200"),
        "require_prefix": ("指令必须带前缀（1 开 / 0 关；打开后所有指令都要以 / . 。 开头，不带的不响应；关闭时带不带都行）", "0"),
    }),
    ("短信与礼物", {
        "sms_daily_limit": ("每人每天最多发几条短信（0 = 不限，按游戏天数算）", "0"),
        "gift_daily_limit": ("每人每天最多送几次礼（0 = 不限）", "0"),
        "allow_custom_sign": ("允许自定义署名，如「神秘人短信 张三 你好」（1 开 / 0 关，关了署名只能是自己的角色名）", "0"),
        "gift_public_send": ("礼物公开播报：送达后按概率把「谁送了什么给谁」发到公告群（1 开 / 0 关）", "0"),
        "gift_public_chance": ("礼物公开概率（%）", "50"),
        "gift_catalog": ("礼品店：可送的礼物，用顿号分开（留空 = 没有礼品店）", ""),
        "gift_only_preset": ("只能送礼品店里有的礼物（1 是 / 0 否，随便写）", "0"),
        "window_sms": ("短信与礼物开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "welcome_text": ("卡拉被拉进新群时发的欢迎语（留空用默认）", ""),
    }),
    ("约会房间", {
        "window_date": ("私约/电话/微信开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "window_wish": ("心愿开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "window_forum": ("论坛开放时段（写成 10-23 或 20:00-02:00，留空 = 全天开放）", ""),
        "limit_私约": ("每人每天最多发起几次私约（0 = 不限）", "0"),
        "limit_电话": ("每人每天最多发起几次电话（0 = 不限）", "0"),
        "limit_微信": ("每人每天最多发起几次微信（0 = 不限）", "0"),
        "phone_open_cap": ("每人最多同时有几个没结束的电话（0 = 不限）", "5"),
        "wish_max_concurrent": ("每人同时最多挂几条没被摘走的心愿（0 = 不限）", "0"),
        "wish_public_send": ("挂心愿时同步发到公告群（1 开 / 0 关）", "0"),
        "limit_挂心愿": ("每人每天最多挂几条心愿（0 = 不限）", "0"),
        "limit_摘心愿": ("每人每天最多摘几条心愿（0 = 不限）", "0"),
        "room_expire_hours": ("房间开了多久自动结束（小时，0 = 不自动结束）", "24"),
        "pool_auto_free": ("房间结束后群自动放回池（1 是 / 0 否，否 = 要管理员确认群里的人清空了才放回）", "0"),
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
        "dice_default_rule": ("群里默认规则（coc 或 dnd，群里可以用「切换规则」单独改）", "coc"),
    }),
    ("RPG 物品与地点", {
        "place_system_enabled": ("启用严格地点检查（1 开 / 0 关）", "0"),
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
        n_backup = conn.execute("SELECT COUNT(*) FROM role_groups WHERE kind='backup' AND group_openid IS NOT NULL").fetchone()[0]
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
    if not enabled:
        todo.append(("warn", "心动信开关是关的", url_for("settings"), "玩家现在不能投信"))
    if not announce:
        todo.append(("warn", "还没设置公告群", url_for("admins_page"), "管理员在目标群里发「设为公告群」"))
    if not n_admins:
        todo.append(("warn", "还没有 QQ 管理员", url_for("admins_page"), "没有人能在群里用管理指令"))
    if n_slots:
        todo.append(("warn", f"有 {n_slots} 个群位等着认群", url_for("roles_page"), "登记了群号，但还没人在群里发认群码"))
    if n_undelivered:
        todo.append(("warn", f"最近 24 小时有 {n_undelivered} 条消息没送达", url_for("messages_page"), "收件人的群可能没授权机器人发言"))
    late = relations_core.overdue()
    if late:
        top = late[0]
        todo.append(("warn", f"有 {len(late)} 条关系线超过 {relations_core.alert_hours():g} 小时没人回复", url_for("relations_page"),
                     f"最久的：{top['a_name']} ⇄ {top['b_name']}，{top['waiting_on']} 已经 {top['hours']:.0f} 小时没回"))
    latest = get_config("update_latest", "")
    if latest and updater.is_newer(latest, updater.current_version()):
        todo.append(("warn", f"有新版本 {latest} 可以更新", url_for("update_page"), "到「更新」页点一下就行，会备份并自动重启"))
    facts = [("角色", n_roles), ("个人群 / 备用群", f"{n_main} / {n_backup}"), ("今天的短信和礼物", n_msg_today),
             ("待派送的心动信", len(pool)), ("关系线", n_rel)]
    return {"sheet": sheet, "todo": todo, "facts": facts, "now": time.strftime("%H:%M"), "date": time.strftime("%Y年%m月%d日")}


# 不属于任何一个机器人的页面（账号、更新、远程访问、机器人清单、登录）；其余页面都在 /b/<AppID>/ 下
GLOBAL_ENDPOINTS = {"wizard", "bots_erase", "login", "logout", "setup", "static", "bots_page", "bots_add", "bots_test", "bots_secret", "bots_toggle", "bots_delete",
                    "accounts_page", "accounts_generate", "accounts_reset", "accounts_disable", "accounts_password", "accounts_delete",
                    "update_page", "update_check", "update_apply", "remote_page", "remote_save", "dashboard"}


def field_view(key: str, label: str, default: str, value: str) -> dict:
    """把「标签（说明）」形式的设置项变成界面要的样子：1/0 的变开关，数字的变数字框（带单位），其余是文本框。"""
    import re
    main, inner = label, ""
    if label.endswith("）") and "（" in label:
        i = label.rindex("（")
        main, inner = label[:i], label[i + 1:-1]
    f = {"key": key, "label": main.strip(), "hint": inner, "value": value, "kind": "text", "unit": ""}
    m = re.match(r"^1\s*(?:开|是)\s*/\s*0\s*(?:关|否)\s*[，,]?\s*(.*)$", inner)
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
    from core import season as _s
    status = {b["id"]: b for b in _bot_status()}
    cards = []
    for a in botconf.list_accounts():
        with use_bot(a["app_id"]), get_conn() as conn:
            n_roles = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
            running = _s.running()
            phone_on = get_config("phone_enabled", "0") == "1"
            port = get_config("phone_port", "5092")
        st = status.get(a["app_id"], {})
        cards.append({"id": a["app_id"], "label": _bot_label(a), "enabled": bool(a["enabled"]), "online": st.get("online", False),
                      "roles": n_roles, "season": running["name"] if running else "", "phone_on": phone_on, "phone_port": port})
    return cards


def create_app() -> Flask:
    init_db()
    # 兼容旧部署：还没有任何账号、但 .env 里有 ADMIN_PASSWORD，就用它建出第一个账号 admin
    legacy = os.getenv("ADMIN_PASSWORD", "")
    if webusers.count() == 0 and legacy:
        ok, err = webusers.create_user("admin", legacy, "owner")
        log.warning("已用 .env 里的 ADMIN_PASSWORD 创建主账号 admin%s，登录后请在「账号」页改密码，并从 .env 里删掉它", "" if ok else f"（失败：{err}）")

    if webusers.count() == 0:
        log.warning("首次设置：从别的电脑打开设置平台时，需要验证码 %s（也在 data/.setup_code）", setupcode.code())
    app = Flask(__name__)
    app.secret_key = _secret_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.jinja_env.filters["datetimefmt"] = lambda ts: time.strftime("%m-%d %H:%M", time.localtime(ts))
    app.jinja_env.globals["csrf"] = lambda: Markup(f'<input type="hidden" name="_csrf" value="{_csrf_token()}">')

    from .botprefix import BotPrefix
    app.wsgi_app = BotPrefix(app.wsgi_app)

    @app.before_request
    def bot_context():
        """/b/<AppID>/… 下的请求都落在这个机器人的工作区；老网址（没有前缀的机器人页面）自动带上前缀。"""
        bid = request.environ.get("changri.bot", "")
        if bid:
            if bid not in list_bot_ids(False):
                abort(404)
            g._bot_token = enter_bot(bid)
            if session.get("uid"):
                session["bot"] = bid
        elif session.get("uid") and request.endpoint and request.endpoint not in GLOBAL_ENDPOINTS and any(list_bot_ids(False)):
            ids = [i for i in list_bot_ids(False) if i]
            if request.method != "GET":      # 没带机器人前缀的写操作：不猜是哪个机器人，宁可拒绝，免得角色建到别人名下
                flash("没有选定机器人，这次操作没有执行。请先选择机器人，再从它的页面操作。")
                return redirect("/?pick=1")
            if ids:
                pick = session.get("bot") if session.get("bot") in ids else ids[0]
                return redirect(f"/b/{pick}{request.full_path.rstrip('?')}")

    @app.teardown_request
    def bot_context_end(_exc):
        tok = g.pop("_bot_token", None)
        if tok is not None:
            leave_bot(tok)

    @app.before_request
    def guard():
        for prefix, ext_id in (("/lovemail", "lovemail"), ("/relations", "relations"), ("/rpg", "rpg")):      # 扩展关了，页面网址也一并关掉
            if request.path.startswith(prefix) and not ext_core.is_enabled(ext_id):
                abort(404)
        if request.method == "POST" and not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "-")):
            abort(400)

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if webusers.count() == 0:
                return redirect(url_for("setup"))
            user = webusers.get_user(session.get("uid", 0)) if session.get("uid") else None
            if not user or user["disabled"]:      # 账号被删或被停用：已登录的会话也立刻失效
                session.clear()
                return redirect(url_for("login"))
            session["name"] = user["username"]
            session["owner"] = user["role"] == "owner"
            return view(*args, **kwargs)
        return wrapper

    def owner_required(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not session.get("owner"):
                abort(403)
            return view(*args, **kwargs)
        return wrapper

    @app.context_processor
    def inject():
        in_bot = bool(request.script_root)
        ctx = {"me": session.get("name", ""), "is_owner": bool(session.get("owner")), "disabled_nav": ext_core.disabled_nav(),
               "in_bot": in_bot, "bot_id": request.environ.get("changri.bot", ""), "bots": [], "bot_label": "",
               "root_url": lambda ep, **kw: url_for(ep, **kw)[len(request.script_root):]}
        if session.get("uid"):
            accs = botconf.list_accounts()
            ctx["bots"] = [(a["app_id"], _bot_label(a)) for a in accs]
            ctx["bot_label"] = next((_bot_label(a) for a in accs if a["app_id"] == ctx["bot_id"]), "")
        return ctx

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if webusers.count() > 0:
            return redirect(url_for("login"))
        need_code = setupcode.required_for(request.remote_addr)
        if need_code:
            setupcode.code()
        if request.method == "POST":
            if need_code and not setupcode.check(request.form.get("setup_code", "")):
                flash("验证码不对。验证码在安装完成时打印在屏幕上，也在服务器的 data/.setup_code 文件里。")
            elif request.form.get("password") != request.form.get("again"):
                flash("两次输入的密码不一样。")
            else:
                ok, err = webusers.create_user(request.form.get("username", "").strip(), request.form.get("password", ""), "owner")
                if ok:
                    setupcode.clear()
                    user = webusers.verify(request.form["username"].strip(), request.form["password"])
                    session["uid"] = user["id"]
                    return redirect(url_for("dashboard"))
                flash(err)
        return render_template("setup.html", need_code=need_code)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if webusers.count() == 0:
            return redirect(url_for("setup"))
        if request.method == "POST":
            name = request.form.get("username", "").strip()
            ip = request.remote_addr or "?"
            keys = (f"{ip}|{name.lower()}", f"{ip}|*")          # 这个账号被猜、这个 IP 在乱猜，分开记
            now = time.time()
            for k in keys:
                _, until = _fails.get(k, (0, 0.0))
                if until > now:
                    flash(f"输错太多次了，请 {int((until - now) // 60) + 1} 分钟后再试。")
                    return render_template("login.html"), 429
            user = webusers.verify(name, request.form.get("password", ""))
            if user and user["disabled"]:
                flash("这个账号已被停用，请联系主账号。")
                return render_template("login.html"), 403
            if user:
                for k in keys:
                    _fails.pop(k, None)
                session.clear()
                session["uid"] = user["id"]
                return redirect(url_for("dashboard"))
            for k in keys:                                       # 累计失败不清零：每满 5 次锁一次，越错锁越久（5 分钟 → 1 小时 → 24 小时）
                n = _fails.get(k, (0, 0.0))[0] + 1
                lock = 0 if n % LOCK_AFTER else (86400 if n >= 3 * LOCK_AFTER else 3600 if n >= 2 * LOCK_AFTER else LOCK_SECONDS)
                _fails[k] = (n, now + lock if lock else 0.0)
            flash("账号或密码不对。")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        if not request.script_root:
            from core import db as _db
            if not _db.LEGACY_SINGLE and not botconf.list_accounts() and session.get("owner"):
                return redirect(url_for("wizard"))          # 刚装好、还没接入机器人：走安装向导
            cards = _bot_cards()
            if len(cards) == 1 and not request.args.get("pick"):
                return redirect(f"/b/{cards[0]['id']}/")          # 只有一个机器人：直接进去
            if cards:
                return render_template("picker.html", cards=cards)
        return render_template("dashboard.html", d=_dashboard())

    @app.route("/settings")
    @login_required
    def settings():
        return redirect(url_for("extensions_page"))        # 设置都挪到各功能卡片里了，旧网址跳过去

    @app.post("/settings/<group>")
    @login_required
    def settings_save(group):
        keys = dict(SECTIONS).get(group)
        if keys is None:
            abort(404)
        for key in keys:
            if key in request.form:
                set_config(key, request.form[key].strip())
        flash(f"「{group}」的设置已保存，机器人马上按新设置运行")
        return redirect(url_for("extensions_page"))

    @app.route("/wizard")
    @owner_required
    def wizard():
        """安装向导：每一步的完成状态都从真实情况算出来，随时离开、随时回来接着做。"""
        accs = botconf.list_accounts()
        acc = accs[0] if accs else None
        bid = acc["app_id"] if acc else ""
        online = bool(bid) and any(b["id"] == bid and b["online"] for b in _bot_status())
        n_admins = n_roles = 0
        phone_on = False
        if bid:
            with use_bot(bid), get_conn() as conn:
                n_admins = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
                n_roles = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
                phone_on = get_config("phone_enabled", "0") == "1"
        steps = [bool(acc), online, n_admins > 0, n_roles > 0, phone_on]
        cur = next((i for i, d in enumerate(steps[:4]) if not d), 4)       # 第一个没完成的必做步骤
        return render_template("wizard.html", acc=acc, bid=bid, online=online, n_admins=n_admins, n_roles=n_roles, phone_on=phone_on,
                               steps=steps, cur=cur, ip=botconf.public_ip(), result=session.pop("bot_test", None))

    @app.route("/bots")
    @login_required
    def bots_page():
        owner = bool(session.get("owner"))
        status = {b["id"]: b for b in _bot_status()}
        accounts = []
        for a in botconf.list_accounts():
            st = status.get(a["app_id"], {"online": False, "seen": 0, "groups": 0, "admins": 0})
            accounts.append({**a, **st, "masked": botconf.mask(a["secret"])})
        return render_template("bots.html", accounts=accounts, owner=owner, ip=botconf.public_ip() if owner else "",
                               result=session.pop("bot_test", None))

    def _remember_test(app_id, secret):
        res = botconf.test_credentials(app_id, secret)
        session["bot_test"] = {**res, "app_id": app_id}
        return res

    @app.post("/bots/add")
    @owner_required
    def bots_add():
        app_id, secret = request.form.get("app_id", "").strip(), request.form.get("secret", "").strip()
        ok, err = botconf.add_account(app_id, secret, request.form.get("note", ""))
        if not ok:
            flash(err)
        else:
            res = _remember_test(app_id, secret)
            flash("已保存，机器人会在几秒内自动连接。" if res["ok"] else "已保存，但连接检查没通过，请看下面的提示。")
        return redirect(url_for("wizard") if request.form.get("next") == "wizard" else url_for("bots_page"))

    @app.post("/bots/<app_id>/test")
    @owner_required
    def bots_test(app_id):
        acc = botconf.get_account(app_id)
        if acc:
            _remember_test(app_id, acc["secret"])
        return redirect(url_for("wizard") if request.form.get("next") == "wizard" else url_for("bots_page"))

    @app.post("/bots/<app_id>/secret")
    @owner_required
    def bots_secret(app_id):
        secret = request.form.get("secret", "").strip()
        ok, err = botconf.update_secret(app_id, secret)
        if ok:
            _remember_test(app_id, secret)
            flash("密钥已更新，机器人会自动重新连接（约 10 秒）。")
        else:
            flash(err)
        return redirect(url_for("bots_page"))

    @app.post("/bots/<app_id>/toggle")
    @owner_required
    def bots_toggle(app_id):
        botconf.set_enabled(app_id, request.form.get("on") == "1")
        flash("已更新，机器人会自动跟上。")
        return redirect(url_for("bots_page"))

    @app.post("/bots/<app_id>/erase")
    @owner_required
    def bots_erase(app_id):
        """彻底删除：账号 + 这个机器人的整套数据。要原样输入 AppID 确认。"""
        from core import db as _db, phoneserver
        if request.form.get("confirm", "").strip() != app_id:
            flash("AppID 没对上，没有删除。")
            return redirect(url_for("bots_page"))
        phoneserver.stop(app_id)
        botconf.delete_account(app_id)
        gone = _db.erase_workspace(app_id)
        flash(f"已彻底删除机器人 {app_id}" + ("和它的全部数据。" if gone else "（它本来就没有数据）。") + "这一步不能撤销。")
        return redirect(url_for("bots_page"))

    @app.post("/bots/<app_id>/delete")
    @owner_required
    def bots_delete(app_id):
        botconf.delete_account(app_id)
        flash("已移除这个机器人。它在群里绑定的角色和数据还保留着。")
        return redirect(url_for("bots_page"))

    @app.route("/accounts")
    @login_required
    def accounts_page():
        return render_template("accounts.html", users=webusers.list_users() if session.get("owner") else [], my_id=session["uid"],
                               cred=session.pop("new_cred", None))

    @app.post("/accounts/generate")
    @owner_required
    def accounts_generate():
        username, password = webusers.generate_admin(request.form.get("note", "").strip())
        session["new_cred"] = {"username": username, "password": password}
        return redirect(url_for("accounts_page"))

    @app.post("/accounts/<int:user_id>/reset")
    @owner_required
    def accounts_reset(user_id):
        user = webusers.get_user(user_id)
        password = webusers.reset_password(user_id) if user and user["role"] != "owner" else None
        if password:
            session["new_cred"] = {"username": user["username"], "password": password}
        else:
            flash("不能重置这个账号的密码。")
        return redirect(url_for("accounts_page"))

    @app.post("/accounts/<int:user_id>/disable")
    @owner_required
    def accounts_disable(user_id):
        off = request.form.get("off") == "1"
        ok, err = webusers.set_disabled(user_id, off)
        flash(("账号已停用，对方马上无法继续使用。" if off else "账号已恢复。") if ok else err)
        return redirect(url_for("accounts_page"))

    @app.post("/accounts/password")
    @login_required
    def accounts_password():
        if request.form.get("new") != request.form.get("again"):
            flash("两次输入的新密码不一样。")
        else:
            ok, err = webusers.change_password(session["uid"], request.form.get("old", ""), request.form.get("new", ""))
            flash("密码已修改" if ok else err)
        return redirect(url_for("accounts_page"))

    @app.post("/accounts/<int:user_id>/delete")
    @owner_required
    def accounts_delete(user_id):
        ok, err = webusers.delete_user(user_id, session["uid"])
        flash("账号已删除" if ok else err)
        return redirect(url_for("accounts_page"))

    # ---------- 在线更新（只有主账号） ----------
    @app.route("/update")
    @owner_required
    def update_page():
        failed = DB_PATH.parent / "update_failed.txt"
        return render_template("update.html", current=updater.current_version(), info=session.pop("update_info", None),
                               done=request.args.get("done", ""), failed=failed.read_text(encoding="utf-8") if failed.exists() else "")

    @app.post("/update/check")
    @owner_required
    def update_check():
        try:
            session["update_info"] = updater.check()
            set_config("update_latest", session["update_info"]["version"] if session["update_info"]["newer"] else "")
        except updater.UpdateError as e:
            flash(str(e))
        return redirect(url_for("update_page"))

    @app.post("/update/apply")
    @owner_required
    def update_apply():
        try:
            res = updater.update_now()
        except updater.UpdateError as e:
            flash(str(e))
            return redirect(url_for("update_page"))
        except Exception as e:  # noqa: BLE001
            log.exception("更新失败")
            flash(f"更新失败：{type(e).__name__}。原来的版本没有被改动。")
            return redirect(url_for("update_page"))
        set_config("update_latest", "")
        (DB_PATH.parent / "update_failed.txt").unlink(missing_ok=True)
        updater.restart()
        return redirect(url_for("update_page", done=res["to"]))

    @app.route("/roles")
    @login_required
    def roles_page():
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM roles ORDER BY id").fetchall()
        items = [{"role": r, "groups": roles_core.list_groups(r["id"])} for r in rows]
        return render_template("roles.html", items=items, code=request.args.get("code", ""), code_role=request.args.get("role", ""), code_kind=request.args.get("kind", ""))

    @app.post("/roles/<int:role_id>/slot")
    @login_required
    def add_slot(role_id):
        ok, res = roles_core.add_slot(role_id, request.form.get("real_gid", "").strip(), request.form.get("kind", "backup"))
        if not ok:
            flash(res)
            return redirect(url_for("roles_page"))
        with get_conn() as conn:
            name = conn.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()["name"]
        flash("群位已登记，请到那个群里发认群码")
        return redirect(url_for("roles_page", code=res, role=name, kind=request.form.get("kind", "backup")))

    @app.post("/roles/<int:role_id>/slot/<int:slot_id>/delete")
    @login_required
    def delete_slot(role_id, slot_id):
        with get_conn() as conn:
            conn.execute("DELETE FROM role_groups WHERE id=? AND role_id=? AND kind='backup'", (slot_id, role_id))
        return redirect(url_for("roles_page"))

    @app.post("/roles/<int:role_id>/delete")
    @login_required
    def delete_role(role_id):
        roles_core.delete_role(role_id)
        flash("角色已删除")
        return redirect(url_for("roles_page"))

    @app.route("/messages")
    @login_required
    def messages_page():
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT m.*, f.name AS from_name, t.name AS to_name FROM messages m "
                "JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id ORDER BY m.id DESC LIMIT 100"
            ).fetchall()
        return render_template("messages.html", rows=rows)

    @app.template_filter("ts")
    def _ts_filter(v):
        import time as _t
        return _t.strftime("%m-%d %H:%M", _t.localtime(v)) if v else "—"

    # ---------- 跑团角色库 ----------
    @app.route("/trpg")
    @login_required
    def trpg_page():
        q = request.args.get("q", "").strip().lower()
        players = trpg_core.players_overview()
        if q:
            players = [p for p in players if q in p["display"].lower() or any(q in c["name"].lower() for c in p["cards"])]
        return render_template("trpg.html", players=players, q=q, total=len(trpg_core.players_overview()))

    @app.route("/trpg/card/<int:card_id>")
    @login_required
    def trpg_card_page(card_id):
        card = trpg_core.get_card(card_id)
        if not card:
            flash("没有这张角色卡")
            return redirect(url_for("trpg_page"))
        attrs = trpg_core.list_attrs(card_id)
        return render_template("trpg_card.html", card=card, owner=trpg_core.card_owner(card_id), attrs=attrs,
                               history=trpg_core.card_history(card_id), battles=trpglog_core.logs_of_card(card_id), title=trpg_core.dice.RULE_TITLE.get(card["rule"], card["rule"]))

    # ---------- QQ 号监听（群号池）----------
    @app.route("/qqlink")
    @login_required
    def qqlink_page():
        from core.db import current_bot
        qqpool_core.ensure_links()
        st = qqmanager_core.status(current_bot())
        link_rows, live, errs = [], [], []
        for ln in qqpool_core.links():
            ls = qqmanager_core.link_status(current_bot(), ln["id"])
            link_rows.append({**ln, "st": ls, "used": qqpool_core.link_usage(ln["id"]), "groups": len(qqpool_core.link_groups(ln["id"]))})
            if ls["connected"]:
                try:
                    for g in loopbridge.run(qqmanager_core.group_list(current_bot(), ln["id"]), timeout=15):
                        live.append({**g, "link_id": ln["id"], "link_name": ln["name"]})
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{ln['name']}：{e or '取群列表失败'}")
        live_err = "；".join(errs)
        in_sets = {}
        sets = qqpool_core.sets(current_bot())
        for x in sets:
            for g in x["groups"]:
                in_sets[g["real_gid"]] = x["name"]
        names = {str(g.get("group_id")): g.get("group_name", "") for g in live}
        multi = len(link_rows) > 1
        tpl = get_config("qqlink_room_name", "auto")
        if tpl == "{kind}·{names}":
            tpl = "auto"
        sample = {"kind": "私约", "id": 12, "day": 3, "start_hm": "20:00", "end_hm": "21:00", "place": "海边咖啡馆"}
        presets = list(qqpool_core.ROOM_NAME_PRESETS)
        return render_template("qqlink.html", conf=qqpool_core.conf(), pool=[g for g in qqpool_core.list_groups() if g["kind"] != "room"], kinds=qqpool_core.KINDS,
                               st=st, link_rows=link_rows, multi=multi, has_token=bool(get_config("qqlink_token", "")), skip="\n".join(sorted(qqpool_core.skip_users())),
                               tpl=tpl, presets=presets, is_preset=tpl in {t for t, _ in qqpool_core.ROOM_NAME_PRESETS},
                               preview=qqpool_core.room_name(sample, ["小明", "小红"], tpl), free_name=get_config("qqlink_free_name", "空房间"),
                               sets=sets, live=live, live_err=live_err, in_sets=in_sets, names=names,
                               auth="\n".join(sorted(qqpool_core.auth_qqs())),
                               binds=roles_core.qq_bindings(), role_names=[r["name"] for r in roles_core.list_all()], trpg_binds=qqpool_core.trpg_bindings())

    @app.post("/qqlink/sets/add")
    @login_required
    def qqlink_sets_add():
        name = (request.form.get("new_set", "").strip() or request.form.get("pick_set", "").strip())
        gids = request.form.getlist("gid")                       # 每项是「QQ号连接编号:群号」
        if not gids:
            flash("先勾选要加的群")
            return redirect(url_for("qqlink_page"))
        bad = []
        for item in gids:
            lid, _, gid = item.partition(":")
            err = qqpool_core.join_set(current_bot(), gid, name, int(lid) if lid.isdigit() else 0)
            if err:
                bad.append(f"{gid}：{err}")
        flash(f"已把 {len(gids) - len(bad)} 个群加进群号组「{name}」" + (("；没加成：" + "；".join(bad)) if bad else ""))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/sets/rename")
    @login_required
    def qqlink_sets_rename():
        old, new = request.form.get("old", ""), request.form.get("new", "")
        err = qqpool_core.rename_set(current_bot(), old, new)
        flash(err or f"群号组已改名为「{new.strip()}」")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/sets/toggle")
    @login_required
    def qqlink_sets_toggle():
        name, on = request.form.get("name", ""), request.form.get("on") == "1"
        err = qqpool_core.set_enabled(current_bot(), name, on)
        flash(err or (f"群号组「{name}」已开启使用" if on else f"群号组「{name}」已关闭，不再分配房间"))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/sets/remove/<gid>")
    @login_required
    def qqlink_sets_remove(gid):
        err = qqpool_core.leave_set(current_bot(), gid)
        flash(err or "已移出群号组")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/auth")
    @login_required
    def qqlink_auth():
        qqpool_core.save_auth(request.form.get("auth", ""))
        flash("已保存")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/conf")
    @login_required
    def qqlink_conf():
        from core.db import current_bot
        err = qqpool_core.save_conf(request.form.get("url", ""), request.form.get("token") or None, bool(request.form.get("on")))
        if err:
            flash(err)
        else:
            loopbridge.run(qqmanager_core.restart(current_bot()))
            flash("设置已保存，连接已按新设置重启")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/link/add")
    @login_required
    def qqlink_link_add():
        from core.db import current_bot
        lid, err = qqpool_core.add_link(request.form.get("name", ""), request.form.get("url", ""), request.form.get("token", ""), bool(request.form.get("on")))
        if err:
            flash(err)
        else:
            loopbridge.run(qqmanager_core.restart(current_bot()))
            flash("已添加 QQ 号连接")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/link/<int:lid>/save")
    @login_required
    def qqlink_link_save(lid):
        from core.db import current_bot
        err = qqpool_core.update_link(lid, request.form.get("name", ""), request.form.get("url", ""), request.form.get("token") or None, bool(request.form.get("on")))
        if err:
            flash(err)
        else:
            loopbridge.run(qqmanager_core.restart(current_bot()))
            flash("已保存，连接已按新设置重启")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/link/<int:lid>/delete")
    @login_required
    def qqlink_link_delete(lid):
        from core.db import current_bot
        err = qqpool_core.delete_link(current_bot(), lid)
        if not err:
            qqmanager_core.stop_link(current_bot(), lid)
        flash(err or "已删除这个 QQ 号连接，它名下的群也一起移出了群号池")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/skip")
    @login_required
    def qqlink_skip():
        qqpool_core.save_skip(request.form.get("skip", ""))
        flash("已保存")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/bind")
    @login_required
    def qqlink_bind():
        name, qq = request.form.get("role", "").strip(), request.form.get("qq", "").strip()
        role = roles_core.get_role_by_name(name)
        err = "没有叫这个名字的角色" if not role else roles_core.bind_qq(role["id"], qq)
        flash(err or f"已把 QQ {qq} 认到「{name}」名下")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/unbind/<qq>")
    @login_required
    def qqlink_unbind(qq):
        done = roles_core.unbind_qq(qq) | qqpool_core.unbind_trpg(qq)
        flash("已解除绑定" if done else "没有这个绑定")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/rooms")
    @login_required
    def qqlink_rooms_add():
        ok, bad = qqpool_core.add_rooms(current_bot(), request.form.get("gids", ""), request.form.get("set_name", "").strip())
        flash(f"已加 {ok} 个房间群" + (("；没加成：" + "；".join(bad)) if bad else ""))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/roomname")
    @login_required
    def qqlink_roomname():
        pick = request.form.get("preset", "")
        tpl = request.form.get("custom", "").strip() if pick == "custom" else pick
        set_config("qqlink_room_name", tpl or "auto")
        set_config("qqlink_free_name", request.form.get("free_name", "").strip() or "空房间")
        flash("已保存")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group")
    @login_required
    def qqlink_group_add():
        err = qqpool_core.add_group(request.form.get("gid", ""), request.form.get("note", ""), request.form.get("kind", "listen"))
        flash(err or "已加进群号池")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group/<gid>/edit")
    @login_required
    def qqlink_group_edit(gid):
        ok = qqpool_core.update_group(gid, request.form.get("note", ""), request.form.get("kind", "listen"), request.form.get("group_openid", ""))
        flash("已保存" if ok else "没有这个群")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group/<gid>/delete")
    @login_required
    def qqlink_group_delete(gid):
        flash("已从群号池移除" if qqpool_core.remove_group(gid) else "没有这个群")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group/<gid>/rename")
    @login_required
    def qqlink_group_rename(gid):
        from core.db import current_bot
        err = loopbridge.run(qqmanager_core.rename(current_bot(), gid, request.form.get("name", "")))
        flash(err or "群名已改")
        return redirect(url_for("qqlink_page"))

    # ---------- 房间复盘：所有人的房间记录 ----------
    def _review_filters():
        return {k: request.args.get(k, "").strip() for k in ("role", "kind", "q")}

    @app.route("/reviews")
    @login_required
    def reviews_page():
        from core import roomlog
        f = _review_filters()
        with get_conn() as conn:
            names = [r["name"] for r in conn.execute("SELECT name FROM roles ORDER BY name")]
        evts = roomlog.events(**f)
        return render_template("reviews.html", f=f, names=names, days=roomlog.day_summary(evts), total=len(evts))

    @app.route("/reviews/day/<label>")
    @login_required
    def review_day(label):
        from core import roomlog
        f = _review_filters()
        with get_conn() as conn:
            names = [r["name"] for r in conn.execute("SELECT name FROM roles ORDER BY name")]
        evts = roomlog.events(**f)
        if label != "all":
            evts = [e for e in evts if e["day_label"] == label]
        return render_template("review_day.html", f=f, names=names, label=label, evts=evts,
                               status_text={"open": "进行中", "ended": "已结束", "cancelled": "已取消", "pending": "待送达", "sent": "已送达", "failed": "没送到"})

    @app.route("/reviews/<int:rid>")
    @login_required
    def review_view(rid):
        from core import roomlog
        room = rooms_core.get(rid)
        if not room:
            abort(404)
        from core.overview import slot_text
        return render_template("review_view.html", room=room, slot=slot_text(room), who=[m["name"] for m in rooms_core.members(rid)],
                               lines=roomlog.lines(rid), stats=roomlog.stats(rid),
                               status_text={"open": "进行中", "ended": "已结束", "cancelled": "已取消"}[room["status"]])

    @app.route("/reviews/<int:rid>/export")
    @login_required
    def review_export(rid):
        from flask import Response
        from core import roomlog
        text = roomlog.export_text(rid)
        if not text:
            abort(404)
        return Response(text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=R{rid}.txt"})

    # ---------- 跑团日志 ----------
    @app.route("/trpglog")
    @login_required
    def trpglog_page():
        with get_conn() as conn:
            logs = [dict(x) for x in conn.execute(
                "SELECT l.*, (SELECT COUNT(*) FROM trpg_log_lines WHERE log_id=l.id) AS n FROM trpg_logs l ORDER BY l.id DESC")]
        return render_template("trpglog.html", logs=logs, rules=trpglog_core.rules(), status=trpglog_core.STATUS_TEXT,
                               prefixes=get_config("trpg_log_skip_prefixes", trpglog_core.DEFAULT_PREFIXES))

    @app.post("/trpglog/rules")
    @login_required
    def trpglog_rules():
        set_config("trpg_log_skip_brackets", "1" if request.form.get("brackets") else "0")
        set_config("trpg_log_skip_cmds", "1" if request.form.get("cmds") else "0")
        set_config("trpg_log_skip_prefixes", "\n".join(x.strip() for x in request.form.get("prefixes", "").splitlines() if x.strip()))
        flash("记录规则已保存，对之后新记的内容生效")
        return redirect(url_for("trpglog_page"))

    @app.route("/trpglog/<int:log_id>")
    @login_required
    def trpglog_view(log_id):
        log = trpglog_core.get(log_id)
        if not log:
            flash("没有这个 log")
            return redirect(url_for("trpglog_page"))
        ls = trpglog_core.lines(log_id)
        style = trpgrender_core.parse_style(log["palette"])
        cmap = trpgrender_core.color_map(ls, style)
        return render_template("trpglog_view.html", log=log, stats=trpglog_core.stats(log_id), status=trpglog_core.STATUS_TEXT,
                               style=style, themes=trpgrender_core.THEMES, colors=[(n, c[0]) for n, c in cmap.items()],
                               preview=trpgrender_core.render_html(log, ls, style), url=trpglog_core.share_url(log))

    @app.post("/trpglog/<int:log_id>/style")
    @login_required
    def trpglog_style(log_id):
        log = trpglog_core.get(log_id)
        if not log:
            return redirect(url_for("trpglog_page"))
        ls = trpglog_core.lines(log_id)
        defaults = {n: c[0] for n, c in trpgrender_core.color_map(ls, trpgrender_core.parse_style("{}")).items()}
        colors = {}
        for i, n in enumerate(trpgrender_core.speakers(ls)):
            c = request.form.get(f"color_{i}", "")
            if c and c.lower() != defaults[n].lower():
                colors[n] = c
        trpglog_core.save_style(log_id, {"theme": request.form.get("theme", "auto"), "colors": colors,
                                          "dice_hl": bool(request.form.get("dice_hl")), "dim_bot": bool(request.form.get("dim_bot"))})
        flash("样式已保存，分享页和下载的文件都会用这套")
        return redirect(url_for("trpglog_view", log_id=log_id))

    @app.route("/trpglog/<int:log_id>/download/<fmt>")
    @login_required
    def trpglog_download(log_id, fmt):
        from urllib.parse import quote
        log = trpglog_core.get(log_id)
        if not log or fmt not in ("zip", "html", "txt", "json", "summary"):
            abort(404)
        data, fname, mime = trpgexport_core.build(log, fmt)
        resp = make_response(data)
        resp.headers["Content-Type"] = mime
        resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(fname)}"
        return resp

    @app.post("/trpglog/<int:log_id>/import")
    @login_required
    def trpglog_import(log_id):
        log = trpglog_core.get(log_id)
        if not log:
            return redirect(url_for("trpglog_page"))
        up = request.files.get("file")
        raw = up.read(trpgimport_core.MAX_BYTES + 1) if up and up.filename else request.form.get("text", "").encode()
        if len(raw) > trpgimport_core.MAX_BYTES:
            flash("文件太大了，最多 2MB")
        else:
            entries = trpgimport_core.parse(trpgimport_core.decode(raw))
            if not entries:
                flash("没有认出聊天记录的格式。需要每条消息前有一行「2026-11-14 21:03:11 昵称」或「昵称(2026-11-14 21:03:11)」。")
            else:
                skip = {x.strip() for x in re.split(r"[,，\n]", request.form.get("skip", "")) if x.strip()}
                r = trpgimport_core.import_into(log_id, entries, skip)
                flash(f"导入完成：新增 {r['added']} 条，重复 {r['dup']} 条，跳过 {r['skipped']} 条（机器人昵称或不记录规则）")
        return redirect(url_for("trpglog_view", log_id=log_id))

    @app.post("/trpglog/<int:log_id>/share")
    @login_required
    def trpglog_share(log_id):
        log = trpglog_core.get(log_id)
        if log:
            on = bool(request.form.get("on"))
            trpglog_core.mark_shared(log_id, on)
            flash("已公开分享链接" if on else "已关闭分享链接（旧链接立刻失效）")
        return redirect(url_for("trpglog_view", log_id=log_id))

    @app.post("/trpglog/<int:log_id>/delete")
    @login_required
    def trpglog_delete(log_id):
        trpglog_core.delete(log_id)
        flash("已删除")
        return redirect(url_for("trpglog_page"))

    @app.post("/trpg/card/<int:card_id>/attr")
    @login_required
    def trpg_card_attr(card_id):
        if not trpg_core.get_card(card_id):
            return redirect(url_for("trpg_page"))
        items = trpg_core.parse_st(request.form.get("text", ""))
        if not items:
            flash("格式：力量60 或 力量+5（可以一次写多个）")
        else:
            ok, lines = trpg_core.apply_st(card_id, items, source="后台手改")
            flash("；".join(lines) if ok else lines[0])
        return redirect(url_for("trpg_card_page", card_id=card_id))

    @app.post("/trpg/card/<int:card_id>/attr/delete")
    @login_required
    def trpg_card_attr_delete(card_id):
        name = request.form.get("name", "")
        flash("已删除" if trpg_core.delete_attr(card_id, name, source="后台手改") else "没有这个属性")
        return redirect(url_for("trpg_card_page", card_id=card_id))

    # ---------- 物品、属性、地点与商城 ----------
    @app.route("/rpg")
    @login_required
    def rpg_page():
        with get_conn() as conn:
            role_rows = [dict(x) for x in conn.execute("SELECT id,name FROM roles ORDER BY name")]
            key_rows = {(x["role_id"], x["place_id"]) for x in conn.execute("SELECT * FROM rpg_place_keys")}
        return render_template("rpg.html", defs=rpg_core.attr_defs(), items=rpg_core.items(), shop=rpg_core.listings(),
                               places=rpg_core.places(), roles=role_rows, keys=key_rows)

    @app.post("/rpg/attr")
    @login_required
    def rpg_attr_save():
        ok, msg = rpg_core.save_attr_def(request.form.get("name", ""), request.form.get("min"), request.form.get("max"),
                                         request.form.get("default", 0), request.form.get("description", ""))
        flash("属性已保存" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/item")
    @login_required
    def rpg_item_save():
        ok, msg = rpg_core.save_item(request.form.get("code", ""), request.form.get("name", ""), request.form.get("kind", "item"),
                                     request.form.get("description", ""), request.form.get("effects", ""), request.form.get("max_uses", -1),
                                     bool(request.form.get("can_give")))
        flash("物品已保存" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/inventory")
    @login_required
    def rpg_inventory_adjust():
        ok, msg = rpg_core.adjust(int(request.form.get("role_id", 0)), request.form.get("item", ""), request.form.get("delta", 0), "后台调整")
        flash("背包已调整" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/shop")
    @login_required
    def rpg_shop_save():
        ok, msg = rpg_core.set_listing(request.form.get("item", ""), request.form.get("price", 0), request.form.get("currency", ""))
        flash("已上架" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/shop/<code>/delete")
    @login_required
    def rpg_shop_delete(code):
        with get_conn() as conn:
            conn.execute("DELETE FROM rpg_shop WHERE item_code=?", (code,))
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/place")
    @login_required
    def rpg_place_save():
        ok, msg = rpg_core.save_place(request.form.get("name", ""), request.form.get("description", ""), bool(request.form.get("locked")))
        flash("地点已保存" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/key")
    @login_required
    def rpg_key_save():
        rpg_core.set_key(int(request.form.get("role_id", 0)), int(request.form.get("place_id", 0)), bool(request.form.get("on")))
        return redirect(url_for("rpg_page"))

    @app.route("/lovemail")
    @login_required
    def lovemail_page():
        return render_template("lovemail.html", pool=lovemail_core.pool_all(), announce=get_announce(),
                               at=get_config("lovemail_delivery_time", "22:00"), last=get_config("lovemail_last_run", "—"),
                               pending_now=get_config("lovemail_deliver_now", "0") == "1", open_=get_config("lovemail_enabled", "1") != "0")

    @app.post("/lovemail/open")
    @login_required
    def lovemail_open():
        set_config("lovemail_enabled", "1" if request.form.get("on") else "0")
        return redirect(url_for("lovemail_page"))

    @app.post("/lovemail/deliver")
    @login_required
    def lovemail_deliver():
        set_config("lovemail_deliver_now", "1")   # 机器人进程每 30 秒看一次这个标记
        flash("已请求立即派送，机器人会在 30 秒内处理")
        return redirect(url_for("lovemail_page"))

    @app.post("/lovemail/<int:mail_id>/delete")
    @login_required
    def lovemail_delete(mail_id):
        with get_conn() as conn:
            conn.execute("DELETE FROM lovemails WHERE id=? AND status='pool'", (mail_id,))
        return redirect(url_for("lovemail_page"))

    @app.route("/relations")
    @login_required
    def relations_page():
        with get_conn() as conn:
            rels = conn.execute(
                "SELECT x.*, a.name AS a_name, b.name AS b_name, i.name AS init_name, "
                "(SELECT COUNT(*) FROM rel_details d WHERE d.rel_id=x.id) AS n, "
                "(SELECT COALESCE(SUM(LENGTH(text)),0) FROM rel_details d WHERE d.rel_id=x.id) AS chars "
                "FROM relations x JOIN roles a ON a.id=x.role_lo JOIN roles b ON b.id=x.role_hi JOIN roles i ON i.id=x.initiator_id "
                "ORDER BY x.id DESC").fetchall()
        waits = {w["rel_id"]: w for w in relations_core.waiting_lines()}
        return render_template("relations.html", rels=rels, stats=relations_core.stats(), waits=waits, late=relations_core.overdue(),
                               limit=relations_core.alert_hours())

    @app.post("/relations/alert")
    @login_required
    def relations_alert():
        try:
            hours = max(0.0, float(request.form.get("hours", "0") or 0))
        except ValueError:
            flash("请填数字，0 表示不提醒")
            return redirect(url_for("relations_page"))
        set_config("rel_alert_hours", f"{hours:g}")
        flash("已保存：" + (f"超过 {hours:g} 小时没回复会提醒" if hours else "不再提醒"))
        return redirect(url_for("relations_page"))

    @app.post("/relations/<int:rel_id>/delete")
    @login_required
    def relations_delete(rel_id):
        relations_core.delete_rel(rel_id)
        flash("关系线已删除")
        return redirect(url_for("relations_page"))

    @app.route("/forum")
    @login_required
    def forum_page():
        with get_conn() as conn:
            posts = conn.execute("SELECT p.*, r.name AS real_name, (SELECT COUNT(*) FROM replies WHERE post_id=p.id) AS n FROM posts p "
                                 "JOIN roles r ON r.id=p.role_id ORDER BY p.id DESC LIMIT 100").fetchall()
            replies = conn.execute("SELECT x.*, r.name AS real_name FROM replies x JOIN roles r ON r.id=x.role_id ORDER BY x.post_id DESC, x.floor").fetchall()
        by_post: dict[int, list] = {}
        for x in replies:
            by_post.setdefault(x["post_id"], []).append(x)
        return render_template("forum.html", posts=posts, by_post=by_post, pid=forum_core.pid)

    @app.post("/forum/<int:post_id>/delete")
    @login_required
    def forum_delete_post(post_id):
        forum_core.delete_post(post_id)
        flash("帖子已删除")
        return redirect(url_for("forum_page"))

    @app.post("/forum/<int:post_id>/<int:floor>/delete")
    @login_required
    def forum_delete_floor(post_id, floor):
        forum_core.delete_floor(post_id, floor)
        flash("楼层已删除")
        return redirect(url_for("forum_page"))

    @app.route("/remote")
    @login_required
    def remote_page():
        from admin.remote import parse_allow
        return render_template("remote.html", on=get_config("remote_admin_enabled", "0") == "1", ips=get_config("remote_admin_ips", ""),
                               port=os.getenv("PORT", "5091"), count=len(parse_allow(get_config("remote_admin_ips", ""))),
                               ip=netcheck.public_ip_cached())

    @app.post("/remote/save")
    @login_required
    def remote_save():
        from admin.remote import parse_allow
        ips = request.form.get("ips", "").strip()
        want = bool(request.form.get("on"))
        if want and not parse_allow(ips):
            flash("要先填至少一个允许的 IP，否则不能开启（名单为空 = 谁都进不来）")
            return redirect(url_for("remote_page"))
        set_config("remote_admin_ips", ips)
        set_config("remote_admin_enabled", "1" if want else "0")
        flash("已保存，马上生效（后台端口不变）")
        return redirect(url_for("remote_page"))

    @app.route("/phone")
    @login_required
    def phone_page():
        from phone.themes import PHONE_COPY, PHONE_THEMES
        from core import phoneserver
        port = int(get_config("phone_port", "") or 0) or phoneserver.suggest_port()      # 第一次开：建议一个没被占的端口
        try:
            last = json.loads(get_config("phone_net_last", "") or "null")
        except ValueError:
            last = None
        try:
            theme = json.loads(get_config("phone_theme_config", "") or "{}")
        except ValueError:
            theme = {}
        return render_template("phone.html", port=port, lights=netcheck.lights(port, last), last=last, codes=phonecodes.listing(),
                               base=get_config("phone_base_url", ""), enabled=get_config("phone_enabled", "0") == "1",
                               paused=get_config("phone_paused", "0") == "1", suggest=netcheck.default_base_url() if not get_config("phone_base_url", "") else "",
                               themes=PHONE_THEMES, copies=PHONE_COPY, theme=theme)

    @app.post("/phone/enable")
    @login_required
    def phone_enable():
        """开启小手机的初始化：装的人自己决定端口（默认 5092），确认后才真正开。"""
        from core import phoneserver
        port = request.form.get("phone_port", "").strip()
        if bad := phoneserver.check_port(current_bot(), port):
            flash(bad)
            return redirect(url_for("phone_page"))
        base = request.form.get("phone_base_url", "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            flash("网址要以 http:// 或 https:// 开头")
            return redirect(url_for("phone_page"))
        from core import phoneserver
        set_config("phone_port", port)
        ip = netcheck.public_ip_cached()
        set_config("phone_base_url", base or (f"http://{ip}:{port}" if ip else ""))
        set_config("phone_https", "1" if base.startswith("https://") else "0")
        set_config("phone_enabled", "1")
        res = phoneserver.start()
        if "没能" in res:
            set_config("phone_enabled", "0")
            flash(res)
        else:
            flash(f"{res}。最后一步：到云服务商控制台的安全组放行 {port} 端口，再点下面的「检查玩家能不能打开」。")
        return redirect(url_for("phone_page"))

    @app.post("/phone/settings")
    @login_required
    def phone_save():
        f = request.form
        from core import phoneserver
        port = f.get("phone_port", "5092").strip()
        if bad := phoneserver.check_port(current_bot(), port):
            flash(bad)
            return redirect(url_for("phone_page"))
        base = f.get("phone_base_url", "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            flash("网址要以 http:// 或 https:// 开头")
            return redirect(url_for("phone_page"))
        old_port, old_on = get_config("phone_port", "5092"), get_config("phone_enabled", "0")
        set_config("phone_port", port)
        set_config("phone_base_url", base)
        set_config("phone_enabled", "1" if f.get("phone_enabled") else "0")
        set_config("phone_paused", "1" if f.get("phone_paused") else "0")
        set_config("phone_https", "1" if base.startswith("https://") else "0")
        theme = {"visual": f.get("visual", "modern"), "copy": f.get("copy", "modern")}
        set_config("phone_theme_config", json.dumps(theme, ensure_ascii=False))
        msg = "已保存"
        if old_port != port or old_on != get_config("phone_enabled"):
            from core import phoneserver
            msg += "。" + phoneserver.start() + (f"。别忘了到云服务商的安全组放行 {port} 端口，然后点下面的「检查玩家能不能打开」" if get_config("phone_enabled") == "1" else "")
        flash(msg)
        return redirect(url_for("phone_page"))

    @app.post("/phone/check")
    @login_required
    def phone_check():
        port = int(get_config("phone_port", "5092") or 5092)
        res = netcheck.probe_outside(port)
        set_config("phone_net_last", json.dumps(res, ensure_ascii=False))
        return redirect(url_for("phone_page"))

    @app.post("/phone/codes/generate")
    @login_required
    def phone_generate():
        flash(f"已给 {phonecodes.generate_all()} 个角色生成激活码")
        return redirect(url_for("phone_page"))

    @app.post("/phone/codes/<int:role_id>/reset")
    @login_required
    def phone_reset(role_id):
        phonecodes.reset(role_id)
        flash("已重置，旧码立刻失效")
        return redirect(url_for("phone_page"))

    @app.route("/pool")
    @login_required
    def pool_page():
        groups = pool_core.all_groups()
        sets: dict[str, list] = {}
        for g in groups:
            sets.setdefault(g["set_name"] or "（未分组）", []).append(g)
        with get_conn() as conn:
            live = conn.execute("SELECT * FROM rooms WHERE status='open' ORDER BY id").fetchall()
        live_rows = [{**dict(r), "slot": ("不占时间线" if r["kind"] == "微信" else f"D{r['day']} {r['start_hm']}-{r['end_hm']}"),
                      "who": "、".join(m["name"] for m in rooms_core.members(r["id"])),
                      "grp": (pool_core.get(r["pool_id"]) if r["pool_id"] else None)} for r in live]
        return render_template("pool.html", sets=sets, counts=pool_core.counts(), live=live_rows, unfilled=sum(1 for g in groups if not g["real_gid"]))

    @app.post("/pool/<int:pid>/save")
    @login_required
    def pool_save(pid):
        pool_core.update(pid, request.form.get("real_gid", ""), request.form.get("label", ""), request.form.get("set_name", ""))
        flash("已保存")
        return redirect(url_for("pool_page"))

    @app.post("/pool/<int:pid>/toggle")
    @login_required
    def pool_toggle(pid):
        ok, why = pool_core.set_in_pool(pid, request.form.get("on") == "1")
        flash("已更新" if ok else why)
        return redirect(url_for("pool_page"))

    @app.post("/pool/<int:pid>/clean")
    @login_required
    def pool_clean(pid):
        g = pool_core.get(pid)
        if g and g["state"] == "cleaning":
            pool_core.release(pid, "free")
            flash("已放回群池，排队的房间稍后自动补上群。")
        return redirect(url_for("pool_page"))

    @app.post("/pool/<int:pid>/delete")
    @login_required
    def pool_delete(pid):
        ok, why = pool_core.delete(pid)
        flash("已删除" if ok else why)
        return redirect(url_for("pool_page"))

    @app.post("/pool/set")
    @login_required
    def pool_set():
        name, on = request.form.get("set_name", ""), request.form.get("on") == "1"
        ok, skip = pool_core.set_set(name, on)
        flash(f"组「{name}」已{'开启' if on else '关闭'}：{ok} 个群{('，跳过 %d 个（占用中或已是别的用途）' % skip) if skip else ''}")
        return redirect(url_for("pool_page"))

    @app.post("/pool/bulk")
    @login_required
    def pool_bulk():
        """把一串群号按顺序填给「还没填群号」的群（按先被机器人看到的顺序）。"""
        nums = [x.strip() for x in request.form.get("nums", "").replace("，", "\n").replace(",", "\n").split() if x.strip()]
        set_name = request.form.get("set_name", "").strip()
        empty = [g for g in pool_core.all_groups() if not g["real_gid"] and not g["conflict"]]
        for g, n in zip(empty, nums):
            pool_core.update(g["id"], n, g["label"], set_name or g["set_name"])
        flash(f"已填 {min(len(empty), len(nums))} 个群号" + (f"；多出 {len(nums) - len(empty)} 个号没有对应的群（机器人还没进那些群）" if len(nums) > len(empty) else ""))
        return redirect(url_for("pool_page"))

    @app.post("/rooms/<int:rid>/end")
    @login_required
    def room_end(rid):
        room = rooms_core.get(rid)
        pool_id = room["pool_id"] if room else None
        rooms_core.end(rid)
        g = pool_core.get(pool_id) if pool_id else None
        if g and pool_core.is_qq_only(g):                  # 房间群：群名改回空房间
            loopbridge.run(qqmanager_core.room_rename(current_bot(), g, qqpool_core.free_name(g["set_name"])))
        flash(f"R{rid} 已结束（网页上结束不会通知玩家，群进入待清场）")
        return redirect(url_for("pool_page"))

    @app.route("/extensions")
    @login_required
    def extensions_page():
        items = ext_core.list_all()
        for it in items:
            g = it.get("settings_group")
            it["fields"] = [field_view(k, label, default, get_config(k, default)) for k, (label, default) in dict(SECTIONS)[g].items() if k not in HIDDEN_FIELDS] if g else []
            it["load_error"] = it.get("error") or ext_core.load_error(it["id"])
            if it["kind"] == "user":
                it["vals"] = {st["key"]: ext_core.get_setting(it["id"], st["key"]) for st in it.get("settings", [])}
        return render_template("extensions.html", items=items, tab=request.args.get("tab", "list"), pending=ext_core.pending_restart(),
                               owner=bool(session.get("owner")), restarting=request.args.get("restarting") == "1", ai_prompt=ext_core.ai_prompt() if request.args.get("tab") == "ai" else "")

    @app.route("/extensions/ai-prompt.md")
    @login_required
    def extensions_ai_prompt():
        from flask import Response
        return Response(ext_core.ai_prompt(), mimetype="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E7%BB%99AI%E7%9A%84%E6%89%A9%E5%B1%95%E6%A8%A1%E6%9D%BF.md"})

    @app.route("/extensions/skeleton.zip")
    @login_required
    def extensions_skeleton():
        from flask import Response
        return Response(ext_core.skeleton_zip(), mimetype="application/zip", headers={"Content-Disposition": "attachment; filename=my_extension.zip"})

    @app.post("/extensions/toggle")
    @owner_required
    def extensions_toggle():
        ext_id, on = request.form.get("id", ""), request.form.get("on") == "1"
        known = {e["id"] for e in ext_core.list_all()}
        if ext_id not in known:
            abort(404)
        ext_core.set_enabled(ext_id, on)
        flash(("已打开" if on else "已关闭") + "，重启后生效（点右上角「重启并生效」）")
        return redirect(url_for("extensions_page"))

    @app.post("/extensions/upload")
    @owner_required
    def extensions_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            flash("先选一个 zip 文件")
            return redirect(url_for("extensions_page"))
        ok, msg = ext_core.install_zip(f.read(ext_core.MAX_ZIP + 1), replace=request.form.get("replace") == "1")
        flash(msg)
        return redirect(url_for("extensions_page"))

    @app.post("/extensions/<ext_id>/delete")
    @owner_required
    def extensions_delete(ext_id):
        flash("已删除，重启后生效" if ext_core.delete_user(ext_id) else "没有这个扩展")
        return redirect(url_for("extensions_page"))

    @app.post("/extensions/<ext_id>/settings")
    @owner_required
    def extensions_settings(ext_id):
        n = ext_core.save_settings(ext_id, request.form)
        flash(f"已保存 {n} 项")
        return redirect(url_for("extensions_page"))

    @app.post("/extensions/restart")
    @owner_required
    def extensions_restart():
        ext_core.restart_soon()
        return redirect(url_for("extensions_page", restarting=1))

    # ---------- 自定义回复 ----------
    def _ar_form() -> dict:
        f = request.form
        try:
            replies = json.loads(f.get("replies_json", "[]"))
            replies = [str(x) for x in replies] if isinstance(replies, list) else []
        except ValueError:
            replies = []
        try:
            cd = int(f.get("cooldown", "0") or 0)
        except ValueError:
            cd = 0
        return {"name": f.get("name", ""), "mode": f.get("mode", "contains"), "words": f.get("words", ""), "replies": replies,
                "who": f.get("who", "all"), "place": f.get("place", "all"), "cooldown": cd, "send_mode": f.get("send_mode", "random")}

    @app.route("/autoreply")
    @login_required
    def autoreply_page():
        return render_template("autoreply.html", rules=autoreply_core.list_rules(), modes=autoreply_core.MODES, who=autoreply_core.WHO,
                               place=autoreply_core.PLACE, send=autoreply_core.SEND, edit=request.args.get("edit", type=int))

    @app.post("/autoreply/save")
    @login_required
    def autoreply_save():
        rid = request.form.get("id", type=int)
        ok, err = autoreply_core.save_rule(_ar_form(), rid)
        flash("已保存" if ok else err)
        return redirect(url_for("autoreply_page", edit=None if ok else rid))

    @app.post("/autoreply/<int:rule_id>/toggle")
    @login_required
    def autoreply_toggle(rule_id):
        autoreply_core.set_enabled(rule_id, bool(request.form.get("on")))
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/<int:rule_id>/move")
    @login_required
    def autoreply_move(rule_id):
        autoreply_core.move(rule_id, -1 if request.form.get("dir") == "up" else 1)
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/<int:rule_id>/delete")
    @login_required
    def autoreply_delete(rule_id):
        autoreply_core.delete_rule(rule_id)
        flash("已删除")
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/test")
    @login_required
    def autoreply_test():
        from flask import jsonify
        return jsonify(autoreply_core.preview(request.form.get("text", "")))

    @app.route("/season")
    @login_required
    def season_page():
        from core import entrance as entrance_core
        return render_template("season.html", running=season_core.running(), phase=season_core.phase(), day=season_core.day_label(),
                               auto=get_config("auto_day") == "1", booked=season_core.booked(), past=[x for x in season_core.all_seasons() if x["status"] == "ended"],
                               preview=season_core.wipe_preview(), stage=entrance_core.get_stage(), queue=entrance_core.queue(),
                               entrance_on=entrance_core.active())

    @app.post("/season/book")
    @login_required
    def season_book():
        f = request.form
        ok, msg = season_core.book(f.get("name", ""), f.get("start", ""), f.get("end", ""), f.get("supp", ""), f.get("npcs", ""))
        flash(msg)
        return redirect(url_for("season_page"))

    @app.post("/season/<int:season_id>/delete")
    @login_required
    def season_delete(season_id):
        flash("预订已删除" if season_core.delete_booking(season_id) else "只能删除还没开始的预订")
        return redirect(url_for("season_page"))

    @app.post("/season/start")
    @login_required
    def season_start():
        ok, msg = season_core.start(request.form.get("name", ""), clear=request.form.get("clear") == "1")
        flash("上季的角色和互动记录还没清空：先点「收尾」，或勾选「清空后开始」" if msg == "NEEDS_WIPE" else msg)
        return redirect(url_for("season_page"))

    @app.post("/season/end")
    @login_required
    def season_end():
        flash(season_core.end()[1])
        return redirect(url_for("season_page"))

    @app.post("/season/wipe")
    @login_required
    def season_wipe():
        if season_core.running():
            flash("季度还在进行中，先结束")
        elif request.form.get("confirm") != "收尾":
            flash("没有清空：确认框里要输入「收尾」")
        else:
            season_core.wipe()
            flash("已收尾，本季的角色和互动记录已清空")
        return redirect(url_for("season_page"))

    @app.post("/season/day")
    @login_required
    def season_day():
        raw = request.form.get("day", "").strip().upper().lstrip("D")
        if not season_core.running():
            flash("现在没有进行中的季度")
        elif not raw.isdigit():
            flash("天数写成 D3 或 3")
        else:
            season_core.set_day(int(raw))
            flash(f"现在是 D{int(raw)}")
        return redirect(url_for("season_page"))

    @app.post("/season/autoday")
    @login_required
    def season_autoday():
        on = request.form.get("on") == "1"
        set_config("auto_day", "1" if on else "0")
        if on:
            set_config("auto_day_last", season_core.today())
        flash("自动天数已" + ("开启" if on else "关闭"))
        return redirect(url_for("season_page"))

    @app.route("/admins")
    @login_required
    def admins_page():
        return render_template("admins.html", admins=admins_core.list_admins(), code=request.args.get("code", ""))

    @app.post("/admins/code")
    @login_required
    def admins_code():
        code = admins_core.make_code(request.form.get("note", "").strip())
        return redirect(url_for("admins_page", code=code))

    @app.post("/admins/remove")
    @login_required
    def admins_remove():
        admins_core.remove(request.form.get("bot_id", ""), request.form.get("openid", ""))
        flash("已移除管理员")
        return redirect(url_for("admins_page"))

    return app
