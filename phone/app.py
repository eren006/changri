"""小手机（玩家用的网页）：第二个 Flask 应用，监听公网端口；设置平台仍只开本机。
和机器人同进程、同一个 SQLite，网页直接读写核心表，没有「同步」这回事。"""
import hashlib
import hmac
import secrets
import time
from pathlib import Path

from flask import Flask, abort, flash, jsonify, make_response, redirect, render_template, request, session, url_for

from core import trpgexport, trpglog, trpgrender, blocks, gifts, loopbridge, messaging, phonecodes, phonegate, season, sendmsg
from core.db import get_conn, get_config
from core.lockstore import FailStore
from phone.themes import PHONE_THEMES, phone_theme

HERE = Path(__file__).resolve().parent
def _cookie() -> str:
    """同一台服务器上不同机器人的小手机只差端口，浏览器的 Cookie 不分端口，所以名字里带上机器人。"""
    from core.db import current_bot
    b = current_bot()
    return f"phone_auth_{b[-6:]}" if b else "phone_auth"

SKIN_COOKIE = "phone_skin"
MAX_AGE = 30 * 86400
LOCK_AFTER, LOCK_SECONDS = 5, 300
_fails = FailStore("phone")      # 存数据库，重启不清零


def _sign(key: str, role_id: int, code: str, ts: int) -> str:
    return hmac.new(key.encode(), f"{role_id}|{code}|{ts}".encode(), hashlib.sha256).hexdigest()


