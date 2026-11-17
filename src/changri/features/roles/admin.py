"""设置平台 · 角色与群。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, request, url_for
from changri.core import roles as roles_core
from changri.core.db import get_conn


def register(app, login_required):
    @app.route("/roles")
    @login_required
    def roles_page():
        return redirect(url_for("dashboard") + "#roles")           # 角色与群并进总览（折叠）

    @app.post("/roles/<int:role_id>/slot")
    @login_required
    def add_slot(role_id):
        ok, res = roles_core.add_slot(role_id, request.form.get("real_gid", "").strip())
        if not ok:
            flash(res)
            return redirect(url_for("dashboard") + "#roles")
        with get_conn() as conn:
            name = conn.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()["name"]
        flash("群位已登记，请到那个群里发认群码")
        return redirect(url_for("dashboard", code=res, role=name) + "#roles")

    @app.post("/roles/<int:role_id>/delete")
    @login_required
    def delete_role(role_id):
        roles_core.delete_role(role_id)
        flash("角色已删除")
        return redirect(url_for("dashboard") + "#roles")

    @app.template_filter("ts")
    def _ts_filter(v):
        import time as _t
        return _t.strftime("%m-%d %H:%M", _t.localtime(v)) if v else "—"
