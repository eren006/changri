"""设置平台 · 前置电话。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import precall as precall_core


def register(app, login_required):
    @app.route("/precall")
    @login_required
    def precall_admin():
        return render_template("precall.html", k=precall_core.cfg(), entries=precall_core.all_entries(), groups=precall_core.groups(), calls=precall_core.calls())

    @app.post("/precall/assign")
    @login_required
    def precall_assign():
        nums = [int(x) for x in request.form.getlist("no") if x.isdigit()]
        group = request.form.get("group", "").strip() or request.form.get("new_group", "").strip()
        if request.form.get("ungroup") == "1":
            group = ""
        if not nums:
            flash("先勾几条前置电话")
        else:
            flash(precall_core.assign(nums, group)[1])
        return redirect(url_for("precall_admin"))

    @app.post("/precall/group/<name>")
    @login_required
    def precall_group_op(name):
        op = request.form.get("op")
        if op == "open":
            precall_core.set_group_open(name, True)
            flash(f"「{name}」已设为可选：玩家现在能看到并拨打这个组的前置电话")
        elif op == "close":
            precall_core.set_group_open(name, False)
            flash(f"「{name}」不再开放选择（已通的电话不受影响）")
        elif op == "delete":
            precall_core.delete_group(name)
            flash(f"已删除「{name}」，里面的前置电话回到未分组")
        return redirect(url_for("precall_admin"))

    @app.post("/precall/entry/<int:no>")
    @login_required
    def precall_entry_op(no):
        op = request.form.get("op")
        if op == "hide":
            precall_core.set_hidden(no, True)
        elif op == "show":
            precall_core.set_hidden(no, False)
        elif op == "delete":
            precall_core.remove(no)
            flash(f"已删除 #{no}")
        return redirect(url_for("precall_admin"))

    @app.post("/precall/off")
    @login_required
    def precall_off():
        precall_core.set_on(False)
        flash("前置电话已关闭（数据保留，再打开还在）")
        return redirect(url_for("extensions_page"))

    @app.post("/precall/clear")
    @login_required
    def precall_clear():
        if request.form.get("confirm") == "清空":
            precall_core.clear_all()
            flash("前置电话的提交、分组和拨打记录已全部清空")
        else:
            flash("没有清空：确认框里要写「清空」两个字")
        return redirect(url_for("precall_admin"))
