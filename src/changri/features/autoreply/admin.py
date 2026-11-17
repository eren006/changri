"""设置平台 · 自动回复。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
import json
from flask import flash, redirect, render_template, request, url_for
from changri.core import autoreply as autoreply_core


def register(app, login_required):
    def _ar_form() -> dict:
        f = request.form
        try:
            replies = json.loads(f.get("replies_json", "[]"))
            replies = [str(x) for x in replies] if isinstance(replies, list) else []
        except ValueError:
            replies = []
        try:
            cd = int(f.get("cooldown", "0") or 0)
        except ValueError:
            cd = 0
        return {"name": f.get("name", ""), "mode": f.get("mode", "contains"), "words": f.get("words", ""), "replies": replies,
                "who": f.get("who", "all"), "place": f.get("place", "all"), "cooldown": cd, "send_mode": f.get("send_mode", "random")}

    @app.route("/autoreply")
    @login_required
    def autoreply_page():
        return render_template("autoreply.html", rules=autoreply_core.list_rules(), modes=autoreply_core.MODES, who=autoreply_core.WHO,
                               place=autoreply_core.PLACE, send=autoreply_core.SEND, edit=request.args.get("edit", type=int))

    @app.post("/autoreply/save")
    @login_required
    def autoreply_save():
        rid = request.form.get("id", type=int)
        ok, err = autoreply_core.save_rule(_ar_form(), rid)
        flash("已保存" if ok else err)
        return redirect(url_for("autoreply_page", edit=None if ok else rid))

    @app.post("/autoreply/<int:rule_id>/toggle")
    @login_required
    def autoreply_toggle(rule_id):
        autoreply_core.set_enabled(rule_id, bool(request.form.get("on")))
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/all")
    @login_required
    def autoreply_all():
        on = request.form.get("on") == "1"
        n = autoreply_core.set_all_enabled(on)
        flash((f"已全部打开（{n} 条）" if on else f"已全部关闭（{n} 条），规则都还在，随时可以再打开") if n else ("本来就都开着" if on else "本来就都关着"))
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/<int:rule_id>/move")
    @login_required
    def autoreply_move(rule_id):
        autoreply_core.move(rule_id, -1 if request.form.get("dir") == "up" else 1)
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/<int:rule_id>/delete")
    @login_required
    def autoreply_delete(rule_id):
        autoreply_core.delete_rule(rule_id)
        flash("已删除")
        return redirect(url_for("autoreply_page"))

    @app.post("/autoreply/test")
    @login_required
    def autoreply_test():
        from flask import jsonify
        return jsonify(autoreply_core.preview(request.form.get("text", "")))
