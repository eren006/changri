"""设置平台 · QQ 号监听。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import loopbridge, qqmanager as qqmanager_core, qqpool as qqpool_core, roles as roles_core
from changri.core.db import current_bot, get_config, get_int, set_config


def register(app, login_required):
    @app.route("/qqlink")
    @login_required
    def qqlink_page():
        from changri.core.db import current_bot
        qqpool_core.ensure_links()
        st = qqmanager_core.status(current_bot())
        link_rows, live, errs = [], [], []
        for ln in qqpool_core.links():
            ls = qqmanager_core.link_status(current_bot(), ln["id"])
            link_rows.append({**ln, "st": ls, "used": qqpool_core.link_usage(ln["id"]), "groups": len(qqpool_core.link_groups(ln["id"]))})
            if ls["connected"]:
                try:
                    for g in loopbridge.run(qqmanager_core.group_list(current_bot(), ln["id"]), timeout=15):
                        live.append({**g, "link_id": ln["id"], "link_name": ln["name"]})
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{ln['name']}：{e or '取群列表失败'}")
        live_err = "；".join(errs)
        in_sets = {}
        sets = qqpool_core.sets(current_bot())
        for x in sets:
            for g in x["groups"]:
                in_sets[g["real_gid"]] = x["name"]
        names = {str(g.get("group_id")): g.get("group_name", "") for g in live}
        multi = len(link_rows) > 1
        tpl = get_config("qqlink_room_name", "auto")
        if tpl == "{kind}·{names}":
            tpl = "auto"
        sample = {"kind": "约会", "id": 12, "day": 3, "start_hm": "20:00", "end_hm": "21:00", "place": "海边咖啡馆"}
        presets = list(qqpool_core.ROOM_NAME_PRESETS)
        from changri.core import outage
        view = request.args.get("view") or ("settings" if link_rows else "guide")     # 还没连过小号：先看教程（连上以后表单提交回来留在设置）
        return render_template("qqlink.html", view=view, fallback_missing=outage.ready(), fallback_active=outage.active(),
                               fb_on=outage.enabled(), backfill_on=get_config("qq_backfill_enabled", "1") == "1",
                               alert_min=max(1, get_int("qqlink_alert_secs", 120) // 60), conf=qqpool_core.conf(), pool=[g for g in qqpool_core.list_groups() if g["kind"] != "room"], kinds=qqpool_core.KINDS,
                               st=st, link_rows=link_rows, multi=multi, has_token=bool(get_config("qqlink_token", "")), skip="\n".join(sorted(qqpool_core.skip_users())),
                               tpl=tpl, presets=presets, is_preset=tpl in {t for t, _ in qqpool_core.ROOM_NAME_PRESETS},
                               preview=qqpool_core.room_name(sample, ["小明", "小红"], tpl), free_name=get_config("qqlink_free_name", qqpool_core.FREE_NAME_DEFAULT),
                               sets=sets, live=live, live_err=live_err, in_sets=in_sets, names=names, held=qqpool_core.held_by_other_bots(),
                               auth="\n".join(sorted(qqpool_core.auth_qqs())),
                               binds=roles_core.qq_bindings(), role_names=[r["name"] for r in roles_core.list_all()], trpg_binds=qqpool_core.trpg_bindings())

    @app.post("/qqlink/sets/add")
    @login_required
    def qqlink_sets_add():
        name = (request.form.get("new_set", "").strip() or request.form.get("pick_set", "").strip())
        gids = request.form.getlist("gid")                       # 每项是「QQ号连接编号:群号」
        if not gids:
            flash("先勾选要加的群")
            return redirect(url_for("qqlink_page"))
        bad = []
        for item in gids:
            lid, _, gid = item.partition(":")
            err = qqpool_core.join_set(current_bot(), gid, name, int(lid) if lid.isdigit() else 0)
            if err:
                bad.append(f"{gid}：{err}")
        flash(f"已把 {len(gids) - len(bad)} 个群加进群号组「{name}」" + (("；没加成：" + "；".join(bad)) if bad else ""))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/sets/rename")
    @login_required
    def qqlink_sets_rename():
        old, new = request.form.get("old", ""), request.form.get("new", "")
        err = qqpool_core.rename_set(current_bot(), old, new)
        flash(err or f"群号组已改名为「{new.strip()}」")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/sets/toggle")
    @login_required
    def qqlink_sets_toggle():
        name, on = request.form.get("name", ""), request.form.get("on") == "1"
        err = qqpool_core.set_enabled(current_bot(), name, on)
        flash(err or (f"群号组「{name}」已开启使用" if on else f"群号组「{name}」已关闭，不再分配房间"))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/sets/remove/<gid>")
    @login_required
    def qqlink_sets_remove(gid):
        err = qqpool_core.leave_set(current_bot(), gid)
        flash(err or "已移出群号组")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/auth")
    @login_required
    def qqlink_auth():
        bad = qqpool_core.save_auth(request.form.get("auth", ""))
        flash("已保存" + ("；没保存：" + "；".join(bad) if bad else ""))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/conf")
    @login_required
    def qqlink_conf():
        from changri.core.db import current_bot
        err = qqpool_core.save_conf(request.form.get("url", ""), request.form.get("token") or None, bool(request.form.get("on")))
        if err:
            flash(err)
        else:
            loopbridge.run(qqmanager_core.restart(current_bot()))
            flash("设置已保存，连接已按新设置重启")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/outage")
    @login_required
    def qqlink_outage():
        """QQ 号掉线怎么办：掉线多久算掉线、要不要自动切到小手机、要不要开合并转发补录。"""
        f = request.form
        set_config("qqdown_phone_fallback", "1" if f.get("fallback") else "0")
        set_config("qq_backfill_enabled", "1" if f.get("backfill") else "0")
        mins = f.get("alert_min", "").strip()
        if mins.isdigit() and 1 <= int(mins) <= 120:
            set_config("qqlink_alert_secs", str(int(mins) * 60))
        flash("已保存")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/link/add")
    @login_required
    def qqlink_link_add():
        from changri.core.db import current_bot
        lid, err = qqpool_core.add_link(request.form.get("name", ""), request.form.get("url", ""), request.form.get("token", ""), bool(request.form.get("on")))
        if err:
            flash(err)
        else:
            loopbridge.run(qqmanager_core.restart(current_bot()))
            flash("已添加 QQ 号连接")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/link/<int:lid>/save")
    @login_required
    def qqlink_link_save(lid):
        from changri.core.db import current_bot
        err = qqpool_core.update_link(lid, request.form.get("name", ""), request.form.get("url", ""), request.form.get("token") or None, bool(request.form.get("on")))
        if err:
            flash(err)
        else:
            loopbridge.run(qqmanager_core.restart(current_bot()))
            flash("已保存，连接已按新设置重启")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/link/<int:lid>/delete")
    @login_required
    def qqlink_link_delete(lid):
        from changri.core.db import current_bot
        err = qqpool_core.delete_link(current_bot(), lid)
        if not err:
            qqmanager_core.stop_link(current_bot(), lid)
        flash(err or "已删除这个 QQ 号连接，它名下的群也一起移出了群号池")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/skip")
    @login_required
    def qqlink_skip():
        qqpool_core.save_skip(request.form.get("skip", ""))
        flash("已保存")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/bind")
    @login_required
    def qqlink_bind():
        name, qq = request.form.get("role", "").strip(), request.form.get("qq", "").strip()
        role = roles_core.get_role_by_name(name)
        err = "没有叫这个名字的角色" if not role else roles_core.bind_qq(role["id"], qq, force=True)
        flash(err or f"已把 QQ {qq} 认到「{name}」名下")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/unbind/<qq>")
    @login_required
    def qqlink_unbind(qq):
        done = roles_core.unbind_qq(qq) | qqpool_core.unbind_trpg(qq)
        flash("已解除绑定" if done else "没有这个绑定")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/rooms")
    @login_required
    def qqlink_rooms_add():
        ok, bad = qqpool_core.add_rooms(current_bot(), request.form.get("gids", ""), request.form.get("set_name", "").strip())
        flash(f"已加 {ok} 个房间群" + (("；没加成：" + "；".join(bad)) if bad else ""))
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/roomname")
    @login_required
    def qqlink_roomname():
        pick = request.form.get("preset", "")
        tpl = request.form.get("custom", "").strip() if pick == "custom" else pick
        set_config("qqlink_room_name", tpl or "auto")
        set_config("qqlink_free_name", request.form.get("free_name", "").strip() or qqpool_core.FREE_NAME_DEFAULT)
        flash("已保存")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group")
    @login_required
    def qqlink_group_add():
        err = qqpool_core.add_group(request.form.get("gid", ""), request.form.get("note", ""), "bot")
        flash(err or "已加进来。在群里 @机器人 说一句话就会自动关联")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group/<gid>/edit")
    @login_required
    def qqlink_group_edit(gid):
        ok = qqpool_core.update_group(gid, request.form.get("note", ""), "bot", request.form.get("group_openid", ""))
        flash("已保存" if ok else "没有这个群")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group/<gid>/delete")
    @login_required
    def qqlink_group_delete(gid):
        flash("已从群号池移除" if qqpool_core.remove_group(gid) else "没有这个群")
        return redirect(url_for("qqlink_page"))

    @app.post("/qqlink/group/<gid>/rename")
    @login_required
    def qqlink_group_rename(gid):
        from changri.core.db import current_bot
        err = loopbridge.run(qqmanager_core.rename(current_bot(), gid, request.form.get("name", "")))
        flash(err or "群名已改")
        return redirect(url_for("qqlink_page"))

    # ---------- 一个群在「小号的群号组」和「官方机器人的备用群」之间来回转 ----------

    def _back():
        return redirect(url_for("backup_pool_page") if request.form.get("back") == "pool" else url_for("qqlink_page"))

    @app.post("/qqlink/handover/<gid>/to-official")
    @login_required
    def qqlink_to_official(gid):
        """转到官方备用群：这个群以前在群里发过「设为备用群」，官方机器人认得它，不用再去群里发一次。"""
        from changri.core import handover
        bot = current_bot()
        o = handover.official_row(bot, gid)
        if not o:
            flash("官方机器人还不认得这个群：先在群里发一次「设为备用群 本群群号」")
            return _back()
        why = handover.check_to_official(bot, o["group_openid"], gid)
        if why:
            flash(why)
            return _back()
        a = handover.alt_row(bot, gid)
        name = qqpool_core.free_name((a["set_name"] if a else "") or o["set_name"])
        err = loopbridge.run(qqmanager_core.rename(bot, gid, name)) if a and not a["moved"] else "小号不管这个群"
        ok, why = handover.to_official(bot, o["group_openid"], gid, o["set_name"])
        if not ok:
            flash(why)
            return _back()
        flash(f"群 {gid} 已转给官方机器人。" + (f"群名已由小号改成「{name}」。" if not err else
              f"小号没能改群名（{err}）：{handover.RENAME_ASK.format(name=name)}"))
        return _back()

    @app.post("/qqlink/handover/<gid>/to-qq")
    @login_required
    def qqlink_to_qq(gid):
        """转回小号群：先查小号连着、而且真的在这个群里；官方机器人在群里提醒群主确认小号是管理员。"""
        from changri.core import handover
        from changri.core.qqsend import make_send
        bot = current_bot()
        why = handover.check_to_qq(bot, gid)
        if why:
            flash(why)
            return _back()
        seen, errs = False, []
        for ln in qqpool_core.links():
            if not qqmanager_core.link_status(bot, ln["id"])["connected"]:
                continue
            try:
                groups = loopbridge.run(qqmanager_core.group_list(bot, ln["id"], max_age=0), timeout=15)
                seen = seen or any(str(g.get("group_id")) == gid for g in groups)
            except Exception as e:  # noqa: BLE001
                errs.append(str(e) or "取群列表失败")
        if not seen:
            flash(f"没转：小号现在不在群 {gid} 里" + (f"（{'；'.join(errs)}）" if errs else "（或者小号没连上）") + "。先让小号进群、设为管理员，再点一次。")
            return _back()
        o = handover.official_row(bot, gid)
        note = ""
        if o and not o["moved"]:
            try:
                loopbridge.run(make_send()(bot, o["group_openid"], handover.BACK_NOTICE), timeout=15)
            except Exception as e:  # noqa: BLE001  提醒发不出去（没开主动发言等）不影响转
                note = f"（官方机器人没能在群里发提醒：{e}）"
        ok, why = handover.to_qq(bot, gid)
        flash((f"群 {gid} 已转回小号的群号组。请确认小号在群里是管理员。" + note) if ok else why)
        return _back()
