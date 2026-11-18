"""设置平台（Flask）外壳：登录、首次设置、选机器人、机器人接入、账号、更新、远程访问、HTTPS、扩展。
各功能的页面在 features/<功能>/admin.py，公用的东西在 admin/common.py。"""
import importlib
import os
import secrets
import time
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from functools import wraps
from markupsafe import Markup
from changri import config as _config
from changri.core import botconf, extensions as ext_core, loopbridge, netcheck, pool as pool_core, qqmanager as qqmanager_core, roles as roles_core, roomnotify as roomnotify_core, rooms as rooms_core, season as season_core, setupcode, updater, webusers
from changri.core.db import DB_PATH, current_bot, enter_bot, get_config, get_conn, init_db, leave_bot, list_bot_ids, set_config, use_bot
from changri.admin.common import GLOBAL_ENDPOINTS, HIDDEN_FIELDS, LOCK_AFTER, LOCK_SECONDS, SECTIONS, SETTINGS, _bot_cards, _bot_label, _bot_status, _csrf_token, _dashboard, _fails, _secret_key, field_view, log
from changri.core.textutil import hm_text, next_day_mark

THEME_COOKIE = "admin_theme"
THEMES = {"lily", "ballet", "xmas"}          # 样式在 templates/_themes.html；空 = 默认


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
    # cookie 起个专属名字：浏览器按 IP / 域名存 cookie、不分端口，同一台服务器上别的网站也叫默认的「session」时会互相冲掉，
    # 冲掉以后提交表单对不上校验码，以前直接报 400 Bad Request（登录后跳不进去）
    app.config.update(SESSION_COOKIE_NAME="changri_admin", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.jinja_env.filters["datetimefmt"] = lambda ts: time.strftime("%m-%d %H:%M", time.localtime(ts))
    app.jinja_env.globals["csrf"] = lambda: Markup(f'<input type="hidden" name="_csrf" value="{_csrf_token()}">')
    app.jinja_env.globals["next_day"] = next_day_mark            # 跨午夜的约会在结束时间前显示「次日」
    app.jinja_env.globals["NOTICE_GROUP"] = "120287262"       # 长日bot 通知群（作者的 QQ 群）；二维码在 static/notice-group-qr.png，换群要两个一起换

    from .botprefix import BotPrefix
    app.wsgi_app = BotPrefix(app.wsgi_app)

    loopbridge.install_flask_handler(app)            # 等机器人超时：提示「还在处理」，不出 500

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

    from changri.features import registry as _registry
    _guards = _registry.url_guards()

    def _csrf_failed():
        """表单校验码对不上：页面开太久、服务重启过、或者浏览器没把 cookie 带回来。不再报 400，回到原页面说清楚，换一个新的校验码。"""
        no_cookie = app.config["SESSION_COOKIE_NAME"] not in request.cookies
        session.pop("csrf", None)
        log.warning("表单校验码对不上：%s %s（%s）", request.method, request.path, "浏览器没带 cookie" if no_cookie else "校验码过期")
        flash("浏览器没有保存登录信息：请确认没有禁用 cookie，或者换个浏览器试试。" if no_cookie
              else "页面过期了（开太久或者服务刚重启过），已经刷新，请再操作一次。")
        if request.endpoint in ("login", "setup"):
            return redirect(url_for(request.endpoint))
        from urllib.parse import urlparse
        ref = urlparse(request.referrer or "")
        back = (ref.path or "/") + (f"?{ref.query}" if ref.query else "") if ref.netloc == request.host else url_for("dashboard")
        return redirect(back)

    @app.before_request
    def guard():
        for prefix, ext_id in _guards:      # 扩展关了，页面网址也一并关掉（前缀在各功能的 FEATURE["url_prefixes"] 里）
            if request.path.startswith(prefix) and not ext_core.is_enabled(ext_id):
                abort(404)
        if request.method == "POST" and not secrets.compare_digest(request.form.get("_csrf", "").encode(), session.get("csrf", "-").encode()):
            return _csrf_failed()

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

    @app.post("/theme")
    def theme_set():
        """后台外观（睡莲 / 芭蕾 / 圣诞 / 默认）：记在这个浏览器的 cookie 里，回到刚才的页面。"""
        from urllib.parse import urlparse
        theme = request.form.get("theme", "")
        ref = urlparse(request.referrer or "")
        back = (ref.path or "/") + (f"?{ref.query}" if ref.query else "") if ref.netloc == request.host else "/"
        resp = redirect(back)
        if theme in THEMES:
            resp.set_cookie(THEME_COOKIE, theme, max_age=365 * 86400, path="/", samesite="Lax", httponly=True)
        else:
            resp.delete_cookie(THEME_COOKIE, path="/")
        return resp

    @app.context_processor
    def inject():
        in_bot = bool(request.script_root)
        theme = request.cookies.get(THEME_COOKIE, "")
        ctx = {"admin_theme": theme if theme in THEMES else "", "me": session.get("name", ""), "is_owner": bool(session.get("owner")), "disabled_nav": ext_core.disabled_nav(),
               "in_bot": in_bot, "bot_id": request.environ.get("changri.bot", ""), "bots": [], "bot_label": "",
               "root_url": lambda ep, **kw: url_for(ep, **kw)[len(request.script_root):], "update_dot": ""}
        if session.get("uid"):
            accs = botconf.list_accounts()
            ctx["bots"] = [(a["app_id"], _bot_label(a)) for a in accs]
            ctx["bot_label"] = next((_bot_label(a) for a in accs if a["app_id"] == ctx["bot_id"]), "")
            if session.get("owner"):                  # 「更新」页只有主账号能开，小红点也只给主账号看
                try:
                    ctx["update_dot"] = updater.update_badge()
                except Exception:  # noqa: BLE001
                    pass
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
            from changri.core import db as _db
            if not _db.LEGACY_SINGLE and not botconf.list_accounts() and session.get("owner"):
                return redirect(url_for("wizard"))          # 刚装好、还没接入机器人：走安装向导
            cards = _bot_cards()
            if len(cards) == 1 and not request.args.get("pick"):
                return redirect(f"/b/{cards[0]['id']}/")          # 只有一个机器人：直接进去
            if cards:
                return render_template("picker.html", cards=cards)
        from changri.core import errlog
        with get_conn() as conn:
            live = conn.execute("SELECT * FROM rooms WHERE status='open' ORDER BY id").fetchall()
        live_rooms = [{**dict(r), "slot": ("不占时间线" if r["kind"] == "微信" else f"D{r['day']} {hm_text(r['start_hm'], r['end_hm'])}"),
                       "who": "、".join(m["name"] for m in rooms_core.members(r["id"])),
                       "grp": (pool_core.get(r["pool_id"]) if r["pool_id"] else None)} for r in live]
        err_rows = errlog.recent(errlog.KEEP)
        serious = [e for e in err_rows if e.get("kind") != "notice"]       # 提醒不让总览变红
        errors = (serious + [e for e in err_rows if e.get("kind") == "notice"])[:50]
        errors.sort(key=lambda e: e.get("ts", 0), reverse=True)
        return render_template("dashboard.html", d=_dashboard(), errors=errors, err_serious=len(serious),
                               err_notices=len(err_rows) - len(serious), err_url=errlog.report_url(),
                               cal=season_core.month_grid(request.args.get("cal", "")), cal_line=season_core.upcoming_line(),
                               live_rooms=live_rooms, waiting_rooms=sum(1 for r in live_rooms if not r["grp"]),
                               ask_contact=_should_ask_contact(), noat_tip=get_config(NOAT_TIP_DONE, "") != "1")

    NOAT_TIP_DONE = "noat_tip_done"         # 总览上「不用 @ 也能回」卡片叉掉了（每个机器人各记各的）

    @app.route("/no-at")
    @login_required
    def noat_page():
        """怎么让官方机器人不用 @ 也能回：群主在群机器人管理里打开两个开关。说明以前只藏在备用群池页的折叠框里，很多人找不到。"""
        return render_template("noat.html")

    @app.post("/no-at/dismiss")
    @login_required
    def noat_dismiss():
        """叉掉总览上的卡片：设好了就不占位置。说明页还在左侧「通用」里。"""
        set_config(NOAT_TIP_DONE, "1")
        flash("好的，总览上不再显示。以后要看，在左侧「通用」里点「不用 @ 也能回」。")
        return redirect(url_for("dashboard"))

    CONTACT_PROMPT_DONE = "update_contact_prompt_done"      # update_ 开头 = 全局库，所有机器人共用一个「问过了」

    def _should_ask_contact() -> bool:
        """总览上弹一次「建议填联系 QQ」：主账号、报错会发出去、哪个机器人都没填、也没点过「不用了」。"""
        from changri.core import errlog
        try:
            return (bool(session.get("owner")) and get_config(CONTACT_PROMPT_DONE, "") != "1"
                    and errlog.report_url().lower().startswith(("http://", "https://")) and not errlog.contact_qq())
        except Exception:  # noqa: BLE001  弹窗只是建议，判断出错就不弹，不能把总览弄挂
            return False

    @app.post("/contact-prompt")
    @owner_required
    def contact_prompt():
        """弹窗的两个按钮：保存（填进当前机器人的「联系 QQ」）/ 不用了。两种都不再弹；以后想填或改去「基础」设置。"""
        from changri.core import errlog
        if request.form.get("action") == "save":
            qq = request.form.get("qq", "").strip()
            if not errlog._QQ.fullmatch(qq):
                flash("QQ 号要是 5 到 12 位数字。没保存，下次打开总览还会问你。")
                return redirect(url_for("dashboard"))
            set_config("contact_qq", qq)
            flash(f"已保存联系 QQ {qq}。以后报错发给作者时会带上它；想改或删，去「基础」设置里的「联系 QQ」。")
        else:
            flash("好的，不填。以后想填，去「基础」设置里的「联系 QQ」。")
        set_config(CONTACT_PROMPT_DONE, "1")
        return redirect(url_for("dashboard"))

    @app.post("/rooms/notify")
    @login_required
    def rooms_notify():
        f = request.form
        try:
            hours = float(f.get("hours", "") or 0)
        except ValueError:
            hours = 0
        text = f.get("text", "").strip()
        if hours <= 0 or not text:
            flash("要写清楚多少小时，以及通知的内容")
            return redirect(url_for("dashboard") + "#rooms")
        items = roomnotify_core.candidates(hours, "open" if f.get("mode") == "open" else "idle")
        if not items:
            flash("没有符合条件的房间")
            return redirect(url_for("dashboard") + "#rooms")
        from changri.core.qqsend import make_send
        ok, bad = loopbridge.run(roomnotify_core.notify(items, text, current_bot(), make_send(), qqmanager_core.room_say), timeout=120)
        flash(f"已通知 {ok} 个房间" + (f"；{len(bad)} 个没发出去：" + "；".join(bad[:3]) if bad else ""))
        return redirect(url_for("dashboard") + "#rooms")

    @app.post("/errors/clear")
    @login_required
    def errors_clear():
        from changri.core import errlog
        errlog.clear()
        flash("报错日志已清空")
        return redirect(url_for("dashboard"))

    @app.route("/healthz")
    def healthz():
        """网页自己轮询它：连不上就说明正在重启，页面上显示「正在重启」并在恢复后自动刷新。不需要登录、不返回任何数据。"""
        return Response("ok", mimetype="text/plain", headers={"Cache-Control": "no-store"})

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
        from changri.core import db as _db, phoneserver
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
        done, info = request.args.get("done", ""), None
        # 点了「检查更新」，或者后台定时检查已经发现了新版本（小红点亮着）：打开页面就把这次更新了什么列出来
        if not done and (request.args.get("check") or get_config("update_latest", "")):
            try:
                info = updater.check()
                set_config("update_latest", info["version"] if info["newer"] else "")
            except updater.UpdateError as e:
                flash(str(e))
        return render_template("update.html", current=updater.current_version(), info=info, done=done,
                               skipped=get_config("update_skip", ""), failed=failed.read_text(encoding="utf-8") if failed.exists() else "")

    @app.post("/update/check")
    @owner_required
    def update_check():
        try:
            info = updater.check()
            set_config("update_latest", info["version"] if info["newer"] else "")
        except updater.UpdateError as e:
            flash(str(e))
            return redirect(url_for("update_page"))
        return redirect(url_for("update_page", check=1))

    @app.post("/update/skip")
    @owner_required
    def update_skip():
        """「这次先不更新」：这个版本不再亮小红点，等有更新的版本出来再提醒。随时还能回「更新」页手动更新。"""
        ver = request.form.get("version", "")
        if updater.parse_version(ver):
            set_config("update_skip", ver)
            flash(f"好的，{ver} 先不更新。出了更新的版本会再提醒；想更新随时回这里点。")
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

    from changri.features import registry
    for page in registry.admin_pages():          # 各功能自己的页面：features/<功能>/admin.py
        importlib.import_module(page).register(app, login_required)

    # ---------- 远程访问（只放行名单里的 IP） ----------

    @app.route("/remote")
    @login_required
    def remote_page():
        from changri.admin.remote import parse_allow
        return render_template("remote.html", on=get_config("remote_admin_enabled", "0") == "1", ips=get_config("remote_admin_ips", ""),
                               port=str(_config.admin_port()), count=len(parse_allow(get_config("remote_admin_ips", ""))),
                               ip=netcheck.public_ip_cached())

    @app.post("/remote/save")
    @login_required
    def remote_save():
        from changri.admin.remote import parse_allow
        ips = request.form.get("ips", "").strip()
        want = bool(request.form.get("on"))
        if want and not parse_allow(ips):
            flash("要先填至少一个允许的 IP，否则不能开启（名单为空 = 谁都进不来）")
            return redirect(url_for("remote_page"))
        set_config("remote_admin_ips", ips)
        set_config("remote_admin_enabled", "1" if want else "0")
        flash("已保存，马上生效（后台端口不变）")
        return redirect(url_for("remote_page"))

    # ---------- 一键 HTTPS（只有主账号；公网的「远程访问」里也不能做） ----------
    @app.route("/https")
    @owner_required
    def https_page():
        from changri.core import httpsproxy
        httpsproxy.activate_if_ready()
        return render_template("https.html", st=httpsproxy.status())

    @app.route("/https/status")
    @owner_required
    def https_status():
        from changri.core import httpsproxy
        ready = httpsproxy.activate_if_ready()
        return {"ready": ready, "blocked": False if ready else httpsproxy.blocked(httpsproxy.port80_state(), httpsproxy.port443_state())}

    @app.post("/https/enable")
    @owner_required
    def https_enable():
        from changri.core import httpsproxy
        try:
            subj = httpsproxy.enable(request.form.get("domain", ""))
        except httpsproxy.CaddyError as e:
            flash(f"没能开通：{e}")
            return redirect(url_for("https_page"))
        from changri.platform import service
        fw = service.open_firewall() or service.firewall_hint()
        flash(f"已交给 Caddy，正在为 {subj} 申请证书，一般半分钟内好" + (f"。{fw}" if fw else ""))
        return redirect(url_for("https_page", wait=1))

    @app.post("/https/disable")
    @owner_required
    def https_disable():
        from changri.core import httpsproxy
        try:
            httpsproxy.disable()
            flash("已关闭 HTTPS，小手机网址改回 http")
        except httpsproxy.CaddyError as e:
            flash(f"网址已改回 http，但 Caddy 那边没清理掉：{e}")
        return redirect(url_for("https_page"))

    # ---------- 一键初始化 ----------

    @app.post("/init")
    @login_required
    def init_all():
        """一键初始化：补齐基础内容（HP、金币）和所有设置的默认值。
        补齐模式只填没设过的；恢复模式把所有设置改回默认（确认后才会做）。"""
        from changri.core import rpg
        mode = request.form.get("mode", "fill")
        made = rpg.seed_defaults(force=True)
        n = 0
        for key, (_label, default) in SETTINGS.items():
            if key in HIDDEN_FIELDS:
                continue
            if mode == "reset" or get_config(key, "\0") == "\0":
                set_config(key, default)
                n += 1
        if mode == "reset":
            ext_core.reset_personal()      # 个性化模块每次重置都回到关闭
        what = "、".join(made) if made else "基础内容都在"
        flash(("已恢复全部设置为默认" if mode == "reset" else "已补齐没设过的设置") + f"（{n} 项）；{what}。")
        return redirect(url_for("extensions_page"))

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
        for it in items:
            it["group"] = "core" if it["kind"] == "core" else ("custom" if it.get("personal") else "aux")
        tab = request.args.get("tab", "core")
        return render_template("extensions.html", items=items, tab=tab, cur=tab if tab in ("aux", "custom") else "core", pending=ext_core.pending_restart(),
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
        if ext_id == "phone":                          # 小手机：扩展卡片和「小手机」页的开关是同一个，网页立刻启停（「手机码」指令要重启后才有/才没）
            from changri.core import phoneserver
            set_config("phone_enabled", "1" if on else "0")
            res = phoneserver.start()
            if on:
                from changri.platform import service
                service.open_firewall_async()
            flash(("已打开" if on else "已关闭") + f"小手机：{res}。群里的「手机码」指令重启后生效（点右上角「重启并生效」）")
            return redirect(url_for("extensions_page"))
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

    return app
