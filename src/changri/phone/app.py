"""小手机（玩家用的网页）：第二个 Flask 应用，监听公网端口；设置平台仍只开本机。
和机器人同进程、同一个 SQLite，网页直接读写核心表，没有「同步」这回事。"""
import hashlib
import hmac
import secrets
import time
import importlib
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort, flash, jsonify, make_response, redirect, render_template, request, session, url_for

from changri.core import precall, announce, song as song_core, games, masquerade, moments, publicfeed, profiles, bottle, collect, extensions, gacha, lovemail, relations, roles as roles_core, rooms as rooms_core, rpg, sheets, wishes, trpgexport, trpglog, trpgrender, blocks, gifts, loopbridge, messaging, msgimages, phonecodes, phonegate, season, sendmsg
from changri.core.db import get_conn, get_config, get_int, set_config
from changri.core.lockstore import FailStore
from changri.phone.themes import PHONE_THEMES, phone_theme

HERE = Path(__file__).resolve().parent
def _cookie() -> str:
    """同一台服务器上不同机器人的小手机只差端口，浏览器的 Cookie 不分端口，所以名字里带上机器人。"""
    from changri.core.db import current_bot
    b = current_bot()
    return f"phone_auth_{b[-6:]}" if b else "phone_auth"

SKIN_COOKIE = "phone_skin"
MAX_AGE = 30 * 86400
LOCK_AFTER, LOCK_SECONDS = 5, 300
_fails = FailStore("phone")      # 存数据库，重启不清零

def _sign(key: str, role_id: int, code: str, ts: int) -> str:
    return hmac.new(key.encode(), f"{role_id}|{code}|{ts}".encode(), hashlib.sha256).hexdigest()

def _serve_media_fallback(app: Flask) -> None:
    """大的图片在安装包里放在运行环境的 changri-media/ 下（不随每次更新重复下载）；背景音乐按需下载（platform/remote_media.py）。"""
    from flask import send_from_directory

    from flask import Response

    from changri.platform import remote_media
    from changri.platform.layout import MEDIA_DIR
    plain = app.view_functions["static"]
    media = MEDIA_DIR / "phone" / "static"

    def static(filename):
        if (HERE / "static" / filename).is_file():
            return plain(filename=filename)
        if (media / filename).is_file():
            return send_from_directory(media, filename, max_age=app.get_send_file_max_age(filename))
        rel = f"phone/static/{filename}"
        if remote_media.is_remote(rel):                # 背景音乐这类：安装包里不带，第一次用到时从主页下载一次
            try:
                path = remote_media.fetch(rel)
            except remote_media.MediaError as e:
                return Response(f"暂时取不到这个文件（{e}），稍后再试。", status=503, mimetype="text/plain")
            return send_from_directory(path.parent, path.name, max_age=app.get_send_file_max_age(filename))
        return plain(filename=filename)
    app.view_functions["static"] = static


