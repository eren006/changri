"""设置平台 · 前置电话（照老系统管理端的前置电话卡片）：新开一轮、隐藏某人的资料、看所有电话（真名 + 聊天记录）、替人挂断。
规则（每轮选几个、单向/双向、翻谁的卡、匿名、句数上限）在「功能与设置」的「前置电话」组里。"""
from flask import Response, abort, flash, redirect, render_template, request, url_for

from changri.core import precall as precall_core


def register(app, login_required):
    @app.route("/precall")
    @login_required
    def precall_admin():
        return render_template("precall.html", k=precall_core.cfg(), modes=precall_core.MODES, genders=precall_core.GENDERS,
                               profiles=precall_core.admin_profiles(), sessions=precall_core.admin_sessions(), likes=precall_core.admin_likes())

    @app.route("/precall/photo/<int:role_id>")
    @login_required
    def precall_admin_photo(role_id):
        p = precall_core.profile(role_id)
        data = precall_core.photo(p["pid"], role_id) if p else None
        if not data:
            abort(404)
        return Response(data, mimetype="image/jpeg")

    @app.post("/precall/round")
    @login_required
    def precall_round():
        flash(f"已经开始第 {precall_core.new_round()} 轮：大家的选人次数重新算，已经接通的电话保留。")
        return redirect(url_for("precall_admin"))

    @app.post("/precall/hide/<int:role_id>")
    @login_required
    def precall_hide(role_id):
        hide = request.form.get("hide") == "1"
        precall_core.set_hidden(role_id, hide)
        flash("已隐藏：别人翻不到 TA 的资料了" if hide else "已恢复：别人又能翻到 TA 的资料了")
        return redirect(url_for("precall_admin"))

    @app.post("/precall/end/<int:session_id>")
    @login_required
    def precall_admin_end(session_id):
        flash("已挂断这个电话" if precall_core.end(session_id) else "没有这个电话")
        return redirect(url_for("precall_admin"))
