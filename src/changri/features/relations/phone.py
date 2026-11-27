"""小手机 · 关系线。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import phonegate, relations, roles as roles_core, sheets
from changri.core.db import get_conn
from flask import abort, flash, redirect, request, url_for


def register(app, ctx):
    _ext_or_404, _fmt, _gate, _push, need_login, page = ctx._ext_or_404, ctx._fmt, ctx._gate, ctx._push, ctx.need_login, ctx.page

    def _rel_can(role) -> tuple[bool, str]:
        if not relations.enabled():
            return False, "关系线现在没有开放"
        if why := relations.closed_note():           # 过了截止时间：只能看
            return False, why
        return (False, phonegate.PAUSE_MSG) if phonegate.comm_paused() else (True, "")

    @app.route("/p/me/rel")
    def rel_page():
        _ext_or_404("relations")
        role, bad = need_login()
        if bad:
            return bad
        tab = "gongzhi" if request.args.get("tab") == "gongzhi" else "rel"
        can, why = _rel_can(role)
        lines = []
        unread = relations.unread(role["id"])
        for it in relations.list_for(role["id"]):
            cnt = relations.counts(it["rel_id"])
            un = unread.get(it["rel_id"], {})
            lines.append({"other": it["other_name"], "mine": it["mine"], "confirmed": it["confirmed"], "pending": bool(it["pending"]), "system": False, "unread_chat": un.get("chat", 0), "unread_detail": un.get("detail", 0),
                          **cnt, "last_chat_mine": cnt["last_chat_from"] == role["id"]})
        have = {l["other"] for l in lines}
        with get_conn() as c:
            names = [r["name"] for r in c.execute("SELECT name FROM roles WHERE id!=? ORDER BY name", (role["id"],))]
        gz_closed = not sheets.public()
        gongzhi = []
        if not gz_closed:
            by = {g["name"]: g for g in sheets.all_of("gongzhi")}
            gongzhi = [{"role": n, "text": by[n]["text"] if n in by else "", "time": _fmt(by[n]["updated_ts"]) if n in by else ""} for n in [role["name"], *names]]
        rule = relations.rule()
        used = sum(1 for l in lines if l["mine"])
        return page("relation.html", role=role, owner=role["name"], mode="list", tab=tab, can=can, why=why, lines=lines, contacts=[n for n in names if n not in have], rule=rule,
                    used=used, total_used=len(lines), rel_on=relations.enabled(), new_paused=False, gongzhi=gongzhi, gz_closed=gz_closed, gongzhi_n=sum(1 for g in gongzhi if g["text"]),
                    gongzhi_names={g["role"]: g["role"] for g in gongzhi})

    def _rel_target(role, other: str):
        target = roles_core.get_role_by_name(other)
        if not target or target["id"] == role["id"]:
            abort(404)
        return target

    @app.route("/p/me/rel/<other>")
    def rel_line(other):
        _ext_or_404("relations")
        role, bad = need_login()
        if bad:
            return bad
        target = _rel_target(role, other)
        rel = relations.get_rel(role["id"], target["id"])
        if not rel:
            flash("你们之间还没有关系线。")
            return redirect(url_for("rel_page"))
        tab = request.args.get("tab") if request.args.get("tab") in ("chat", "detail", "time") else "chat"
        if tab in ("chat", "detail"):
            relations.mark_seen(rel["id"], role["id"], tab)
        names = {role["id"]: role["name"], target["id"]: target["name"]}
        details = [{"id": d["id"], "from_role": d["from_name"], "text": d["text"], "edited_at": 0} for d in relations.details(rel["id"])]
        fix = lambda rows: [{**r, "from_role": names.get(r["from_role_id"], ""), "created_at": r["created_ts"], "edited_at": r["edited_ts"]} for r in rows]
        can, why = _rel_can(role)
        rule = relations.rule()
        return page("relation.html", role=role, owner=role["name"], mode="line", other=target["name"], tab=tab, can=can, why=why, rule=rule, details=details,
                    chats=fix(relations.items(rel["id"], "chat")), times=fix(relations.items(rel["id"], "time")), n_det=len(details), total=sum(len(d["text"]) for d in details),
                    line={"system": False, "confirmed": bool(rel["confirmed"]), "initiator": names.get(rel["initiator_id"], "")}, chat_max=relations.CHAT_MAX,
                    label_max=relations.TIME_LABEL_MAX, time_max=relations.TIME_TEXT_MAX, draft=None)

    def _chat_note(frm: str, text: str) -> str:
        """关系线留言也发到对方个人群（作者 2026-11-27 定），跟细节、时间点一样。"""
        return f"💬 「{frm}」在你们的关系线里留言：\n{text}\n\n（小手机 → 关系线 → {frm} 的「闲谈」里回）"

    def _rel_post(other: str, tab: str, fn):
        """关系线的写操作：登录 → 能不能写 → 违禁词 → 执行 fn(role, target) → 回到这条线。"""
        _ext_or_404("relations")
        role, bad = need_login()
        if bad:
            return bad
        target = _rel_target(role, other)
        can, why = _rel_can(role)
        why = "" if can else why
        why = why or _gate(role, "关系线", *request.form.getlist("text"), request.form.get("label", ""))
        if why:
            flash(why)
        else:
            flash(fn(role, target) or "")
        return redirect(url_for("rel_line", other=target["name"], tab=tab))

    @app.post("/p/me/rel/new")
    def rel_new():
        _ext_or_404("relations")
        role, bad = need_login()
        if bad:
            return bad
        f = request.form
        target = roles_core.get_role_by_name(f.get("to", ""))
        can, why = _rel_can(role)
        why = "" if can else why
        why = why or _gate(role, "关系线", f.get("text", ""))
        if not why and (not target or target["id"] == role["id"]):
            why = "请选一个对象。"
        if why:
            flash(why)
            return redirect(url_for("rel_page"))
        text = f.get("text", "").strip()
        if f.get("kind") == "chat":
            ok, msg = relations.add_item(role["id"], target["id"], target["name"], "chat", text)
            tab = "chat"
            if ok:
                _push(target["id"], _chat_note(role["name"], text))
        else:
            ok, msg = relations.add_detail(role["id"], target["id"], target["name"], text)
            tab = "detail"
            if ok:
                _push(target["id"], f"📝 来自「{role['name']}」的新关系细节：\n{text}\n\n（小手机或群里「查看关系线 {role['name']}」都能看）")
        if not ok:
            flash(msg)
            return redirect(url_for("rel_page"))
        flash("已发起，" + msg.replace("\n", " "))
        return redirect(url_for("rel_line", other=target["name"], tab=tab))

    @app.post("/p/me/rel/<other>/chat")
    def rel_chat(other):
        def go(r, t):
            text = request.form.get("text", "").strip()
            ok, msg = relations.add_item(r["id"], t["id"], t["name"], "chat", text)
            if ok:
                _push(t["id"], _chat_note(r["name"], text))
            return msg
        return _rel_post(other, "chat", go)

    @app.post("/p/me/rel/<other>/detail")
    def rel_detail_add(other):
        def go(r, t):
            text = request.form.get("text", "").strip()
            ok, msg = relations.add_detail(r["id"], t["id"], t["name"], text)
            if ok:
                _push(t["id"], f"📝 来自「{r['name']}」的新关系细节：\n{text}\n\n（小手机或群里「查看关系线 {r['name']}」都能看）")
            return msg
        return _rel_post(other, "detail", go)

    @app.post("/p/me/rel/<other>/time")
    def rel_time_add(other):
        def go(r, t):
            ok, msg = relations.add_item(r["id"], t["id"], t["name"], "time", request.form.get("text", ""), request.form.get("label", ""))
            if ok:
                _push(t["id"], f"🕰️ 「{r['name']}」在你们的关系线里记下了一个时间点：{request.form.get('label', '')}")
            return msg
        return _rel_post(other, "time", go)

    @app.post("/p/me/rel/<other>/confirm")
    def rel_confirm(other):
        return _rel_post(other, "detail", lambda r, t: "✅ 已确认这条关系线为完成状态。" if relations.confirm(r["id"], t["id"]) else "没有这条关系线。")

    def _rel_item_op(item_id: int, kind: str, tab: str, op: str):
        """改/删自己写的一段细节或一个时间点；写完回到对方那条线。"""
        _ext_or_404("relations")
        role, bad = need_login()
        if bad:
            return bad
        can, why = _rel_can(role)
        text = request.form.get("text", "")
        why = "" if can else why
        why = why or (_gate(role, "关系线", text, request.form.get("label", "")) if op == "edit" else "")
        other_id = 0
        if not why:
            if kind == "detail":
                with get_conn() as c:
                    row = c.execute("SELECT r.role_lo, r.role_hi FROM rel_details d JOIN relations r ON r.id=d.rel_id WHERE d.id=?", (item_id,)).fetchone()
                other_id = (row["role_hi"] if row["role_lo"] == role["id"] else row["role_lo"]) if row else 0
                if op == "edit":
                    ok, why = relations.edit_detail(item_id, role["id"], text)
                else:
                    ok, why, gone, _ = relations.delete_detail(item_id, role["id"])
                    if ok:
                        _push(other_id, f"🗑️ 「{role['name']}」撤回了一段关系细节：\n\"{gone}\"")
            else:
                if op == "edit":
                    ok, why, other_id = relations.edit_item(item_id, role["id"], text, request.form.get("label", ""))
                else:
                    ok, why, other_id = relations.delete_item(item_id, role["id"])
        flash(why)
        target = roles_core.get_role(other_id) if other_id else None
        return redirect(url_for("rel_line", other=target["name"], tab=tab) if target else url_for("rel_page"))

    @app.post("/p/me/rel/detail/<int:iid>/edit")
    def rel_detail_edit(iid):
        return _rel_item_op(iid, "detail", "detail", "edit")

    @app.post("/p/me/rel/detail/<int:iid>/delete")
    def rel_detail_delete(iid):
        return _rel_item_op(iid, "detail", "detail", "delete")

    @app.post("/p/me/rel/time/<int:iid>/edit")
    def rel_time_edit(iid):
        return _rel_item_op(iid, "time", "time", "edit")

    @app.post("/p/me/rel/time/<int:iid>/delete")
    def rel_time_delete(iid):
        return _rel_item_op(iid, "time", "time", "delete")
