"""设置平台 · 短信礼物复盘、房间复盘。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import Response, abort, flash, redirect, render_template, request, url_for
from changri.core import arclen as arclen_core, rooms as rooms_core
from changri.core.db import get_conn


def register(app, login_required):
    def _review_filters():
        """tab：room = 约会/房间，msg = 短信和礼物，两边分开看；kind 只在 msg 页签里用来再分短信/礼物。"""
        f = {k: request.args.get(k, "").strip() for k in ("role", "kind", "q", "tab")}
        f["tab"] = "msg" if f["tab"] == "msg" else "room"
        f["kind"] = f["kind"] if f["tab"] == "msg" and f["kind"] in ("sms", "gift") else ""
        return f

    def _review_events(f):
        from changri.core import roomlog
        kind = "room" if f["tab"] == "room" else (f["kind"] or "msg")
        return roomlog.events(role=f["role"], kind=kind, q=f["q"])

    @app.route("/reviews")
    @login_required
    def reviews_page():
        from changri.core import roomlog
        f = _review_filters()
        with get_conn() as conn:
            names = [r["name"] for r in conn.execute("SELECT name FROM roles ORDER BY name")]
        evts = _review_events(f)
        return render_template("reviews.html", f=f, names=names, days=roomlog.day_summary(evts), total=len(evts), arc=arclen_core.stats(), fmt_h=arclen_core.fmt)

    @app.route("/reviews/day/<label>")
    @login_required
    def review_day(label):
        f = _review_filters()
        with get_conn() as conn:
            names = [r["name"] for r in conn.execute("SELECT name FROM roles ORDER BY name")]
        evts = _review_events(f)
        if label != "all":
            evts = [e for e in evts if e["day_label"] == label]
        return render_template("review_day.html", f=f, names=names, label=label, evts=evts,
                               status_text={"open": "进行中", "ended": "已结束", "cancelled": "已取消", "pending": "待送达", "sent": "已送达", "failed": "没送到"})

    @app.route("/reviews/<int:rid>")
    @login_required
    def review_view(rid):
        from changri.core import roomlog
        room = rooms_core.get(rid)
        if not room:
            abort(404)
        from changri.core.overview import slot_text
        return render_template("review_view.html", room=room, slot=slot_text(room), who=[m["name"] for m in rooms_core.members(rid)],
                               lines=roomlog.lines(rid), stats=roomlog.stats(rid), members=rooms_core.members(rid),
                               status_text={"open": "进行中", "ended": "已结束", "cancelled": "已取消"}[room["status"]])

    def _line_ts(f, key="when"):
        """表单里的 datetime-local（2026-11-14T20:30）→ 时间戳；没填返回 None。"""
        import datetime
        v = f.get(key, "").strip()
        if not v:
            return None
        try:
            return int(datetime.datetime.fromisoformat(v).timestamp())
        except ValueError:
            return None

    @app.post("/reviews/<int:rid>/lines")
    @login_required
    def review_line_add(rid):
        from changri.core import roomlog
        f = request.form
        ok, msg = roomlog.add_line(rid, f.get("role", type=int), f.get("text", ""), _line_ts(f))
        flash(msg, "message" if ok else "error")
        return redirect(url_for("review_view", rid=rid) + "#lines")

    @app.post("/reviews/<int:rid>/lines/<int:line_id>/edit")
    @login_required
    def review_line_edit(rid, line_id):
        from changri.core import roomlog
        f = request.form
        ok, msg = roomlog.edit_line(rid, line_id, f.get("text", ""), f.get("role", type=int), _line_ts(f))
        flash(msg, "message" if ok else "error")
        return redirect(url_for("review_view", rid=rid) + "#lines")

    @app.post("/reviews/<int:rid>/lines/<int:line_id>/delete")
    @login_required
    def review_line_delete(rid, line_id):
        from changri.core import roomlog
        flash("已删除这条发言" if roomlog.delete_line(rid, line_id) else "找不到这条发言")
        return redirect(url_for("review_view", rid=rid) + "#lines")

    @app.route("/reviews/<int:rid>/export")
    @login_required
    def review_export(rid):
        from changri.core import roomlog
        text = roomlog.export_text(rid)
        if not text:
            abort(404)
        return Response(text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=R{rid}.txt"})
