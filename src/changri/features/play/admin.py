"""设置平台 · 官约与房间。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import loopbridge, pool as pool_core, qqmanager as qqmanager_core, qqpool as qqpool_core, rooms as rooms_core, season as season_core
from changri.core.db import current_bot, get_conn


def register(app, login_required):
    @app.route("/official")
    @login_required
    def official_page():
        from changri.core.overview import slot_text
        with get_conn() as conn:
            all_roles = [dict(r) for r in conn.execute("SELECT id, name FROM roles ORDER BY id")]
            recent = conn.execute("SELECT * FROM rooms WHERE kind='官约' OR (kind='电话' AND note='官电') ORDER BY id DESC LIMIT 15").fetchall()
        rows = [{"id": r["id"], "kind": "官电" if r["note"] == "官电" else "官约", "slot": slot_text(r), "status": r["status"],
                 "who": "、".join(m["name"] for m in rooms_core.members(r["id"]))} for r in recent]
        return render_template("official.html", roles=all_roles, rows=rows, today=season_core.current_day() or 1, running=season_core.phase() == "playing")

    @app.post("/official/launch")
    @login_required
    def official_launch():
        f = request.form
        call = f.get("kind") == "官电"
        ids = [int(x) for x in f.getlist("role") if x.isdigit()]
        if len(ids) < 2:
            flash("至少选两位角色")
            return redirect(url_for("official_page"))
        with get_conn() as conn:
            found = {r["id"]: r for r in conn.execute(f"SELECT * FROM roles WHERE id IN ({','.join('?' * len(ids))})", ids)}
        targets = [found[i] for i in ids if i in found]
        day = f.get("day", "").strip()
        place = "" if call else f.get("place", "").strip()      # 官电没有地点（页面上也会把地点框藏起来）
        from changri.plugins import play           # 依赖 NoneBot，放在真要发之前再导入
        ok, text, _rid = loopbridge.run(play.launch_official(targets, f.get("time", "").strip(), place, int(day) if day.isdigit() else None, call=call), timeout=180)
        flash(text if ok else f"❌ {text}")
        return redirect(url_for("official_page"))

    @app.post("/rooms/<int:rid>/end")
    @login_required
    def room_end(rid):
        room = rooms_core.get(rid)
        pool_id = room["pool_id"] if room else None
        rooms_core.end(rid)
        g = pool_core.get(pool_id) if pool_id else None
        if g and pool_core.is_qq_only(g):                  # 房间群：群名改回「长日备用」（可在「QQ 小号与群号组（双骰）」页改）
            loopbridge.run(qqmanager_core.room_rename(current_bot(), g, qqpool_core.free_name(g["set_name"])))
        flash(f"R{rid} 已结束（网页上结束不会通知玩家，群放回池里）")
        return redirect(url_for("dashboard") + "#rooms")
