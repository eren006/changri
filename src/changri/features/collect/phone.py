"""小手机 · 收集。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import collect
from flask import flash, redirect, request, url_for


def register(app, ctx):
    _gate, need_login, page = ctx._gate, ctx.need_login, ctx.page

    @app.route("/p/me/collect")
    def collect_page():
        role, bad = need_login()
        if bad:
            return bad
        rows = []
        for c in collect.open_now():
            k = collect.active_round(c)
            mine = next((i for i in collect.items(c["id"], k) if i["role_id"] == role["id"]), None)
            rows.append({"id": c["id"], "title": c["title"], "prompt": c["prompt"], "end": collect.window(c, k)[1], "day": k + 1 if c["repeat_days"] > 1 else 0,
                         "mine": mine["content"] if mine else ""})
        return page("collect.html", role=role, rows=rows, mode="discover")

    @app.post("/p/me/collect/submit")
    def collect_submit():
        role, bad = need_login()
        if bad:
            return bad
        cid = request.form.get("cid", "")
        why = _gate(role, "收集", request.form.get("text", ""))
        if why:
            flash(why)
        else:
            ok, msg = collect.submit(role["id"], request.form.get("text", ""), int(cid) if cid.isdigit() else None)
            flash(msg)
        return redirect(url_for("collect_page"))
