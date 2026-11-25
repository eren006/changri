"""设置平台 · 角色（原「上帝模式」）。所有角色在一个地方：列表 + 每人一页
（概况：档案、个人群、激活码、QQ 号；功能、背包、时间、次数、关系线）。
由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名 god_* 不变，老链接照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import charprofile, extensions, godmode as god_core, relations as relations_core, roles as roles_core, rpg as rpg_core
from changri.core.db import get_conn

TABS = ("info", "perm", "bag", "time", "count", "rel")


def _phone_code(role_id: int) -> str:
    """只读：有激活码就返回，没有返回空（phonecodes.code_for_role 读不到会顺手生成，这里不要）。"""
    with get_conn() as c:
        r = c.execute("SELECT code FROM phone_codes WHERE role_id=?", (role_id,)).fetchone()
    return r["code"] if r else ""


def _people() -> list[dict]:
    """列表每人一行：名字、简称、性别年龄、NPC、个人群状态、有没有被干预。"""
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT r.id, r.name, r.is_npc, p.short, p.gender, p.age FROM roles r LEFT JOIN role_profiles p ON p.role_id=r.id ORDER BY r.name")]
    closed, locked = {}, {}
    for c_ in god_core.closed_list():
        closed[c_["role_id"]] = closed.get(c_["role_id"], 0) + 1
    for l in god_core.locks():
        locked[l["role_id"]] = locked.get(l["role_id"], 0) + 1
    for p in rows:
        gs = roles_core.list_groups(p["id"])
        p["gid"] = next((g["real_gid"] for g in gs if g["real_gid"]), "")
        p["bound"] = any(g["group_openid"] for g in gs)
        p["gender"] = p["gender"] or "女"                                 # 没改过档案 = 默认值（同 charprofile.get）
        p["age"] = p["age"] if p["age"] is not None else 18
        p["closed"], p["locked"] = closed.get(p["id"], 0), locked.get(p["id"], 0)
    return rows


def register(app, login_required):
    def _god_back(role_id):
        tab = request.form.get("tab", "")
        return redirect(url_for("god_page", role=role_id, tab=tab) if tab else url_for("god_page", role=role_id))

    @app.route("/god")
    @login_required
    def god_page():
        people = _people()
        rid = request.args.get("role", type=int)
        role = next((p for p in people if p["id"] == rid), None)
        from changri.core import rooms as rooms_core
        blocked = rooms_core.blocked_hours()
        any_global = bool(god_core.global_locks() or blocked or any(god_core.global_off(k) for k in god_core.FEATURES))
        if request.args.get("role") == "all":                 # 全体：对所有人一起生效的开关和时间锁定
            return render_template("god.html", people=people, role=None, everyone=True, tab="", god=god_core,
                                   g_off={k for k in god_core.FEATURES if god_core.global_off(k)}, g_locks=god_core.global_locks(),
                                   blocked=sorted(blocked.items()), edit_day=request.args.get("day", type=int))
        tab = request.args.get("tab", "info")
        tab = tab if tab in TABS else "info"
        ctx = {}
        if role:
            counts = god_core.today_counts(rid)
            with get_conn() as c:
                perms = {r["feature"] for r in c.execute("SELECT feature FROM role_perms WHERE role_id=?", (rid,))}
                my_attrs = [dict(r) for r in c.execute("SELECT name, value FROM role_attrs WHERE role_id=? ORDER BY name", (rid,))]
            ctx = dict(my_attrs=my_attrs, bag=rpg_core.inventory(rid), all_items=rpg_core.items(), attr_defs=rpg_core.attr_defs(), perms=perms, counts=counts,
                       locks=[l for l in god_core.locks() if l["role_id"] == rid], timeline=god_core.timeline_all(rid), rels=relations_core.list_for(rid),
                       others=[p for p in people if p["id"] != rid],
                       profile=charprofile.get(rid), my_groups=roles_core.list_groups(rid), qqs=roles_core.qqs_of_role(rid),
                       phone_on=extensions.is_enabled("phone"), phone_code=_phone_code(rid),
                       bind_code=request.args.get("code", ""), pending=roles_core.pending_code(rid))
        return render_template("god.html", people=people, role=role, everyone=False, tab=tab, god=god_core, any_global=any_global, **ctx)

    @app.post("/god/<int:role_id>/bindqq")
    @login_required
    def god_bindqq(role_id):
        """替玩家补绑 QQ（老季度建的角色没绑过的）。"""
        err = roles_core.bind_qq(role_id, request.form.get("qq", ""))
        flash(err or "QQ 号已绑好", "error" if err else "message")
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/profile")
    @login_required
    def god_profile(role_id):
        """管理员替玩家改档案：和群里一样一行一条（修改性别 男 / 修改年龄 20 …），不受冷却限制。"""
        role = roles_core.get_role(role_id)
        text = request.form.get("lines", "").strip()
        if role and text:
            lines = [x if x.startswith("修改") else "修改" + x for x in (y.strip() for y in text.splitlines()) if x]
            flash(charprofile.edit(role, "\n".join(lines), admin=True).replace("\n", "；"))
        return _god_back(role_id)

    @app.post("/god/all/perm")
    @login_required
    def god_all_perm():
        on = set(request.form.getlist("on"))
        for k in god_core.shown_features():          # 只动页面上列出的开关，藏起来的保持原样
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

    @app.post("/god/all/hours")
    @login_required
    def god_all_hours():
        """禁约时段：某一天勾了哪几个钟点不能约（全不勾 = 这一天不限了）。"""
        from changri.core import rooms as rooms_core
        day = request.form.get("day", type=int)
        if day is None or day < 0:
            flash("要写是第几天（0 或更大的数字）。", "error")
            return redirect(url_for("god_page", role="all"))
        data = rooms_core.blocked_hours()
        hours = sorted({int(h) for h in request.form.getlist("h") if h.isdigit() and 0 <= int(h) <= 23})
        data[day] = hours
        rooms_core.save_blocked_hours(data)
        flash(f"D{day} 的禁约时段已保存" if hours else f"D{day} 不再有禁约时段")
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
        for k in god_core.shown_features():          # 只动页面上列出的开关，藏起来的保持原样
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
        from changri.core import loopbridge
        from changri.plugins import play
        try:
            ok, msg = loopbridge.run(play.remove_room(role_id, room_id))         # 进行中的还要通知参与者、请群里的人退群
        except Exception as e:  # noqa: BLE001
            msg = loopbridge.friendly_error(e, "删除失败")
        flash(msg)
        return _god_back(role_id)

    @app.post("/god/<int:role_id>/erase")
    @login_required
    def god_erase(role_id):
        from changri.core import roles as roles_core
        from changri.features.roles.admin import deleted_text
        role = roles_core.get_role(role_id)
        if not role:
            flash("这个角色已经不在了。")
            return redirect(url_for("god_page"))
        if request.form.get("confirm", "").strip() != role["name"]:
            flash("角色名没对上，没有删除。", "error")
            return _god_back(role_id)
        flash(deleted_text(roles_core.delete_role(role_id)))
        return redirect(url_for("god_page"))

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
            flash("数量要写正整数", "error")
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
            flash("要选属性、写一个整数", "error")
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
