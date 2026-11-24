"""设置平台 · 公知、二表与关系线。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from changri.core import relations as relations_core, sheets as sheets_core
from changri.core.db import get_config, get_conn, set_config
from changri.admin.common import _fmt_ts


def register(app, login_required):
    @app.route("/relations")
    @login_required
    def relations_page():
        with get_conn() as conn:
            rels = conn.execute(
                "SELECT x.*, a.name AS a_name, b.name AS b_name, i.name AS init_name, "
                "(SELECT COUNT(*) FROM rel_details d WHERE d.rel_id=x.id) AS n, "
                "(SELECT COALESCE(SUM(LENGTH(text)),0) FROM rel_details d WHERE d.rel_id=x.id) AS chars "
                "FROM relations x JOIN roles a ON a.id=x.role_lo JOIN roles b ON b.id=x.role_hi JOIN roles i ON i.id=x.initiator_id "
                "ORDER BY x.id DESC").fetchall()
        waits = {w["rel_id"]: w for w in relations_core.waiting_lines()}
        people: dict[int, dict] = {}                       # 每个角色：发起几条、收到几条、各条线的概况（页面上点开才看）
        for r in rels:
            for me, other, sent in ((r["role_lo"], r["b_name"], r["initiator_id"] == r["role_lo"]), (r["role_hi"], r["a_name"], r["initiator_id"] == r["role_hi"])):
                p = people.setdefault(me, {"id": me, "name": r["a_name"] if me == r["role_lo"] else r["b_name"], "sent": 0, "got": 0, "lines": []})
                p["sent" if sent else "got"] += 1
                w = waits.get(r["id"])
                p["lines"].append({"id": r["id"], "other": r["b_name"] if me == r["role_lo"] else r["a_name"], "sent": sent, "n": r["n"], "chars": r["chars"],
                                   "confirmed": r["confirmed"], "wait": w})
        sheets = sheets_core.overview()
        view = "relations" if request.args.get("view") == "relations" else "sheets"
        from changri.core import deadlines
        dl = {k: {"at": deadlines.get(k), "passed": deadlines.passed(k), "reopened": deadlines.reopened(k)} for k in deadlines.KEYS}
        return render_template("relations.html", view=view, rels=rels, people=sorted(people.values(), key=lambda x: x["name"]), stats=relations_core.stats(), waits=waits,
                               late=relations_core.overdue(), limit=relations_core.alert_hours(), sheets=sheets, dl=dl,
                               sheets_public=get_config("gongzhi_public", "0") == "1",
                               n_gz=sum(1 for x in sheets if x["gongzhi"]), n_eb=sum(1 for x in sheets if x["erbiao"]))

    @app.route("/relations/<int:rel_id>/view")
    @login_required
    def relation_view(rel_id):
        with get_conn() as conn:
            r = conn.execute("SELECT x.*, a.name AS a_name, b.name AS b_name FROM relations x JOIN roles a ON a.id=x.role_lo JOIN roles b ON b.id=x.role_hi WHERE x.id=?", (rel_id,)).fetchone()
        if not r:
            abort(404)
        return jsonify({"title": f"{r['a_name']} ⇄ {r['b_name']}", "confirmed": bool(r["confirmed"]),
                        "details": [{"from": d["from_name"], "text": d["text"], "ts": _fmt_ts(d["created_ts"])} for d in relations_core.details(rel_id)]})

    @app.route("/sheets/view/<kind>/<int:role_id>")
    @login_required
    def sheet_view(kind, role_id):
        sh = sheets_core.get(role_id, kind) if kind in sheets_core.KINDS else None
        if not sh:
            abort(404)
        return jsonify({"title": f"{sh['name']}的{sheets_core.KINDS[kind]}", "text": sh["text"], "ts": _fmt_ts(sh["updated_ts"]),
                        "images": [url_for("sheet_img", name=n) for n in sh["images"]]})

    @app.post("/sheets/gongzhi/<int:role_id>/edit")
    @login_required
    def sheet_gongzhi_edit(role_id):
        """管理员改某人的公知文字（照老系统）：图片不动；玩家之后重新提交会覆盖。"""
        flash("公知文字已改好。玩家之后重新「提交公知」会覆盖。" if sheets_core.set_text(role_id, "gongzhi", request.form.get("text", ""))
              else "这个人还没交公知。")
        return redirect(url_for("relations_page", view="sheets"))

    @app.route("/sheets/img/<name>")
    @login_required
    def sheet_img(name):
        got = sheets_core.image_bytes(name)
        if not got:
            abort(404)
        return Response(got[0], mimetype=got[1], headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @app.post("/sheets/public")
    @login_required
    def sheets_public():
        on = request.form.get("on") == "1"
        from changri.admin import quick
        quick.set_switch("gongzhi", on)                 # 和总览上的快捷开关是同一个
        flash("已开启展示公知：玩家发「查看公知」看到所有人的，发「查看公知 角色名」只看这一个人" if on else "已关闭展示公知：玩家只能看自己的")
        return redirect(url_for("relations_page", view="sheets"))

    @app.post("/deadlines/<which>")
    @login_required
    def deadline_set(which):
        """公知、关系线的截止时间（可选）。空 = 没有截止。"""
        from changri.core import deadlines
        if which not in deadlines.KEYS:
            abort(404)
        back = redirect(url_for("relations_page", view="sheets" if which == "gongzhi" else "relations"))
        raw = "" if request.form.get("clear") else request.form.get("at", "")
        if not deadlines.set(which, raw):
            flash("时间看不懂，请用页面上的日期时间选择器选，或者写成 2026-11-25 20:00")
            return back
        name = "公知" if which == "gongzhi" else "关系线"
        at = deadlines.get(which)
        if not at:
            flash(f"已取消{name}截止时间")
        elif which == "gongzhi":
            flash(f"公知 {at} 截止：到点自动对所有人展示、公告群播一句，玩家不能再交或修改（后台还能改文字）")
        else:
            flash(f"关系线 {at} 截止：到点后全部锁定，只能看")
        return back

    @app.post("/deadlines/<which>/reopen")
    @login_required
    def deadline_reopen(which):
        """截止以后的手动后门：「重新开放」↔「重新关闭」，同一个按钮来回切。"""
        from changri.core import deadlines
        if which not in deadlines.KEYS:
            abort(404)
        on = request.form.get("on") == "1"
        deadlines.set_reopened(which, on)
        name = "公知" if which == "gongzhi" else "关系线"
        flash(f"已重新开放{name}：玩家又可以{'交和改' if which == 'gongzhi' else '写、改、撤回'}了。要锁回去，按「重新关闭{name}」。" if on
              else f"已重新关闭{name}：玩家只能看。")
        return redirect(url_for("relations_page", view="sheets" if which == "gongzhi" else "relations"))

    @app.post("/relations/alert")
    @login_required
    def relations_alert():
        try:
            hours = max(0.0, float(request.form.get("hours", "0") or 0))
        except ValueError:
            flash("请填数字，0 表示不提醒")
            return redirect(url_for("relations_page", view="relations"))
        set_config("rel_alert_hours", f"{hours:g}")
        flash("已保存：" + (f"超过 {hours:g} 小时没回复会提醒" if hours else "不再提醒"))
        return redirect(url_for("relations_page", view="relations"))

    @app.post("/relations/<int:rel_id>/delete")
    @login_required
    def relations_delete(rel_id):
        relations_core.delete_rel(rel_id)
        flash("关系线已删除")
        return redirect(url_for("relations_page", view="relations"))
