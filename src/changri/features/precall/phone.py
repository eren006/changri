"""小手机 · 前置电话（照老系统小手机的滑卡玩法：rp_archive 的 player_precall_*）。规则在 core/precall.py。
三个分页：选人（滑卡）/ 我的电话 / 我的资料；接通后是电话聊天页（每 3 秒拉一次新消息）。纯网页，不建 QQ 群。"""
import time

from flask import Response, abort, flash, jsonify, redirect, request, url_for

from changri.core import extensions, phonegate, precall


def register(app, ctx):
    _gate, need_login, page = ctx._gate, ctx.need_login, ctx.page

    def _pre_or_404():
        if not (extensions.is_enabled("phone") and extensions.is_enabled("precall")):
            abort(404)

    def _photo_url(p):
        return url_for("precall_photo", pid=p["pid"], v=p["updated_ts"]) if p and p["has_photo"] else ""

    @app.route("/p/me/precall")
    def precall_page():
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        k = precall.cfg()
        view = request.args.get("view", "deck")
        view = view if view in ("deck", "calls", "profile") else "deck"
        ok = precall.ready(role["id"])
        if view == "deck" and not ok:
            view = "profile"
        cands = precall.candidates(role["id"]) if view == "deck" else []
        for c in cands:
            c["photo"] = url_for("precall_photo", pid=c["pid"], v=c["v"]) if c["has_photo"] else ""
        me = precall.profile(role["id"])
        calls = precall.sessions_for(role["id"]) if view == "calls" else []
        for c in calls:
            c["time"] = time.strftime("%m-%d %H:%M", time.localtime(c["ts"]))
        return page("precall.html", role=role, mode="precall", pview=view, cfg=k, genders=precall.GENDERS, me=dict(me) if me else None,
                    ready=ok, cands=cands, left=precall.left(role["id"], k), photo_url=_photo_url(me), calls=calls,
                    call_badge=precall.badge(role["id"]), bio_max=precall.BIO_MAX)

    @app.post("/p/me/precall/profile")
    def precall_profile():
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        bio = request.form.get("bio", "")
        why = _gate(role, "前置电话资料", bio)
        if why:
            flash(why)
            return redirect(url_for("precall_page", view="profile"))
        f = request.files.get("photo")
        raw = f.read(12 * 1024 * 1024 + 1) if f and f.filename else None
        if raw and len(raw) > 12 * 1024 * 1024:
            flash("图片太大了，换一张小一点的。")
            return redirect(url_for("precall_page", view="profile"))
        ok, msg = precall.save_profile(role["id"], request.form.get("gender", "x"), bio, raw, request.form.get("remove_photo") == "1")
        flash(("✅ " if ok else "❌ ") + msg)
        return redirect(url_for("precall_page", view="deck" if ok else "profile"))

    @app.route("/p/me/precall/photo/<pid>")
    def precall_photo(pid):
        """照片按随机编号取，网址里不带角色名（匿名时别人看不出这是谁）。"""
        role = ctx.current()
        data = precall.photo(pid, role["id"]) if role else None
        if not data:
            abort(404)
        resp = Response(data, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=3600"        # 地址带版本号，换照片地址就变
        return resp

    @app.post("/p/me/precall/swipe")
    def precall_swipe():
        """选 TA：必须是玩家在确认框里点了确定（confirm=1）；对象用随机编号，页面上不出现角色名。"""
        _pre_or_404()
        role = ctx.current()
        if not role:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        if phonegate.comm_paused():
            return jsonify(ok=False, msg=phonegate.PAUSE_MSG), 400
        if request.form.get("confirm") != "1":
            return jsonify(ok=False, msg="参数不对"), 400
        r = precall.like(role["id"], request.form.get("to", ""))
        if not r["ok"]:
            return jsonify(ok=False, msg=r["msg"], left=r.get("left")), 400
        return jsonify(ok=True, matched=r["matched"], left=r["left"],
                       session=url_for("precall_call", session_id=r["session_id"]) if r["matched"] else "")

    @app.route("/p/me/precall/call/<int:session_id>")
    def precall_call(session_id):
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        s = precall.session_of(session_id, role["id"])
        if not s:
            flash("没有找到这个电话。")
            return redirect(url_for("precall_page", view="calls"))
        other = precall.other_of(s, role["id"])
        msgs = precall.messages(session_id)
        last_id = msgs[-1]["id"] if msgs else 0
        if not ctx.is_admin():                                 # 管理员只读代看不算已读
            precall.mark_read(s, role["id"], last_id)
        prof = precall.profile(other)
        return page("precall_call.html", role=role, mode="thread", cfg=precall.cfg(), ses=dict(s), other_name=precall.name_of(other, reveal=True),
                    other_bio=prof["bio"] if prof else "", other_photo=_photo_url(prof),
                    msgs=[{"id": m["id"], "mine": m["from_id"] == role["id"], "text": m["text"]} for m in msgs], last_id=last_id,
                    mine_n=precall.mine_count(session_id, role["id"]), why=phonegate.PAUSE_MSG if phonegate.comm_paused() else "")

    @app.get("/p/me/precall/call/<int:session_id>/poll")
    def precall_poll(session_id):
        _pre_or_404()
        role = ctx.current()
        s = precall.session_of(session_id, role["id"]) if role else None
        if not s:
            return jsonify(ok=False), 404
        after = request.args.get("after", "0")
        msgs = precall.messages(session_id, int(after) if after.isdigit() else 0)
        if msgs and not ctx.is_admin():
            precall.mark_read(s, role["id"], msgs[-1]["id"])
        return jsonify(ok=True, ended=bool(s["ended"]),
                       msgs=[{"id": m["id"], "mine": m["from_id"] == role["id"], "text": m["text"],
                              "time": time.strftime("%m-%d %H:%M", time.localtime(m["created_ts"]))} for m in msgs])

    @app.post("/p/me/precall/call/<int:session_id>/send")
    def precall_send(session_id):
        _pre_or_404()
        role = ctx.current()
        if not role:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        text = request.form.get("text", "")
        why = _gate(role, "前置电话", text)
        if why:
            return jsonify(ok=False, msg=why), 400
        r = precall.send(role["id"], session_id, text)
        return jsonify(**r), (200 if r["ok"] else 400)

    @app.post("/p/me/precall/call/<int:session_id>/end")
    def precall_end(session_id):
        _pre_or_404()
        role = ctx.current()
        if not role or not precall.end(session_id, role["id"]):
            return jsonify(ok=False, msg="没有找到这个电话"), 404
        return jsonify(ok=True)
