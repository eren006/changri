"""设置平台 · 心动信。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
import re
from flask import flash, redirect, render_template, request, url_for
from changri.core import lovemail as lovemail_core
from changri.core.announce import get_announce
from changri.core.db import get_config, get_conn, set_config


def register(app, login_required):
    @app.route("/lovemail")
    @login_required
    def lovemail_page():
        return render_template("lovemail.html", pool=lovemail_core.pool_all(), announce=get_announce(),
                               at=get_config("lovemail_delivery_time", "22:00"), last=get_config("lovemail_last_run", "—"),
                               pending_now=get_config("lovemail_deliver_now", "0") == "1", open_=get_config("lovemail_enabled", "1") != "0")

    @app.post("/lovemail/open")
    @login_required
    def lovemail_open():
        set_config("lovemail_enabled", "1" if request.form.get("on") else "0")
        return redirect(url_for("lovemail_page"))

    @app.post("/lovemail/time")
    @login_required
    def lovemail_time():
        at = request.form.get("at", "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            flash("时间要写成 22:00 这样（24 小时制）")
        else:
            set_config("lovemail_delivery_time", at)
            flash(f"已改：每天 {at} 自动派送")
        return redirect(url_for("lovemail_page"))

    @app.post("/lovemail/deliver")
    @login_required
    def lovemail_deliver():
        set_config("lovemail_deliver_now", "1")   # 机器人进程每 30 秒看一次这个标记
        flash("已请求立即派送，机器人会在 30 秒内处理")
        return redirect(url_for("lovemail_page"))

    @app.post("/lovemail/<int:mail_id>/delete")
    @login_required
    def lovemail_delete(mail_id):
        with get_conn() as conn:
            conn.execute("DELETE FROM lovemails WHERE id=? AND status='pool'", (mail_id,))
        return redirect(url_for("lovemail_page"))
