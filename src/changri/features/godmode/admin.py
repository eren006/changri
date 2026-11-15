"""设置平台 · 上帝模式。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import godmode as god_core, relations as relations_core, rpg as rpg_core
from changri.core.db import get_conn


def register(app, login_required):
    def _god_back(role_id):
        tab = request.form.get("tab", "")
        return redirect(url_for("god_page", role=role_id, tab=tab) if tab else url_for("god_page", role=role_id))

    @app.route("/god")
    @login_required
    def god_page():
        with get_conn() as c:
            people = [dict(r) for r in c.execute("SELECT id, name FROM roles ORDER BY name")]
        rid = request.args.get("role", type=int)
        role = next((p for p in people if p["id"] == rid), None)
        if request.args.get("role") == "all":                 # 全体：对所有人一起生效的开关和时间锁定
            return render_template("god.html", people=people, role=None, everyone=True, tab="", god=god_core,
                                   g_off={k for k in god_core.FEATURES if god_core.global_off(k)}, g_locks=god_core.global_locks())
        tab = request.args.get("tab", "perm")
        tab = tab if tab in ("perm", "bag", "time", "count", "rel") else "perm"
        ctx = {}
        if role:
            counts = god_core.today_counts(rid)
            with get_conn() as c:
                perms = {r["feature"] for r in c.execute("SELECT feature FROM role_perms WHERE role_id=?", (rid,))}
            with get_conn() as c:
                my_attrs = [dict(r) for r in c.execute("SELECT name, value FROM role_attrs WHERE role_id=? ORDER BY name", (rid,))]
            bag = rpg_core.inventory(rid)
            ctx = dict(my_attrs=my_attrs, bag=bag, all_items=rpg_core.items(), attr_defs=rpg_core.attr_defs(), perms=perms, counts=counts, locks=[l for l in god_core.locks() if l["role_id"] == rid],
                       timeline=god_core.timeline_all(rid), rels=relations_core.list_for(rid), others=[p for p in people if p["id"] != rid])
        else:                                            # 没选人：给每个人标上「有没有被干预过」
            closed, locked = {}, {}
            for c_ in god_core.closed_list():
                closed[c_["role_id"]] = closed.get(c_["role_id"], 0) + 1
            for l in god_core.locks():
                locked[l["role_id"]] = locked.get(l["role_id"], 0) + 1
            for p in people:
                p["closed"], p["locked"] = closed.get(p["id"], 0), locked.get(p["id"], 0)
        return render_template("god.html", people=people, role=role, everyone=False, tab=tab, god=god_core,
                               any_global=bool(god_core.global_locks() or any(god_core.global_off(k) for k in god_core.FEATURES)), **ctx)

    @app.post("/god/all/perm")
    @login_required
    def god_all_perm():
        on = set(request.form.getlist("on"))
        for k in god_core.FEATURES:
            god_core.set_global(k, k in on)
        flash("全体功能开关已保存")
        return redirect(url_for("god_page", role="all"))

    @app.post("/god/all/lock")
    @login_required
    def god_all_lock():
        f = request.form
        ok, info = god_core.add_global_lock(f.get("day", type=int), f.get("time", ""), f.get("note", ""))
        flash("已对所有人锁定这段时间" if ok else info)
        return redirect(url_for("god_page", role="all"))

    @app.post("/god/all/unlock/<int:lock_id>")
    @login_required
    def god_all_unlock(lock_id):
        god_core.remove_global_lock(lock_id)
        flash("已解锁")
        return redirect(url_for("god_page", role="all"))

    @app.post("/god/<int:role_id>/perm")
    @login_required
    def god_perm(role_id):
        feats = request.form.getlist("on")
        for k in god_core.FEATURES:
            god_core.set_perm(role_id, k, k in feats)
        flash("功能权限已保存")
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/lock")
    @login_required
    def god_lock(role_id):
        f = request.form
        ok, info = god_core.add_lock(role_id, f.get("day", type=int), f.get("time", ""), f.get("note", ""))
        flash("已锁定这段时间" if ok else info)
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/unlock/<int:lock_id>")
    @login_required
    def god_unlock(role_id, lock_id):
        god_core.remove_lock(lock_id)
        flash("已解锁")
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/slot/<int:room_id>/delete")
    @login_required
    def god_slot_delete(role_id, room_id):
        ok, msg = god_core.delete_slot(role_id, room_id)
        flash(msg)
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/reset")
    @login_required
    def god_reset(role_id):
        n = god_core.reset_today(role_id, request.form.getlist("kind") or None)
        flash(f"今天的次数已清零（{n} 项）" if n else "今天还没有用过次数")
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/item")
    @login_required
    def god_item(role_id):
        f = request.form
        n = f.get("n", type=int) or 0
        if n <= 0:
            flash("数量要写正整数")
        else:
            delta = n if f.get("act") == "give" else -n
            ok, info = rpg_core.adjust(role_id, f.get("item", ""), delta, "上帝模式")
            flash(("已发放" if delta > 0 else "已扣除") + f" {n} 件" if ok else info)
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/attr")
    @login_required
    def god_attr(role_id):
        from changri.core import attrs as attrs_core
        f = request.form
        name = attrs_core.norm(f.get("name", ""))
        val = f.get("value", type=int)
        if not name or val is None:
            flash("要选属性、写一个整数")
        else:
            cur = attrs_core.get_attr(role_id, name) or 0
            new = val if f.get("act") == "set" else cur + val
            ok, info = attrs_core.set_attrs(role_id, [(name, new)])
            flash(f"「{name}」现在是 {attrs_core.get_attr(role_id, name)}" if ok else info)
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/rel/<int:other_id>/delete")
    @login_required
    def god_rel_delete(role_id, other_id):
        rel = relations_core.get_rel(role_id, other_id)
        if rel:
            relations_core.delete_rel(rel["id"])
            flash("关系线已删除")
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/rel/clear")
    @login_required
    def god_rel_clear(role_id):
        flash(f"已清空 {god_core.clear_rels(role_id)} 条关系线")
        return _god_back(role_id)
