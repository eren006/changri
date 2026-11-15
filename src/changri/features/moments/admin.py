"""设置平台 · 朋友圈。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import abort, flash, make_response, redirect, render_template, request, url_for
from changri.core import moments as moments_core


def register(app, login_required):
    @app.route("/moments")
    @login_required
    def moments_page():
        posts, more = moments_core.feed(0, before=request.args.get("before", type=int), limit=30)
        return render_template("moments.html", posts=posts, more=more, st=moments_core.stats())

    @app.get("/moments/img/<int:img_id>")
    @login_required
    def moments_image(img_id):
        data = moments_core.image(img_id, request.args.get("s") == "thumb")
        if not data:
            abort(404)
        resp = make_response(data)
        resp.headers["Content-Type"] = "image/jpeg"
        return resp

    @app.post("/moments/<int:mid>/delete")
    @login_required
    def moments_delete(mid):
        flash("已删除这条朋友圈（图片和评论一起没了）" if moments_core.delete_post(mid) else "这条已经不在了")
        return redirect(url_for("moments_page"))

    @app.post("/moments/comment/<int:cid>/delete")
    @login_required
    def moments_comment_delete(cid):
        flash("已删除这条评论" if moments_core.delete_comment(cid) else "这条评论已经不在了")
        return redirect(url_for("moments_page"))

    @app.post("/moments/image/<int:img_id>/delete")
    @login_required
    def moments_image_delete(img_id):
        flash("已删除这张图片，帖子的文字还在" if moments_core.delete_image(img_id) else "这张图片已经不在了")
        return redirect(url_for("moments_page"))
