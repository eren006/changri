"""设置平台 · 收集。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
import time
from flask import abort, flash, redirect, render_template, request, url_for
from changri.core import collect as collect_core


def register(app, login_required):
    @app.route("/collect")
    @login_required
    def collect_page():
        return render_template("collect.html", rows=collect_core.listing())

    @app.post("/collect/add")
    @login_required
    def collect_add():
        f = request.form
        ok, msg = collect_core.create(f.get("title", ""), f.get("prompt", ""), collect_core.parse_local(f.get("start", "")) if f.get("timed") == "1" else 0,
                                      collect_core.parse_local(f.get("end", "")) if f.get("timed") == "1" else 0)
        if not ok:
            flash(msg, "error")
            return redirect(url_for("collect_page"))
        flash("已创建")
        return redirect(url_for("collect_view", cid=int(msg)))

    @app.route("/collect/<int:cid>")
    @login_required
    def collect_view(cid):
        col = collect_core.get(cid)
        if not col:
            abort(404)
        label, k = collect_core.status(col)
        k = request.args.get("round", k, type=int)
        k = min(max(k, 0), col["repeat_days"] - 1)
        rounds = [{"k": i, "start": collect_core.window(col, i)[0], "n": len(collect_core.items(cid, i))} for i in range(col["repeat_days"])
                  if collect_core.window(col, i)[0] <= time.time() or i == k]
        return render_template("collect_view.html", col=col, label=label, k=k, rounds=rounds, items=collect_core.items(cid, k), missing=collect_core.missing(cid, k),
                               win=collect_core.window(col, k), text=collect_core.export_text(col, k))

    @app.post("/collect/<int:cid>/close")
    @login_required
    def collect_close(cid):
        collect_core.set_closed(cid, request.form.get("reopen") != "1")
        flash("已重新开放" if request.form.get("reopen") == "1" else "已关闭")
        return redirect(url_for("collect_view", cid=cid))

    @app.post("/collect/<int:cid>/delete")
    @login_required
    def collect_delete(cid):
        collect_core.delete(cid)
        flash("已删除")
        return redirect(url_for("collect_page"))
