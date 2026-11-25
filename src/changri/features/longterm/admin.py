"""设置平台 · 长期群：建长期群、设货币和兑换比例、属性、注册项；看注册信息、字数留底；改玩家的货币 / 字数 / 属性 / 背包。"""
import time

from flask import abort, flash, redirect, render_template, request, url_for

from changri.core import longterm as lt


def _group_or_404(gid: int) -> dict:
    g = lt.get_group(gid)
    if not g:
        abort(404)
    return g


def _member_or_404(mid: int) -> dict:
    m = lt.get_member(mid)
    if not m:
        abort(404)
    return m


TABS = [("home", "总览"), ("players", "玩家"), ("words", "字数"), ("items", "道具与商城"), ("settings", "设置")]


def _tab(gid: int, tab: str) -> str:
    return url_for("longterm_group", gid=gid, tab=tab)


def register(app, login_required):
    @app.route("/longterm")
    @login_required
    def longterm_page():
        return render_template("longterm.html", lt_groups=lt.list_groups())

    @app.post("/longterm/new")
    @login_required
    def longterm_new():
        gid, err = lt.create_group(request.form.get("name", ""))
        if not gid:
            flash(err, "error")
            return redirect(url_for("longterm_page"))
        flash("建好了。QQ 群里管理员发「/模式 长期群」，再发「/切换长期群 名字」接上。")
        return redirect(url_for("longterm_group", gid=gid))

    @app.route("/longterm/<int:gid>")
    @login_required
    def longterm_group(gid):
        """一个长期群分五页：总览（等批准 + 动态）、玩家、字数、道具、设置。每页只查自己要的。"""
        g = _group_or_404(gid)
        tab = request.args.get("tab", "home")
        tab = tab if tab in dict(TABS) else "home"
        ctx = {"g": g, "tab": tab, "tabs": TABS, "pending": lt.pending_submissions(gid)}
        if tab == "home":
            kind = request.args.get("kind", "")
            mid = request.args.get("mid", "")
            before = request.args.get("before", "")
            ev = lt.events(gid, kind if kind in lt.EVENT_KINDS else "", int(mid) if mid.isdigit() else None,
                           limit=51, before=int(before) if before.isdigit() else None)
            ctx.update(stats=lt.stats(gid), events=ev[:50], more=ev[49]["id"] if len(ev) > 50 else None,
                       kind=kind, mid=mid, kinds=lt.EVENT_KINDS, all_members=lt.list_members(gid))
        elif tab == "players":
            q = request.args.get("q", "").strip()
            members = lt.list_members(gid)
            kpi = request.args.get("kpi", "")
            prog = lt.kpi_progress(gid) if lt.kpi_on(g) else {}
            if kpi in ("ok", "no") and prog:
                members = [m for m in members if prog[m["id"]]["ok"] == (kpi == "ok")]
            ctx.update(all_members=lt.list_members(gid), fields=lt.reg_fields(g), q=q, kpi=kpi, prog=prog,
                       kpi_label=lt.kpi_label(g),
                       members=[m for m in members if not q or q in m["name"] or q in m["aliases"]
                                or any(q in str(v) for v in m["info"].values())])
        elif tab == "words":
            ctx.update(sub_list=lt.submissions(gid, 100), unclaimed=lt.list_unclaimed(gid), all_members=lt.list_members(gid))
        elif tab == "items":
            rs = lt.recipes(gid)
            ctx.update(item_list=lt.item_defs(gid), edit_item=lt.item_def(gid, request.args.get("item", "")),
                       recipes=rs, edit_recipe=next((r for r in rs if r["name"] == request.args.get("recipe")), None))
        else:
            ctx.update(defs=lt.attr_defs(gid), checkins_today=lt.checkins_today(gid), kpi_periods=lt.KPI_PERIODS, kpi_now=lt.kpi_label(g), kpi_left=lt.kpi_left_days(g),
                       kpi_anchor_text=time.strftime("%Y-%m-%d", time.localtime(g["kpi_anchor"])) if g.get("kpi_anchor") else "")
        return render_template("longterm_group.html", **ctx)

    @app.post("/longterm/<int:gid>/settings")
    @login_required
    def longterm_settings(gid):
        _group_or_404(gid)
        f = request.form
        ok, err = lt.update_group(gid, name=f.get("name", ""), currency=f.get("currency", ""),
                                  chars_per_step=f.get("chars_per_step"), coins_per_step=f.get("coins_per_step"),
                                  reg_fields=f.get("reg_fields", ""), self_attrs=f.get("self_attrs") == "1",
                                  count_admin_only=f.get("count_admin_only") == "1", count_review=f.get("count_review") == "1",
                                  checkin_on=f.get("checkin_on") == "1", checkin_rewards=f.get("checkin_rewards", ""),
                                  kpi_period=f.get("kpi_period", "week"), kpi_days=f.get("kpi_days"), kpi_anchor=f.get("kpi_anchor", ""), kpi_chars=f.get("kpi_chars") or 0, kpi_paras=f.get("kpi_paras") or 0)
        flash("已保存。" if ok else err)
        return redirect(_tab(gid, "settings"))

    @app.post("/longterm/<int:gid>/delete")
    @login_required
    def longterm_delete(gid):
        g = _group_or_404(gid)
        if request.form.get("confirm", "").strip() != g["name"]:
            flash("没删：要在框里填长期群的名字确认。", "error")
            return redirect(_tab(gid, "settings"))
        lt.delete_group(gid)
        flash(f"已删除长期群「{g['name']}」和里面的所有数据。")
        return redirect(url_for("longterm_page"))

    @app.post("/longterm/<int:gid>/attr")
    @login_required
    def longterm_attr_save(gid):
        _group_or_404(gid)
        f = request.form
        ok, err = lt.set_attr_def(gid, f.get("name", ""), f.get("default") or 0, f.get("min"), f.get("max"))
        flash("属性已保存（已注册的玩家没有这一项的，补上默认值）。" if ok else err)
        return redirect(_tab(gid, "settings") + "#attrs")

    @app.post("/longterm/<int:gid>/attr/delete")
    @login_required
    def longterm_attr_delete(gid):
        _group_or_404(gid)
        lt.delete_attr_def(gid, request.form.get("name", ""))
        flash("已删除这项属性（所有玩家的这一项也一起删了）。")
        return redirect(_tab(gid, "settings") + "#attrs")

    @app.post("/longterm/<int:gid>/item")
    @login_required
    def longterm_item_save(gid):
        _group_or_404(gid)
        f = request.form
        ok, err = lt.save_item_def(gid, f.get("name", ""), f.get("description", ""), f.get("effects", ""),
                                   on_sale=f.get("on_sale") == "1", price=f.get("price") or 0, stock=f.get("stock", ""),
                                   old_name=f.get("old_name", ""), limit_total=f.get("limit_total") or 0, limit_daily=f.get("limit_daily") or 0)
        flash("道具已保存。" if ok else err)
        return redirect(_tab(gid, "items"))

    @app.post("/longterm/<int:gid>/item/delete")
    @login_required
    def longterm_item_delete(gid):
        _group_or_404(gid)
        lt.delete_item_def(gid, request.form.get("name", ""))
        flash("已删除这个道具的登记（玩家背包里已有的留着，变成不能使用的普通物品）。")
        return redirect(_tab(gid, "items"))

    @app.post("/longterm/sub/<int:sid>/review")
    @login_required
    def longterm_review(sid):
        s, _ = lt.submission(sid)
        if not s:
            abort(404)
        ok, msg = (lt.approve if request.form.get("act") == "approve" else lt.reject)(sid)
        flash(msg, "message" if ok else "error")
        back = request.form.get("back", "home")
        return redirect(url_for("longterm_submission", sid=sid) if back == "sub" else _tab(s["group_id"], back if back in dict(TABS) else "home"))

    @app.post("/longterm/<int:gid>/review_all")
    @login_required
    def longterm_review_all(gid):
        _group_or_404(gid)
        n = sum(1 for s in lt.pending_submissions(gid) if lt.approve(s["id"])[0])
        flash(f"批准了 {n} 次字数统计。")
        back = request.form.get("back", "home")
        return redirect(_tab(gid, back if back in dict(TABS) else "home"))

    @app.post("/longterm/<int:gid>/grant")
    @login_required
    def longterm_grant(gid):
        _group_or_404(gid)
        ops = lt.parse_adjust(request.form.get("text", ""))
        ids = [int(x) for x in request.form.getlist("member") if x.isdigit()]
        if request.form.get("all") == "1":
            ids = [m["id"] for m in lt.list_members(gid)]
        ids = [i for i in ids if (lt.get_member(i) or {}).get("group_id") == gid]
        if not ops or not ids:
            flash("要选人（或者勾「全员」），并写内容，比如：银两+50 苹果+1", "error")
        else:
            res = lt.bulk_adjust(ids, ops, note=request.form.get("note", "").strip()[:60] or "后台批量发放")
            bad = [f"{n}：{'；'.join(o)}" for n, o in res if not all("→" in x for x in o)]
            flash(f"发给了 {len(res)} 人。" + (" 有问题的：" + " / ".join(bad) if bad else ""))
        return redirect(_tab(gid, "players") + "#grant")

    @app.post("/longterm/<int:gid>/recipe")
    @login_required
    def longterm_recipe_save(gid):
        _group_or_404(gid)
        f = request.form
        ok, err = lt.save_recipe(gid, f.get("name", ""), f.get("inputs", ""), f.get("outputs", ""), f.get("old_name", ""))
        flash("配方已保存。" if ok else err)
        return redirect(_tab(gid, "items") + "#recipes")

    @app.post("/longterm/<int:gid>/recipe/delete")
    @login_required
    def longterm_recipe_delete(gid):
        _group_or_404(gid)
        lt.delete_recipe(gid, request.form.get("name", ""))
        flash("已删除配方。")
        return redirect(_tab(gid, "items") + "#recipes")

    @app.post("/longterm/<int:gid>/member/add")
    @login_required
    def longterm_member_add(gid):
        _group_or_404(gid)
        m, err = lt.add_member(gid, request.form.get("name", ""))
        if not m:
            flash(err, "error")
            return redirect(_tab(gid, "players"))
        flash("加好了。本人在群里用同一个名字「/注册」就会认上。")
        return redirect(url_for("longterm_member", mid=m["id"]))

    @app.post("/longterm/<int:gid>/claim")
    @login_required
    def longterm_claim(gid):
        _group_or_404(gid)
        name = request.form.get("name", "")
        mid = request.form.get("member_id", "")
        m = lt.get_member(int(mid)) if mid.isdigit() else None
        if not m or m["group_id"] != gid:
            flash("先选一位玩家。", "error")
        else:
            n = lt.claim(gid, name, m["id"])
            flash(f"「{name}」的 {n} 字转给了「{m['name']}」，以后这个名字自动算给 TA。")
        return redirect(_tab(gid, "words") + "#unclaimed")

    @app.post("/longterm/<int:gid>/unclaimed/drop")
    @login_required
    def longterm_unclaimed_drop(gid):
        _group_or_404(gid)
        lt.drop_unclaimed(gid, request.form.get("name", ""))
        flash("已丢掉这个名字的字数。")
        return redirect(_tab(gid, "words") + "#unclaimed")

    @app.route("/longterm/sub/<int:sid>")
    @login_required
    def longterm_submission(sid):
        s, lines = lt.submission(sid)
        if not s:
            abort(404)
        return render_template("longterm_sub.html", s=s, lines=lines, g=lt.get_group(s["group_id"]))

    @app.route("/longterm/member/<int:mid>")
    @login_required
    def longterm_member(mid):
        m = _member_or_404(mid)
        g = lt.get_group(m["group_id"])
        return render_template("longterm_member.html", m=m, g=g, fields=lt.reg_fields(g), attrs=lt.list_attrs(mid),
                               defs=lt.attr_defs(g["id"]), bag=lt.items(mid), events=lt.events(g["id"], mid=mid, limit=100),
                               ledger=lt.ledger(mid, 200), lines=lt.member_submission_lines(mid, 200), pending_chars=lt.pending_chars(mid),
                               kpi=lt.kpi_progress(g["id"]).get(mid) if lt.kpi_on(g) else None, kpi_label=lt.kpi_label(g), notes_text=lt.notes_to_text(m["notes"]))

    @app.post("/longterm/member/<int:mid>/edit")
    @login_required
    def longterm_member_edit(mid):
        m = _member_or_404(mid)
        g = lt.get_group(m["group_id"])
        info = {k: request.form.get(f"info_{k}", "").strip()[:200] for k in lt.reg_fields(g)}
        info = {**{k: v for k, v in m["info"].items() if k not in info}, **{k: v for k, v in info.items() if v}}
        ok, err = lt.update_member(mid, name=request.form.get("name", m["name"]), aliases=request.form.get("aliases", ""), info=info)
        flash("已保存。" if ok else err)
        return redirect(url_for("longterm_member", mid=mid))

    @app.post("/longterm/member/<int:mid>/notes")
    @login_required
    def longterm_member_notes(mid):
        _member_or_404(mid)
        notes, err = lt.parse_notes_text(request.form.get("notes", ""))
        if notes is None:
            flash(err, "error")
        else:
            lt.replace_notes(mid, notes)
            flash("条目已保存。")
        return redirect(url_for("longterm_member", mid=mid) + "#notes")

    @app.post("/longterm/member/<int:mid>/coins")
    @login_required
    def longterm_member_coins(mid):
        m = _member_or_404(mid)
        try:
            n = int(request.form.get("amount", ""))
        except ValueError:
            flash("数量要填整数。", "error")
            return redirect(url_for("longterm_member", mid=mid))
        how = request.form.get("how", "add")
        note = request.form.get("note", "").strip()[:60] or "后台调整"
        before = m["coins"]
        if how == "set":
            ok, err = lt.set_coins(mid, n, note)
        else:
            ok, err = lt.adjust_coins(mid, -n if how == "sub" else n, note)
        if ok:
            g = lt.get_group(m["group_id"])
            lt.event(g["id"], mid, "管理", f"{note}：{m['name']} 的{g['currency']} {before} → {lt.get_member(mid)['coins']}")
        flash("已改好。" if ok else err)
        return redirect(url_for("longterm_member", mid=mid))

    @app.post("/longterm/member/<int:mid>/adjust")
    @login_required
    def longterm_member_adjust(mid):
        _member_or_404(mid)
        ops = lt.parse_adjust(request.form.get("text", ""))
        if not ops:
            flash("格式：力量+5 苹果+2 字数+300 金币=100（可以一次写多个）", "error")
        else:
            note = request.form.get("note", "").strip()[:60] or "后台调整"
            flash("；".join(lt.apply_adjust(mid, ops, note=note, log=True)))
        return redirect(url_for("longterm_member", mid=mid))

    @app.post("/longterm/member/<int:mid>/attr/delete")
    @login_required
    def longterm_member_attr_delete(mid):
        _member_or_404(mid)
        flash("已删除" if lt.delete_attr(mid, request.form.get("name", "")) else "没有这一项")
        return redirect(url_for("longterm_member", mid=mid))

    @app.post("/longterm/member/<int:mid>/unbind")
    @login_required
    def longterm_member_unbind(mid):
        _member_or_404(mid)
        lt.unbind_member(mid)
        flash("已解开 QQ：数据都留着，本人再用这个名字「/注册」就认回来。")
        return redirect(url_for("longterm_member", mid=mid))

    @app.post("/longterm/member/<int:mid>/delete")
    @login_required
    def longterm_member_delete(mid):
        m = _member_or_404(mid)
        lt.delete_member(mid)
        flash(f"已删除玩家「{m['name']}」。")
        return redirect(_tab(m["group_id"], "players"))