def create_phone_app(secret_key: str) -> Flask:
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"), static_url_path="/p/static")
    _serve_media_fallback(app)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024        # 短信带图片：上传大小的硬上限，单张具体上限在设置里（msgimages）
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
        from changri.core.qqsend import make_send
        return make_send()

    def page(template: str, **ctx):
        ctx.setdefault("skin", _skin())
        ctx.setdefault("comm_paused", phonegate.comm_paused())
        ctx.setdefault("brand", ctx["skin"]["words"]["brand"])
        if ctx.get("role") is not None and "rel_unread" not in ctx and ctx.get("mode") in ("inbox", "discover", "profile"):
            ctx["rel_unread"] = relations.unread_total(ctx["role"]["id"]) if extensions.is_enabled("relations") and relations.enabled() else 0
        role = ctx.get("role")
        if role is not None and "status" not in ctx:
            ctx["status"] = _status(role)
        if role is not None:
            ctx.setdefault("disp", profiles.make_disp(role["id"]))
            ctx.setdefault("phone_avatars", profiles.avatar_versions())
            ctx.setdefault("my_remarks", profiles.remark_map(role["id"]))
        if role is not None and "outage_rooms" not in ctx:                # 拉群 QQ 号掉线：页顶提示到小手机上接着写
            from changri.core import outage
            ctx["outage_rooms"] = [{"id": r["id"], "kind": r["kind"]} for r in outage.rooms_down_for(role["id"])]
        resp = make_response(render_template(template, **ctx))
        return resp

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        # 刚登录（写了登录 cookie）：记下是哪个机器人，所有机器人共用一个端口，后面的请求靠它分给这个机器人（core/phoneserver.py）
        from changri.core.db import current_bot
        bot = current_bot()
        if bot and any(c.startswith((_cookie() + "=", _admin_cookie() + "=")) and not c.startswith((_cookie() + "=;", _admin_cookie() + "=;"))
                       for c in resp.headers.getlist("Set-Cookie")):
            resp.set_cookie("phone_bot", bot, max_age=MAX_AGE, path="/p", httponly=True, samesite="Lax",
                            secure=get_config("phone_https", "0") == "1")
        return resp

    @app.before_request
    def guard():
        if get_config("phone_enabled", "0") != "1" and not request.path.startswith("/p/static"):
            abort(404)
        if (get_config("phone_https", "0") == "1" and request.scheme == "http" and _base_url().startswith("https://")
                and request.path != "/p/health"):
            # 直接打开了 http 端口：跳到 https 网址（https 端口和 http 端口不是同一个，不能只换协议头）
            return redirect(_base_url() + request.full_path.rstrip("?"), 302)
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
            ip = request.remote_addr or "?"          # 经 Caddy 转来的已由 LocalProxyFix 换成真实 IP；X-Real-IP 谁都能伪造，不再看
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

    @app.route("/p/as/<tok>")
    def enter_as(tok):
        """设置平台发来的一次性令牌（管理员以某角色身份打开小手机）。60 秒内有效，用过即废。"""
        key = f"phone_as:{tok}"
        raw = get_config(key, "")
        set_config(key, "")
        try:
            rid, until = (int(x) for x in raw.split("|"))
        except ValueError:
            abort(404)
        role = phonecodes.role_by_id(rid)
        if not role or time.time() > until:
            abort(404)
        code = phonecodes.code_for_role(rid)
        ts = int(time.time())
        resp = redirect(url_for("inbox"))
        resp.set_cookie(_cookie(), f"{rid}.{code}.{ts}.{_sign(app.secret_key, rid, code, ts)}", max_age=MAX_AGE, path="/p", httponly=True,
                        samesite="Lax", secure=get_config("phone_https", "0") == "1")
        return resp

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
                text = (m["content"] or "［图片］") if m["kind"] == "sms" else f"［礼物］{m['title'] or m['content']}"
                threads[other] = {"id": other, "name": name, "preview": text[:40], "time": time.strftime("%m-%d %H:%M", time.localtime(m["created_ts"]))}
        return page("inbox.html", role=role, threads=list(threads.values()), mode="inbox", pub_last=publicfeed.last_line(),
                    ball=masquerade.inbox_row(rid) if extensions.is_enabled("masquerade") and not role["is_npc"] else None)

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
        pics = msgimages.ids_by_msg([m["id"] for m in msgs])
        favs = {r[0] for r in get_conn().execute("SELECT message_id FROM phone_favs WHERE role_id=?", (rid,))}
        items = [{"id": m["id"], "fav": m["id"] in favs, "mine": m["from_role_id"] == rid, "kind": m["kind"], "title": m["title"], "text": m["content"], "imgs": pics.get(m["id"], []),
                  "time": time.strftime("%m-%d %H:%M", time.localtime(m["created_ts"]))} for m in msgs]
        blk = blocks.get_block(role["id"], other)
        return page("thread.html", role=role, me=role["name"], other=who["name"], other_id=other, items=items, mode="thread",
                    blocked=blk, can_recall=bool(messaging.last_recallable(role["id"])),
                    items_catalog=gifts.catalog(), only_preset=gifts.only_preset(), draft_gift=request.args.get("gift", ""),
                    allow_sign=get_config("allow_custom_sign", "0") == "1", images_ok=msgimages.enabled(), image_mb=msgimages.max_bytes() // 1024 // 1024)

    @app.route("/p/img/<int:img_id>")
    def msg_image(img_id: int):
        """短信里的图：只有收发双方登录后能看。"""
        role, bad = need_login()
        if bad:
            return bad
        row = msgimages.get(img_id)
        if not row or role["id"] not in (row["from_role_id"], row["to_role_id"]):
            abort(404)
        resp = make_response(row["data"])
        resp.headers["Content-Type"] = row["mime"]
        resp.headers["Cache-Control"] = "private, max-age=86400"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

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
        images = []
        up = request.files.get("image")
        if up and up.filename:
            data = up.read(msgimages.max_bytes() + 1)
            why = msgimages.check(data)
            if why:
                flash(why)
                return back
            images = [data]
        bad_reason = phonegate.check_send(role["name"], "短信" if kind == "sms" else "礼物", gift, text)
        if bad_reason:
            flash(bad_reason)
            return back
        sign = request.form.get("sign", "").strip() if get_config("allow_custom_sign", "0") == "1" else ""
        try:
            ok, msg = loopbridge.run(sendmsg.send(role, target, kind, gift, text, sign, _send_fn(), images, gift_by_name_ok=True))
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
        from changri.core.qqsend import recall_group_message

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

    # ---------- 各功能页面共用的小工具（功能页面本身在 features/<功能>/phone.py） ----------

    def _chat_or_404():
        if not (extensions.is_enabled("phone") and (extensions.is_enabled("chatroom") or extensions.is_enabled("groupchat") or extensions.is_enabled("precall"))):
            abort(404)

    def _ext_or_404(ext_id: str):
        if not extensions.is_enabled(ext_id):
            abort(404)

    def _fmt(ts) -> str:
        return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else ""

    app.jinja_env.filters["rel_time_short"] = _fmt

    def _push(role_id: int, text: str) -> bool:
        try:
            ok, _ = loopbridge.run(messaging.push_to_role(role_id, text, _send_fn()))
            return bool(ok)
        except Exception:  # noqa: BLE001
            return False

    def _gate(role, field: str, *texts: str) -> str:
        """发送类入口的闸：暂停通讯、违禁词（档期由各功能自己的规则管，群里同一功能也不看季度）。"""
        if phonegate.comm_paused():
            return phonegate.PAUSE_MSG
        return "内容里有不能发送的词，请改一改再发" if phonegate.blocked_hit(role["name"], field, *texts) else ""

    @app.route("/p/me/discover")
    def discover():
        role, bad = need_login()
        if bad:
            return bad
        return page("discover.html", role=role, mode="discover", collecting=len(collect.open_now()), rel_unread=relations.unread_total(role["id"]) if extensions.is_enabled("relations") else 0,
                    on={k: extensions.is_enabled(k) for k in ("lovemail", "wishes", "bottle", "relations", "moments", "masquerade", "games", "chatroom", "groupchat", "song", "precall")},
                    moments_latest=moments.latest() if extensions.is_enabled("moments") else None,
                    masq_on=extensions.is_enabled("masquerade") and masquerade.is_on(),
                    precall_badge=0)

    # ---------- 时间线、搜索与收藏 ----------

    @app.route("/p/me/timeline")
    def timeline_page():
        role, bad = need_login()
        if bad:
            return bad
        from changri.core import overview
        view = "stats" if request.args.get("view") == "stats" else "timeline"
        report = ""
        if view == "stats":
            from changri.core import mystats
            report = mystats.build(role["id"])
        return page("timeline.html", role=role, mode="discover", view=view, report=report, days=overview.timeline_days(role["id"]) if view == "timeline" else [], chat_on=extensions.is_enabled("chatroom") or extensions.is_enabled("groupchat"))

    def _snip(text: str, q: str):
        i = text.lower().find(q.lower())
        a = max(0, i - 14)
        return (("…" if a else "") + text[a:i], text[i:i + len(q)], text[i + len(q):i + len(q) + 28])

    @app.route("/p/me/search")
    def search_page():
        role, bad = need_login()
        if bad:
            return bad
        rid = role["id"]
        q = request.args.get("q", "").strip()[:30]
        groups, favs = [], []
        like = "%" + q.replace("%", "").replace("_", "") + "%"
        with get_conn() as c:
            if q:
                feats = [("朋友圈", "moments_feed", "moments"), ("心动信箱", "lovemail_page", "lovemail"), ("心愿", "wishes_page", "wishes"), ("漂流瓶", "bottle_page", "bottle"),
                         ("关系线", "rel_page", "relations"), ("小游戏", "games_page", "games"), ("匿名舞会", "masq_page", "masquerade"), ("邀约 聊天室 多人群聊", "appoint_page", "chatroom"),
                         ("时间线 统计", "timeline_page", ""), ("我的待回", "todo_page", ""), ("收集", "collect_page", ""), ("使用指南", "guide", ""), ("外观 字号 主题", "profile", ""), ("点歌台 点歌", "songs_page", "song")]
                hit = [{"title": n.split()[0], "sub": "功能", "url": url_for(ep)} for n, ep, ext in feats if q.lower() in n.lower() and (not ext or extensions.is_enabled(ext) or (ext == "chatroom" and extensions.is_enabled("groupchat")))]
                if hit:
                    groups.append({"label": "功能", "items": hit})
                ppl = c.execute("SELECT id, name FROM roles WHERE id!=? AND name LIKE ? ORDER BY name LIMIT 8", (rid, like)).fetchall()
                if ppl:
                    groups.append({"label": "联系人", "items": [{"title": r["name"], "sub": "点击开始对话", "url": url_for("thread", other=r["id"])} for r in ppl]})
                rows = c.execute("SELECT m.id, m.kind, m.title, m.content, m.created_ts, m.from_role_id, f.name AS fname, t.name AS tname FROM messages m JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id "
                                 "WHERE (m.from_role_id=? OR m.to_role_id=?) AND (m.content LIKE ? OR m.title LIKE ?) ORDER BY m.id DESC LIMIT 12", (rid, rid, like, like)).fetchall()
                if rows:
                    items = []
                    for m in rows:
                        other = m["tname"] if m["from_role_id"] == rid else m["fname"]
                        oid = c.execute("SELECT id FROM roles WHERE name=?", (other,)).fetchone()
                        body = ((m["title"] + " ") if m["kind"] == "gift" else "") + (m["content"] or "")
                        pre, hit_, post = _snip(body, q) if q.lower() in body.lower() else ("", body[:20], "")
                        items.append({"title": other, "sub": ("我" if m["from_role_id"] == rid else other) + " · " + _fmt(m["created_ts"]), "pre": pre, "hit": hit_, "post": post,
                                      "url": url_for("thread", other=oid["id"]) + f"#m{m['id']}" if oid else ""})
                    groups.append({"label": "短信和礼物", "items": items})
                if extensions.is_enabled("moments"):
                    ms = c.execute("SELECT m.id, m.content, m.created_ts, r.name FROM moments m JOIN roles r ON r.id=m.role_id WHERE m.deleted=0 AND m.content LIKE ? ORDER BY m.id DESC LIMIT 8", (like,)).fetchall()
                    if ms:
                        groups.append({"label": "朋友圈", "items": [dict(zip(("title", "sub", "pre", "hit", "post", "url"), (r["name"], _fmt(r["created_ts"]), *_snip(r["content"], q), url_for("moments_feed")))) for r in ms]})
            else:
                for m in c.execute("SELECT m.id, m.kind, m.title, m.content, m.created_ts, m.from_role_id, m.to_role_id, f.name AS fname, t.name AS tname FROM phone_favs v JOIN messages m ON m.id=v.message_id "
                                   "JOIN roles f ON f.id=m.from_role_id JOIN roles t ON t.id=m.to_role_id WHERE v.role_id=? ORDER BY v.created_ts DESC LIMIT 100", (rid,)):
                    other_id = m["to_role_id"] if m["from_role_id"] == rid else m["from_role_id"]
                    favs.append({"id": m["id"], "who": "我" if m["from_role_id"] == rid else m["fname"], "with": m["tname"] if m["from_role_id"] == rid else m["fname"],
                                 "text": ("［礼物］" + m["title"] + " " if m["kind"] == "gift" else "") + (m["content"] or ""), "time": _fmt(m["created_ts"]), "url": url_for("thread", other=other_id) + f"#m{m['id']}"})
        return page("search.html", role=role, mode="discover", q=q, groups=groups, favs=favs)

    @app.post("/p/me/fav")
    def fav_toggle():
        role, bad = need_login()
        if bad:
            return bad
        mid = request.form.get("id", type=int) or 0
        with get_conn() as c:
            m = c.execute("SELECT from_role_id, to_role_id FROM messages WHERE id=?", (mid,)).fetchone()
            if not m or role["id"] not in (m["from_role_id"], m["to_role_id"]):
                abort(404)
            if c.execute("DELETE FROM phone_favs WHERE role_id=? AND message_id=?", (role["id"], mid)).rowcount == 0:
                c.execute("INSERT INTO phone_favs(role_id, message_id, created_ts) VALUES(?,?,?)", (role["id"], mid, int(time.time())))
        nxt = request.form.get("next", "")
        return redirect(nxt if nxt.startswith("/p/me/") else url_for("search_page"))

    # ---------- 待办与个人页 ----------

    @app.route("/p/me/todo")
    def todo_page():
        role, bad = need_login()
        if bad:
            return bad
        from changri.core import overview
        return page("todo.html", role=role, mode="discover", items=overview.todo_items(role))

    @app.route("/p/me/profile")
    def profile():
        role, bad = need_login()
        if bad:
            return bad
        return page("profile.html", role=role, mode="profile", season=season.running(), https=get_config("phone_https", "0") == "1",
                    my_nick=profiles.nick_map().get(role["name"], ""), nick_max=profiles.NICK_MAX)

    # ---------- 房间小工具（邀约、戏录、前置电话共用） ----------

    def _room_row(r) -> dict:
        from changri.core.overview import slot_text
        return {"id": r["id"], "kind": r["kind"], "slot": slot_text(r), "who": "、".join(m["name"] for m in rooms_core.members(r["id"])),
                "web": not r["pool_id"] and r["status"] == "open", "grp": rooms_core.group_info(r)}

    # ---------- 管理员手机：设置平台发一枚一次性令牌进来，不另设密码；能切到任意角色、看所有房间的戏录、开关暂停通讯 ----------

    def _admin_cookie() -> str:
        return _cookie().replace("phone_auth", "phone_admin")

    def is_admin() -> bool:
        raw = request.cookies.get(_admin_cookie(), "")
        try:
            ts, sig = raw.split(".")
            ts = int(ts)
        except ValueError:
            return False
        return hmac.compare_digest(sig, _sign(app.secret_key, 0, "admin", ts)) and time.time() - ts < 12 * 3600

    def _admin_or_404():
        if not is_admin():
            abort(404)

    @app.route("/p/admin/enter/<tok>")
    def admin_enter(tok):
        key = f"phone_admin:{tok}"
        raw = get_config(key, "")
        set_config(key, "")
        try:
            until = int(raw)
        except ValueError:
            abort(404)
        if time.time() > until:
            abort(404)
        ts = int(time.time())
        resp = redirect(url_for("admin_home"))
        resp.set_cookie(_admin_cookie(), f"{ts}.{_sign(app.secret_key, 0, 'admin', ts)}", max_age=12 * 3600, path="/p", httponly=True,
                        samesite="Lax", secure=get_config("phone_https", "0") == "1")
        return resp

    @app.route("/p/admin")
    def admin_home():
        _admin_or_404()
        with get_conn() as c:
            rs = c.execute("SELECT id, name FROM roles ORDER BY name").fetchall()
            n_open = c.execute("SELECT COUNT(*) FROM rooms WHERE status='open'").fetchone()[0]
        return page("admin_home.html", mode="discover", roles=rs, paused=phonegate.comm_paused(), n_open=n_open)

    @app.route("/p/admin/pause", methods=["POST"])
    def admin_pause():
        _admin_or_404()
        set_config("phone_paused", "0" if phonegate.comm_paused() else "1")
        return redirect(url_for("admin_home"))

    @app.route("/p/admin/as/<int:rid>", methods=["POST"])
    def admin_as(rid):
        _admin_or_404()
        role = phonecodes.role_by_id(rid)
        if not role:
            abort(404)
        code = phonecodes.code_for_role(rid)
        ts = int(time.time())
        resp = redirect(url_for("inbox"))
        resp.set_cookie(_cookie(), f"{rid}.{code}.{ts}.{_sign(app.secret_key, rid, code, ts)}", max_age=MAX_AGE, path="/p", httponly=True,
                        samesite="Lax", secure=get_config("phone_https", "0") == "1")
        return resp

    @app.route("/p/admin/rooms")
    def admin_rooms():
        _admin_or_404()
        from changri.core import roomlog
        items = roomlog.archive_all()
        for x in items:
            x["who"] = "、".join(m["name"] for m in rooms_core.members(x["id"]))
        items.sort(key=lambda x: x["status"] != "open")
        return page("archive.html", mode="discover", items=items, admin=True)

    @app.route("/p/admin/rooms/<int:rid>")
    def admin_room_view(rid):
        _admin_or_404()
        r = rooms_core.get(rid)
        if not r:
            abort(404)
        return ctx.archive_render(r, None)          # 戏录页的渲染，在 features/archive/phone.py 里挂到 ctx 上

    @app.route("/p/admin/logout", methods=["POST"])
    def admin_logout():
        resp = redirect(url_for("entry"))
        resp.delete_cookie(_admin_cookie(), path="/p")
        return resp


    def _room_for(role, rid):
        r = rooms_core.get(rid)
        if not r or not rooms_core.is_member(rid, role["id"]):
            abort(404)
        return r

    # ---------- JSON 回应（朋友圈、匿名舞会、小游戏、聊天室共用） ----------

    def _mjson(ok: bool, msg: str = "", **extra):
        return jsonify(ok=ok, msg=msg, **extra), (200 if ok else 400)

    @app.route("/p/me/public")
    def public_page():
        role, bad = need_login()
        if bad:
            return bad
        return page("public.html", role=role, items=publicfeed.recent(), mode="public")

    @app.post("/p/me/nickname")
    def set_nickname():
        role, bad = need_login()
        if bad:
            return bad
        nick = request.form.get("nick", "").strip()
        why = _gate(role, "微信名", nick) if nick else ""
        if why:
            flash(why)
        else:
            flash(profiles.set_nick(role["id"], nick)[1])
        return redirect(url_for("profile"))

    @app.post("/p/me/remark")
    def set_remark():
        role, bad = need_login()
        if bad:
            return bad
        tid = request.form.get("target", "")
        text = request.form.get("remark", "").strip()
        why = _gate(role, "备注", text) if text else ""
        if why or not tid.isdigit():
            flash(why or "找不到这个人")
            return redirect(url_for("inbox"))
        flash(profiles.set_remark(role["id"], int(tid), text)[1])
        return redirect(url_for("thread", other=int(tid)))

    @app.post("/p/me/avatar")
    def avatar_upload():
        role, bad = need_login()
        if bad:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        f = request.files.get("avatar")
        ok, msg, v = profiles.set_avatar(role["id"], f.read(profiles.MAX_UPLOAD + 1) if f else b"")
        return jsonify(ok=ok, msg=msg, v=v)

    @app.post("/p/me/avatar/delete")
    def avatar_delete():
        role, bad = need_login()
        if bad:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        profiles.remove_avatar(role["id"])
        return jsonify(ok=True, msg="已换回名字头像")

    @app.route("/p/me/avatar/<name>")
    def avatar_image(name: str):
        role, bad = need_login()
        if bad:
            abort(404)
        data = profiles.avatar_data(name)
        if not data:
            abort(404)
        resp = make_response(data)
        resp.headers["Content-Type"] = "image/jpeg"
        return resp

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

    ctx = SimpleNamespace(_chat_or_404=_chat_or_404, _csrf=_csrf, _ext_or_404=_ext_or_404, _fmt=_fmt, _gate=_gate, _mjson=_mjson, _push=_push, _room_for=_room_for, _room_row=_room_row, _send_fn=_send_fn, current=current, need_login=need_login, page=page)
    from changri.features import registry
    for mod in registry.phone_pages():          # 各功能自己的小手机页面：features/<功能>/phone.py
        importlib.import_module(mod).register(app, ctx)

    return app
