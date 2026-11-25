"""设置平台 · 图片管理。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import abort, flash, make_response, redirect, render_template, request, url_for
from changri.core import imagemgr as imagemgr_core, msgimages as msgimages_core
from changri.core.db import get_config, set_config


def register(app, login_required):
    @app.route("/images")
    @login_required
    def images_page():
        src = request.args.get("src", "sms")
        if not imagemgr_core.valid(src):
            src = "sms"
        page = max(1, request.args.get("page", 1, type=int))
        per = 48
        rows = imagemgr_core.listing(src, per + 1, (page - 1) * per)
        return render_template("images.html", src=src, sources=imagemgr_core.SOURCES, counts=imagemgr_core.counts(), sizes=imagemgr_core.sizes(),
                               rows=rows[:per], page=page, more=len(rows) > per, mom_quota=get_config("moment_image_quota", "20"), mom_mb=get_config("moment_total_mb", "300"), sms_on=msgimages_core.enabled(), mail_on=msgimages_core.mail_enabled(), max_mb=msgimages_core.max_bytes() // 1024 // 1024)

    @app.route("/images/<src>/<int:img_id>/raw")
    @login_required
    def images_raw(src, img_id):
        got = imagemgr_core.raw(src, img_id) if imagemgr_core.valid(src) else None
        if not got:
            abort(404)
        resp = make_response(got[0])
        resp.headers["Content-Type"] = got[1]
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.post("/images/<src>/<int:img_id>/delete")
    @login_required
    def images_delete(src, img_id):
        if imagemgr_core.valid(src):
            imagemgr_core.delete(src, img_id)
            flash("图片已删除")
        return redirect(url_for("images_page", src=src, page=request.form.get("page", 1, type=int)))

    @app.post("/images/moments-settings")
    @login_required
    def images_moments_settings():
        for k, lo in (("moment_image_quota", 0), ("moment_total_mb", 1)):
            v = request.form.get(k, "").strip()
            if v.isdigit() and int(v) >= lo:
                set_config(k, v)
        flash("朋友圈图片额度已保存")
        return redirect(url_for("images_page", src="moments"))

    @app.post("/images/purge")
    @login_required
    def images_purge():
        src = request.form.get("src", "sms")
        days = request.form.get("days", type=int)
        if not imagemgr_core.valid(src):
            abort(404)
        if request.form.get("all") == "1":
            n = imagemgr_core.delete_all(src)
        elif src == "sms" and days and days > 0:
            n = msgimages_core.delete_older_than(days)
        elif src == "lovemail" and days and days > 0:
            n = msgimages_core.mail_delete_older_than(days)          # 还没派送的信的图不删
        else:
            flash("要写清楚清理几天前的，或者选「全部删除」", "error")
            return redirect(url_for("images_page", src=src))
        flash(f"已删除 {n} 张图片")
        return redirect(url_for("images_page", src=src))
