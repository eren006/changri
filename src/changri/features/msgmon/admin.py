"""设置平台 · 消息监听。由 changri.admin.create_app 调用 register(app, login_required)。"""
import time

from flask import flash, jsonify, redirect, render_template, request, url_for

from changri.core import msgmon


def _view(x: dict) -> dict:
    return {**x, "time": time.strftime("%H:%M:%S", time.localtime(x["ts"]))}


def register(app, login_required):
    @app.route("/monitor")
    @login_required
    def msgmon_page():
        return render_template("msgmon.html", items=[_view(x) for x in msgmon.since(0, msgmon.KEEP)], on=msgmon.enabled(), keep=msgmon.KEEP)

    @app.route("/monitor/feed")
    @login_required
    def msgmon_feed():
        return jsonify(items=[_view(x) for x in msgmon.since(request.args.get("after", 0, type=int))])

    @app.post("/monitor/clear")
    @login_required
    def msgmon_clear():
        msgmon.clear()
        flash("已清空")
        return redirect(url_for("msgmon_page"))
