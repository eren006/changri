"""设置平台 · 论坛。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, url_for
from changri.core import forum as forum_core
from changri.core.db import get_conn


def register(app, login_required):
    @app.route("/forum")
    @login_required
    def forum_page():
        with get_conn() as conn:
            posts = conn.execute("SELECT p.*, r.name AS real_name, (SELECT COUNT(*) FROM replies WHERE post_id=p.id) AS n FROM posts p "
                                 "JOIN roles r ON r.id=p.role_id ORDER BY p.id DESC LIMIT 100").fetchall()
            replies = conn.execute("SELECT x.*, r.name AS real_name FROM replies x JOIN roles r ON r.id=x.role_id ORDER BY x.post_id DESC, x.floor").fetchall()
        by_post: dict[int, list] = {}
        for x in replies:
            by_post.setdefault(x["post_id"], []).append(x)
        return render_template("forum.html", posts=posts, by_post=by_post, pid=forum_core.pid)

    @app.post("/forum/<int:post_id>/delete")
    @login_required
    def forum_delete_post(post_id):
        forum_core.delete_post(post_id)
        flash("帖子已删除")
        return redirect(url_for("forum_page"))

    @app.post("/forum/<int:post_id>/<int:floor>/delete")
    @login_required
    def forum_delete_floor(post_id, floor):
        forum_core.delete_floor(post_id, floor)
        flash("楼层已删除")
        return redirect(url_for("forum_page"))
