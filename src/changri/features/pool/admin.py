"""设置平台 · 备用群池（地址 /backup-pool，旧地址 /pool 跳转过来）。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import pool as pool_core


def register(app, login_required):
    @app.route("/pool")
    @login_required
    def pool_old_url():
        """旧地址（改名前的书签）"""
        return redirect(url_for("backup_pool_page"))

    @app.route("/backup-pool")
    @login_required
    def backup_pool_page():
        groups = [g for g in pool_core.all_groups() if not pool_core.is_qq_only(g)]      # 小号群号组的群在「QQ 小号与群号组（双骰）」页管
        alt = {g["real_gid"]: g for g in pool_core.all_groups() if pool_core.is_qq_only(g)}
        for g in groups:                                   # 同一个群号也在小号的群号组里：可以来回转
            a = alt.get(g["real_gid"]) if g["real_gid"] else None
            g["handover"] = "" if not a else ("to_official" if g["moved"] else ("to_qq" if a["moved"] and g["state"] == "free" else ""))
        sets: dict[str, list] = {}
        blocked = [g for g in groups if g["conflict"]]                 # 个人群/戏群/公告群不能当房间，单独收起来，别和能用的混在一起
        for g in groups:
            if not g["conflict"]:
                sets.setdefault(g["set_name"] or "（未分组）", []).append(g)
        usable = [g for g in groups if not g["conflict"]]
        cleaning = [g for g in usable if g["state"] == "cleaning" and not g["moved"]]
        from changri.core import qqpool
        from changri.core.db import get_config
        view = request.args.get("view") or ("settings" if groups else "guide")     # 一个群都还没认得：先看教程
        return render_template("pool.html", view=view, sets=sets, blocked=blocked, counts=pool_core.counts(), cleaning=cleaning, total=len(usable),
                               free_name=get_config("qqlink_free_name", qqpool.FREE_NAME_DEFAULT))

    @app.post("/backup-pool/free-name")
    @login_required
    def backup_pool_free_name():
        """空闲群名：官方机器人的备用群一直叫这个名字；小号的群房间结束后也改回这个名字（和「结合QQ小号模式」页是同一个设置）。"""
        from changri.core import qqpool
        from changri.core.db import set_config
        name = request.form.get("free_name", "").strip()[:30] or qqpool.FREE_NAME_DEFAULT
        set_config("qqlink_free_name", name)
        flash(f"空闲群名改成了「{name}」。已经在用的官方备用群，群名要群主自己去改。")
        return redirect(url_for("backup_pool_page"))

    @app.post("/backup-pool/<int:pid>/save")
    @login_required
    def backup_pool_save(pid):
        pool_core.update(pid, request.form.get("real_gid", ""), request.form.get("label", ""), request.form.get("set_name", ""))
        flash("已保存")
        return redirect(url_for("backup_pool_page"))

    @app.post("/backup-pool/<int:pid>/toggle")
    @login_required
    def backup_pool_toggle(pid):
        ok, why = pool_core.set_in_pool(pid, request.form.get("on") == "1")
        flash("已更新" if ok else why)
        return redirect(url_for("backup_pool_page"))

    @app.post("/backup-pool/<int:pid>/clean")
    @login_required
    def backup_pool_clean(pid):
        g = pool_core.get(pid)
        if g and g["state"] == "cleaning":
            pool_core.release(pid, "free")
            flash("已放回备用群池。")
        return redirect(url_for("backup_pool_page"))

    @app.post("/backup-pool/<int:pid>/delete")
    @login_required
    def backup_pool_delete(pid):
        ok, why = pool_core.delete(pid)
        flash("已删除" if ok else why)
        return redirect(url_for("backup_pool_page"))

    @app.post("/backup-pool/set")
    @login_required
    def backup_pool_set():
        name, on = request.form.get("set_name", ""), request.form.get("on") == "1"
        ok, skip = pool_core.set_set(name, on)
        flash(f"组「{name}」已{'可以使用' if on else '暂停使用'}：{ok} 个群{('，跳过 %d 个（占用中或已是别的用途）' % skip) if skip else ''}")
        return redirect(url_for("backup_pool_page"))
