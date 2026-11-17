"""设置平台 · 群池。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import pool as pool_core


def register(app, login_required):
    @app.route("/pool")
    @login_required
    def pool_page():
        groups = pool_core.all_groups()
        sets: dict[str, list] = {}
        blocked = [g for g in groups if g["conflict"]]                 # 个人群/戏群/公告群不能当房间，单独收起来，别和能用的混在一起
        for g in groups:
            if not g["conflict"]:
                sets.setdefault(g["set_name"] or "（未分组）", []).append(g)
        return render_template("pool.html", sets=sets, blocked=blocked, counts=pool_core.counts(),
                               unfilled=sum(1 for g in groups if not g["real_gid"] and not g["conflict"]))

    @app.post("/pool/<int:pid>/save")
    @login_required
    def pool_save(pid):
        pool_core.update(pid, request.form.get("real_gid", ""), request.form.get("label", ""), request.form.get("set_name", ""))
        flash("已保存")
        return redirect(url_for("pool_page"))

    @app.post("/pool/<int:pid>/toggle")
    @login_required
    def pool_toggle(pid):
        ok, why = pool_core.set_in_pool(pid, request.form.get("on") == "1")
        flash("已更新" if ok else why)
        return redirect(url_for("pool_page"))

    @app.post("/pool/<int:pid>/clean")
    @login_required
    def pool_clean(pid):
        g = pool_core.get(pid)
        if g and g["state"] == "cleaning":
            pool_core.release(pid, "free")
            flash("已放回备用群池。")
        return redirect(url_for("pool_page"))

    @app.post("/pool/<int:pid>/delete")
    @login_required
    def pool_delete(pid):
        ok, why = pool_core.delete(pid)
        flash("已删除" if ok else why)
        return redirect(url_for("pool_page"))

    @app.post("/pool/set")
    @login_required
    def pool_set():
        name, on = request.form.get("set_name", ""), request.form.get("on") == "1"
        ok, skip = pool_core.set_set(name, on)
        flash(f"组「{name}」已{'开启' if on else '关闭'}：{ok} 个群{('，跳过 %d 个（占用中或已是别的用途）' % skip) if skip else ''}")
        return redirect(url_for("pool_page"))

    @app.post("/pool/bulk")
    @login_required
    def pool_bulk():
        """把一串群号按顺序填给「还没填群号」的群（按先被机器人看到的顺序）。"""
        nums = [x.strip() for x in request.form.get("nums", "").replace("，", "\n").replace(",", "\n").split() if x.strip()]
        set_name = request.form.get("set_name", "").strip()
        empty = [g for g in pool_core.all_groups() if not g["real_gid"] and not g["conflict"]]
        for g, n in zip(empty, nums):
            pool_core.update(g["id"], n, g["label"], set_name or g["set_name"])
        flash(f"已填 {min(len(empty), len(nums))} 个群号" + (f"；多出 {len(nums) - len(empty)} 个号没有对应的群（机器人还没进那些群）" if len(nums) > len(empty) else ""))
        return redirect(url_for("pool_page"))
