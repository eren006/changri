"""设置平台 · 加百列闹钟。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import alarm as alarm_core


def register(app, login_required):
    @app.route("/alarm")
    @login_required
    def alarm_page():
        return render_template("alarm.html", rows=alarm_core.group_list(), players=len(alarm_core.player_ids()), max=alarm_core.GROUP_MAX)

    @app.post("/alarm/add")
    @login_required
    def alarm_add():
        items, errs = alarm_core.parse_group_lines(request.form.get("lines", ""))
        if errs:
            flash("没加上：" + "；".join(errs[:3]) + ("……" if len(errs) > 3 else ""), "error")
            return redirect(url_for("alarm_page"))
        if not items:
            flash("还没写内容。一行一条：9月12日 20:00 记得交日报", "error")
            return redirect(url_for("alarm_page"))
        n, err = alarm_core.group_add(items)
        flash(err or f"已添加 {n} 条群体闹钟" + ("" if n == len(items) else f"（{len(items) - n} 条和已有的完全相同，跳过了）"))
        return redirect(url_for("alarm_page"))

    @app.post("/alarm/<int:alarm_id>/delete")
    @login_required
    def alarm_delete(alarm_id):
        alarm_core.group_delete(alarm_id)
        flash("已撤销")
        return redirect(url_for("alarm_page"))

    @app.post("/alarm/clear")
    @login_required
    def alarm_clear():
        flash(f"已撤销全部 {alarm_core.group_delete(None)} 条")
        return redirect(url_for("alarm_page"))
