"""设置平台 · 目击。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, url_for
from changri.core import sighting as sighting_core


def register(app, login_required):
    @app.route("/sightings")
    @login_required
    def sighting_page():
        return render_template("sightings.html", rows=sighting_core.recent(None, 200), st=sighting_core.stats())

    @app.post("/sightings/clear")
    @login_required
    def sighting_clear():
        sighting_core.clear_all()
        flash("目击记录已清空")
        return redirect(url_for("sighting_page"))
