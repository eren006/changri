"""设置平台 · QQ 管理员。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import admins as admins_core


def register(app, login_required):
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
