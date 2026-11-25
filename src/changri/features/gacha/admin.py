"""设置平台 · 抽取池。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import gacha as gacha_core, rpg as rpg_core
from changri.core.db import get_conn


def register(app, login_required):
    @app.route("/gacha")
    @login_required
    def gacha_page():
        with get_conn() as c:
            people = [dict(r) for r in c.execute("SELECT id, name FROM roles WHERE is_npc=0 ORDER BY id")]
        return render_template("gacha.html", pools=gacha_core.pools(), items=rpg_core.items(), people=people, log=gacha_core.recent_log())

    @app.post("/gacha/pool/save")
    @login_required
    def gacha_pool_save():
        f = request.form
        ok, msg = gacha_core.save_pool(f.get("name", ""), f.get("note", ""), f.get("pity_n", 0), f.get("enabled", "1") == "1", f.get("pool_id", type=int))
        flash(msg, "message" if ok else "error")
        return redirect(url_for("gacha_page"))

    @app.post("/gacha/pool/<int:pool_id>/delete")
    @login_required
    def gacha_pool_delete(pool_id):
        gacha_core.delete_pool(pool_id)
        flash("池子已删除")
        return redirect(url_for("gacha_page"))

    @app.post("/gacha/prize/add")
    @login_required
    def gacha_prize_add():
        f = request.form
        ok, msg = gacha_core.add_prize(f.get("pool_id", type=int) or 0, f.get("item", ""), f.get("n", 1), f.get("weight", 1), f.get("rare") == "1")
        flash(msg, "message" if ok else "error")
        return redirect(url_for("gacha_page"))

    @app.post("/gacha/prize/<int:prize_id>/delete")
    @login_required
    def gacha_prize_delete(prize_id):
        gacha_core.remove_prize(prize_id)
        return redirect(url_for("gacha_page"))

    @app.post("/gacha/grant")
    @login_required
    def gacha_grant():
        f = request.form
        who, pool = f.get("role", ""), str(f.get("pool_id", ""))
        with get_conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM roles WHERE is_npc=0")] if who == "all" else [int(who)] if who.isdigit() else []
        done, err = 0, ""
        for rid in ids:
            ok, msg = gacha_core.grant(rid, pool, f.get("n", 0))
            done += ok
            err = err if ok else msg
        flash(f"已发放给 {done} 人" + (f"（有人没成功：{err}）" if err else ""))
        return redirect(url_for("gacha_page"))
