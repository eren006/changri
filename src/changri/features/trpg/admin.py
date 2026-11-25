"""设置平台 · 跑团角色卡。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import trpg as trpg_core, trpglog as trpglog_core


def register(app, login_required):
    @app.route("/trpg")
    @login_required
    def trpg_page():
        q = request.args.get("q", "").strip().lower()
        players = trpg_core.players_overview()
        if q:
            players = [p for p in players if q in p["display"].lower() or any(q in c["name"].lower() for c in p["cards"])]
        return render_template("trpg.html", players=players, q=q, total=len(trpg_core.players_overview()))

    @app.route("/trpg/card/<int:card_id>")
    @login_required
    def trpg_card_page(card_id):
        card = trpg_core.get_card(card_id)
        if not card:
            flash("没有这张角色卡", "error")
            return redirect(url_for("trpg_page"))
        attrs = trpg_core.list_attrs(card_id)
        return render_template("trpg_card.html", card=card, owner=trpg_core.card_owner(card_id), attrs=attrs,
                               history=trpg_core.card_history(card_id), battles=trpglog_core.logs_of_card(card_id), title=trpg_core.dice.RULE_TITLE.get(card["rule"], card["rule"]))

    @app.post("/trpg/card/<int:card_id>/attr")
    @login_required
    def trpg_card_attr(card_id):
        if not trpg_core.get_card(card_id):
            return redirect(url_for("trpg_page"))
        items = trpg_core.parse_st(request.form.get("text", ""))
        if not items:
            flash("格式：力量60 或 力量+5（可以一次写多个）", "error")
        else:
            ok, lines = trpg_core.apply_st(card_id, items, source="后台手改")
            flash("；".join(lines) if ok else lines[0])
        return redirect(url_for("trpg_card_page", card_id=card_id))

    @app.post("/trpg/card/<int:card_id>/attr/delete")
    @login_required
    def trpg_card_attr_delete(card_id):
        name = request.form.get("name", "")
        flash("已删除" if trpg_core.delete_attr(card_id, name, source="后台手改") else "没有这个属性")
        return redirect(url_for("trpg_card_page", card_id=card_id))
