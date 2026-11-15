"""小手机 · 前置电话。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import extensions, loopbridge, precall, rooms as rooms_core
from flask import abort, flash, redirect, request, url_for


def register(app, ctx):
    _gate, _room_row, need_login, page = ctx._gate, ctx._room_row, ctx.need_login, ctx.page

    def _pre_or_404():
        if not (extensions.is_enabled("phone") and extensions.is_enabled("precall")):
            abort(404)

    def _dial_flow(me, no):
        from changri.plugins import precall as precall_plugin
        return loopbridge.run(precall_plugin.dial_flow(me, no), timeout=120)

    app.config["PRECALL_DIAL"] = _dial_flow

    @app.route("/p/me/precall")
    def precall_page():
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        return page("precall.html", role=role, mode="discover", mine=precall.entry_of(role["id"]), items=precall.visible(), usage=precall.usage(role["id"]),
                    calls=[_room_row(r) for r in rooms_core.my_open(role["id"]) if r["kind"] == "前置电话"], text_max=precall.TEXT_MAX)

    @app.post("/p/me/precall/submit")
    def precall_submit():
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        text = request.form.get("text", "")
        why = _gate(role, "前置电话", text)
        if why:
            flash(why)
        else:
            flash(precall.submit(role["id"], text)[1])
        return redirect(url_for("precall_page"))

    @app.post("/p/me/precall/withdraw")
    def precall_withdraw():
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        flash("已撤回你的前置电话" if precall.withdraw(role["id"]) else "你还没有提交前置电话")
        return redirect(url_for("precall_page"))

    @app.post("/p/me/precall/dial")
    def precall_dial():
        _pre_or_404()
        role, bad = need_login()
        if bad:
            return bad
        no = request.form.get("no", type=int) or 0
        why = _gate(role, "前置电话")
        if why:
            flash(why)
            return redirect(url_for("precall_page"))
        try:
            ok, msg, rid = app.config["PRECALL_DIAL"](role, no)
        except Exception as e:  # noqa: BLE001
            ok, msg, rid = False, f"拨打失败：{str(e)[:60]}", 0
        flash(msg.split("\n")[0] if ok else f"❌ {msg}")
        return redirect(url_for("room_page", rid=rid) if ok and rid else url_for("precall_page"))
