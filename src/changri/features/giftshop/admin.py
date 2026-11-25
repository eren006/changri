"""设置平台 · 礼品店。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import Response, flash, redirect, render_template, request, url_for
from changri.core import gifts as gifts_core
from changri.core.db import get_config


def register(app, login_required):
    @app.route("/giftshop")
    @login_required
    def giftshop_page():
        return render_template("giftshop.html", items=gifts_core.items(), only=get_config("gift_only_preset", "0") == "1",
                               ai_prompt=gifts_core.ai_import_prompt())

    @app.post("/giftshop/add")
    @login_required
    def giftshop_add():
        ok, msg = gifts_core.add(request.form.get("name", ""), request.form.get("note", ""))
        flash(msg, "message" if ok else "error")
        return redirect(url_for("giftshop_page"))

    @app.post("/giftshop/<int:item_id>/delete")
    @login_required
    def giftshop_delete(item_id):
        gifts_core.remove(item_id)
        flash("已下架")
        return redirect(url_for("giftshop_page"))

    @app.post("/giftshop/clear")
    @login_required
    def giftshop_clear():
        flash(f"已清空，下架了 {gifts_core.clear()} 件")
        return redirect(url_for("giftshop_page"))

    @app.post("/giftshop/import")
    @login_required
    def giftshop_import():
        n, skipped = gifts_core.import_text(request.form.get("lines", ""))
        flash(f"新增 {n} 件" + (f"；跳过 {len(skipped)} 件：" + "；".join(skipped[:3]) if skipped else ""))
        return redirect(url_for("giftshop_page"))

    @app.route("/giftshop/export")
    @login_required
    def giftshop_export():
        return Response(gifts_core.export_text(), mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": "attachment; filename=giftshop.txt"})