def create_phone_app(secret_key: str) -> Flask:
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"), static_url_path="/p/static")
    app.secret_key = secret_key
    app.jinja_env.globals["csrf"] = lambda: _csrf()
    app.jinja_env.globals["phone_themes"] = PHONE_THEMES

    def _csrf() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    def _skin():
        return phone_theme(get_config("phone_theme_config", ""), request.cookies.get(SKIN_COOKIE, ""))

    def _base_url() -> str:
        return get_config("phone_base_url", "").rstrip("/")

    def current():
        """cookie 里的角色，每次都回库核对码还在不在（重置码后旧登录立刻失效）。"""
        raw = request.cookies.get(_cookie(), "")
        try:
            rid, code, ts, sig = raw.split(".")
            rid, ts = int(rid), int(ts)
        except ValueError:
            return None
        if not hmac.compare_digest(sig, _sign(app.secret_key, rid, code, ts)) or time.time() - ts > MAX_AGE:
            return None
        role = phonecodes.role_for_code(code)
        if not role or role["id"] != rid:
            return None
        phonecodes.touch(rid)
        return role

    def _status(role) -> dict:
        """顶栏「今日 短信 1/3 · 礼物 0/2」用：没有上限（0）的不显示。"""
        out = {}
        for kind in ("sms", "gift"):
            lim = messaging.daily_limit(kind)
            out[kind] = (messaging.day_count(role["id"], kind), lim) if lim > 0 else None
        return out

    def _send_fn():
        from plugins._long import make_send
        return make_send()

    def page(template: str, **ctx):
        ctx.setdefault("skin", _skin())
        ctx.setdefault("comm_paused", phonegate.comm_paused())
        ctx.setdefault("brand", ctx["skin"]["words"]["brand"])
        role = ctx.get("role")
        if role is not None and "status" not in ctx:
            ctx["status"] = _status(role)
        resp = make_response(render_template(template, **ctx))
        return resp

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.before_request
    def guard():
        if get_config("phone_enabled", "0") != "1" and not request.path.startswith("/p/static"):
            abort(404)
        if get_config("phone_https", "0") == "1" and request.headers.get("X-Forwarded-Proto", request.scheme) == "http":
            return redirect(request.url.replace("http://", "https://", 1), 301)
        if request.method == "POST" and not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "-")):
            abort(400)

    def need_login():
        role = current()
        if not role:
            return None, redirect(url_for("entry"))
        return role, None

    @app.route("/")
    def root():
        return redirect(url_for("entry"))

    @app.route("/p", methods=["GET", "POST"])
    @app.route("/p/", methods=["GET", "POST"])
    def entry():
        if request.method == "GET" and current():
            return redirect(url_for("inbox"))
        error = ""
        if request.method == "POST":
            ip = request.headers.get("X-Real-IP", request.remote_addr or "?")
            n, until = _fails.get(ip, (0, 0.0))
            if n >= LOCK_AFTER and time.time() < until:
                error = f"输错太多次了，请 {int((until - time.time()) // 60) + 1} 分钟后再试"
            else:
                code = request.form.get("code", "").strip().upper()
                role = phonecodes.role_for_code(code)
                if role:
                    _fails.pop(ip, None)
                    ts = int(time.time())
                    resp = redirect(url_for("inbox"))
                    resp.set_cookie(_cookie(), f"{role['id']}.{code}.{ts}.{_sign(app.secret_key, role['id'], code, ts)}",
                                    max_age=MAX_AGE, path="/p", httponly=True, samesite="Lax",
                                    secure=get_config("phone_https", "0") == "1")
                    return resp
                n = (n + 1) if time.time() < until or n < LOCK_AFTER else 1
                _fails[ip] = (n, time.time() + LOCK_SECONDS)
                error = "激活码不对，请核对后再输入"
        return page("entry.html", error=error)

    @app.route("/p/logout", methods=["POST"])
    def logout():
        resp = redirect(url_for("entry"))
        resp.delete_cookie(_cookie(), path="/p")
        return resp

    @app.route("/p/me")
    def inbox():
        role, bad = need_login()
        if bad:
            return bad
        rid = role["id"]
        with get_conn() as c:
            rows = c.execute(
                "SELECT m.*, f.name AS fname, t.name AS tname FROM messages m JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
                "WHERE (m.from_role_id=? OR m.to_role_id=?) ORDER BY m.id DESC", (rid, rid)).fetchall()
        threads: dict[int, dict] = {}
        for m in rows:
            other = m["to_role_id"] if m["from_role_id"] == rid else m["from_role_id"]
            if other not in threads:
                name = m["tname"] if m["from_role_id"] == rid else m["fname"]
                text = m["content"] if m["kind"] == "sms" else f"［礼物］{m['title'] or m['content']}"
                threads[other] = {"id": other, "name": name, "preview": text[:40], "time": time.strftime("%m-%d %H:%M", time.localtime(m["created_ts"]))}
        return page("inbox.html", role=role, threads=list(threads.values()), mode="inbox")

    @app.route("/p/me/<int:other>")
    def thread(other: int):
        role, bad = need_login()
        if bad:
            return bad
        rid = role["id"]
        with get_conn() as c:
            who = c.execute("SELECT name FROM roles WHERE id=?", (other,)).fetchone()
            if not who or other == rid:
                abort(404)
            msgs = c.execute("SELECT * FROM messages WHERE (from_role_id=? AND to_role_id=?) OR (from_role_id=? AND to_role_id=?) ORDER BY id",
                             (rid, other, other, rid)).fetchall()
        items = [{"mine": m["from_role_id"] == rid, "kind": m["kind"], "title": m["title"], "text": m["content"],
                  "time": time.strftime("%m-%d %H:%M", time.localtime(m["created_ts"]))} for m in msgs]
        blk = blocks.get_block(role["id"], other)
        return page("thread.html", role=role, me=role["name"], other=who["name"], other_id=other, items=items, mode="thread",
                    blocked=blk, can_recall=bool(messaging.last_recallable(role["id"])),
                    items_catalog=gifts.catalog(), only_preset=gifts.only_preset(), draft_gift=request.args.get("gift", ""),
                    allow_sign=get_config("allow_custom_sign", "0") == "1")

    @app.route("/p/me/new")
    def new_chat():
        role, bad = need_login()
        if bad:
            return bad
        with get_conn() as c:
            others = c.execute("SELECT id, name FROM roles WHERE id!=? ORDER BY name", (role["id"],)).fetchall()
        return page("new.html", role=role, others=others, gift=request.args.get("gift", ""), mode="new")

    @app.post("/p/me/send")
    def send():
        role, bad = need_login()
        if bad:
            return bad
        to = request.form.get("to", "")
        target = phonecodes.role_by_id(int(to)) if to.isdigit() else None
        if not target:
            abort(404)
        kind = "gift" if request.form.get("kind") == "gift" else "sms"
        gift = request.form.get("gift_name", "").strip()
        text = request.form.get("text", "").strip()
        back = redirect(url_for("thread", other=target["id"]))
        bad_reason = phonegate.check_send(role["name"], "短信" if kind == "sms" else "礼物", gift, text)
        if bad_reason:
            flash(bad_reason)
            return back
        sign = request.form.get("sign", "").strip() if get_config("allow_custom_sign", "0") == "1" else ""
        try:
            ok, msg = loopbridge.run(sendmsg.send(role, target, kind, gift, text, sign, _send_fn()))
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"发送失败：{str(e)[:60]}"
        flash(msg)
        return back

    @app.post("/p/me/recall")
    def recall():
        role, bad = need_login()
        if bad:
            return bad
        m = messaging.last_recallable(role["id"])
        back = redirect(request.referrer or url_for("inbox"))
        if not m:
            flash("没有可以撤回的：只能撤回自己 2 分钟内发出的最近一条短信/礼物。")
            return back
        import nonebot
        from plugins._long import recall_group_message

        async def go():
            bot = nonebot.get_bots().get(m["delivered_bot"])
            if bot is None:
                raise RuntimeError("对方群所在的机器人现在不在线")
            await recall_group_message(bot, m["delivered_group"], m["delivered_msg_id"])
        try:
            loopbridge.run(go())
        except Exception as e:  # noqa: BLE001
            flash(f"撤回失败（可能已超过 2 分钟）：{str(e)[:60]}")
            return back
        messaging.mark_recalled(m["id"])
        flash(f"已撤回发给「{m['to_name']}」的{sendmsg.KIND_NAME[m['kind']]}，今天的次数已返还。")
        return back

    @app.post("/p/me/block")
    def block():
        role, bad = need_login()
        if bad:
            return bad
        other = request.form.get("to", "")
        if not other.isdigit() or int(other) == role["id"] or not phonecodes.role_by_id(int(other)):
            abort(404)
        other = int(other)
        if request.form.get("action") == "unblock":
            ok, msg = blocks.unblock(role["id"], other)
            flash("已解除拉黑。" if ok else (f"还要等 {msg[5:]} 分钟才能解除拉黑。" if msg.startswith("wait:") else "你没有拉黑 TA。"))
        else:
            blocks.block(role["id"], other, request.form.get("silent") == "1")
            flash("已拉黑。TA 发起的短信和礼物都不会送达你。")
        return redirect(url_for("thread", other=other))

    @app.route("/p/me/shop")
    def shop():
        role, bad = need_login()
        if bad:
            return bad
        return page("shop.html", role=role, items=gifts.catalog(), album=gifts.received(role["id"]), only=gifts.only_preset(), mode="shop")

    @app.route("/p/me/discover")
    def discover():
        role, bad = need_login()
        if bad:
            return bad
        return page("discover.html", role=role, mode="discover")

    @app.route("/p/me/timeline")
    def timeline_page():
        role, bad = need_login()
        if bad:
            return bad
        from core import overview
        return page("timeline.html", role=role, mode="discover", days=overview.timeline_days(role["id"]))

    @app.route("/p/me/todo")
    def todo_page():
        role, bad = need_login()
        if bad:
            return bad
        from core import overview
        return page("todo.html", role=role, mode="discover", items=overview.todo_items(role))

    @app.route("/p/me/profile")
    def profile():
        role, bad = need_login()
        if bad:
            return bad
        return page("profile.html", role=role, mode="profile", season=season.running(), https=get_config("phone_https", "0") == "1")

    @app.route("/p/me/skin", methods=["POST"])
    def set_skin():
        role, bad = need_login()
        if bad:
            return bad
        pick = request.form.get("skin", "")
        resp = redirect(url_for("profile"))
        if pick in PHONE_THEMES:
            resp.set_cookie(SKIN_COOKIE, pick, max_age=365 * 86400, path="/p", samesite="Lax")
        else:
            resp.delete_cookie(SKIN_COOKIE, path="/p")
        return resp

    @app.route("/p/guide")
    def guide():
        return page("guide.html", mode="guide")

    @app.route("/p/manifest.json")
    def manifest():
        return jsonify({"name": _skin()["words"]["brand"], "short_name": "小手机", "start_url": "/p/me", "display": "standalone",
                        "background_color": "#ffffff", "theme_color": "#ffffff",
                        "icons": [{"src": "/p/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/p/icon-512.png", "sizes": "512x512", "type": "image/png"}]})

    @app.route("/p/icon-<int:size>.png")
    def icon(size: int):
        from flask import send_from_directory
        if size not in (180, 192, 512):
            abort(404)
        return send_from_directory(HERE / "static" / "themes" / "phone-icons", f"icon-{size}.png")

    @app.route("/l/<token>")
    def trpg_log_share(token):
        """跑团 log 的只读分享页：不用登录，token 是长随机串；只有 /log end 或 /log get 公开过的才能打开。"""
        log = trpglog.by_token(token)
        if not log:
            abort(404)
        resp = make_response(trpgrender.render_html(log, trpglog.lines(log["id"]), download_url=url_for("trpg_log_zip", token=token)))
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Robots-Tag"] = "noindex"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'"
        return resp

    @app.route("/l/<token>/zip")
    def trpg_log_zip(token):
        from urllib.parse import quote
        log = trpglog.by_token(token)
        if not log:
            abort(404)
        data, fname, mime = trpgexport.build(log, "zip")
        resp = make_response(data)
        resp.headers["Content-Type"] = mime
        resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(fname)}"
        return resp

    @app.route("/p/health")
    def health():
        return "ok"

    return app
