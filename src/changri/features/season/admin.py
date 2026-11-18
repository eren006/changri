"""设置平台 · 季度、出场与 NPC。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import Response, abort, flash, redirect, render_template, request, url_for
from changri.core import loopbridge, npc as npc_core, season as season_core, seasonarchive
from changri.core.db import get_config, get_conn, set_config


def register(app, login_required):
    def _report_ctx():
        from changri.core import seasonreport
        from changri.core.render import season_poster
        live = [] if season_core.phase() == "none" else seasonreport.live_players()
        return {"on": seasonreport.enabled(), "skin": seasonreport.skin(), "skins": season_poster.skin_choices(),
                "live": [{"id": p["id"], "name": p["name"]} for p in live]}

    def _all_roles():
        with get_conn() as c:
            return [dict(r) for r in c.execute("SELECT id, name, is_npc FROM roles ORDER BY name")]

    @app.route("/season")
    @login_required
    def season_page():
        from changri.core import entrance as entrance_core
        return render_template("season.html", running=season_core.running(), phase=season_core.phase(), day=season_core.day_label(),
                               auto=get_config("auto_day") == "1", booked=season_core.booked(), past=[x for x in season_core.all_seasons() if x["status"] == "ended"], archived=seasonarchive.archived_ids(), cal=season_core.month_grid(request.args.get("cal", "")), cal_line=season_core.upcoming_line(), report=_report_ctx(),
                               preview=season_core.wipe_preview(), stage=entrance_core.get_stage(), queue=entrance_core.queue(),
                               entrance_on=entrance_core.active(), npcs=npc_core.listing(), others=[r for r in _all_roles() if not r['is_npc']])

    @app.post("/season/book")
    @login_required
    def season_book():
        f = request.form
        ok, msg = season_core.book(f.get("name", ""), f.get("start", ""), f.get("end", ""), f.get("supp", ""))
        flash(msg)
        return redirect(url_for("season_page"))

    @app.post("/season/npc/create")
    @login_required
    def season_npc_create():
        ok, msg = npc_core.create(request.form.get("name", ""))
        flash(msg)
        return redirect(url_for("season_page") + "#npc")

    @app.post("/season/npc/<int:role_id>/profile")
    @login_required
    def season_npc_profile(role_id):
        """NPC 改档案（和玩家在群里发的「修改名字 / 性别 / 年龄 / 皮相 / 签名 / 简称」同一套规则；管理员代改不受冷却限制）。"""
        from changri.core import charprofile, roles as roles_core
        r = roles_core.get_role(role_id)
        if not r or not r["is_npc"]:
            flash("没有这个 NPC")
        else:
            text = request.form.get("lines", "").strip()
            flash(charprofile.edit(r, text, admin=True) if text else "写上要改的，比如：修改皮相 某某")
        return redirect(url_for("season_page") + "#npc")

    @app.post("/season/npc/flag")
    @login_required
    def season_npc_flag():
        ok, msg = npc_core.set_flag(request.form.get("name", ""), request.form.get("on") == "1")
        flash(msg)
        return redirect(url_for("season_page") + "#npc")

    @app.post("/season/<int:season_id>/delete")
    @login_required
    def season_delete(season_id):
        flash("预订已删除" if season_core.delete_booking(season_id) else "只能删除还没开始的预订")
        return redirect(url_for("season_page"))

    @app.post("/season/<int:season_id>/edit")
    @login_required
    def season_edit(season_id):
        f = request.form
        flash(season_core.update_booking(season_id, f.get("name", ""), f.get("start", ""), f.get("end", ""), f.get("supp", ""))[1])
        return redirect(url_for("season_page"))

    # ---------- 历史季度：回看、改名、删除 ----------

    def _past_or_404(season_id):
        s = season_core.get_season(season_id)
        if not s or s["status"] != "ended":
            abort(404)
        return s

    @app.route("/season/<int:season_id>")
    @login_required
    def season_view(season_id):
        s = _past_or_404(season_id)
        arc = seasonarchive.get(season_id)
        players = sorted(arc["summary"].get("players", []), key=lambda p: (p["npc"], -p["chars"], p["name"])) if arc else []
        poster_idx = {p["name"]: i for i, p in enumerate([p for p in (arc["summary"].get("players", []) if arc else []) if p.get("report")])}
        return render_template("season_view.html", s=s, arc=arc, players=players, poster_idx=poster_idx, counts=arc["summary"].get("counts", {}) if arc else {},
                               rooms=arc["rooms"] if arc else [])

    @app.route("/season/<int:season_id>/room/<int:rid>")
    @login_required
    def season_room(season_id, rid):
        s = _past_or_404(season_id)
        arc = seasonarchive.get(season_id)
        room = next((r for r in (arc["rooms"] if arc else []) if r["id"] == rid), None)
        if not room:
            abort(404)
        return render_template("season_room.html", s=s, room=room)

    @app.route("/season/<int:season_id>/room/<int:rid>/export")
    @login_required
    def season_room_export(season_id, rid):
        s = _past_or_404(season_id)
        arc = seasonarchive.get(season_id)
        room = next((r for r in (arc["rooms"] if arc else []) if r["id"] == rid), None)
        if not room:
            abort(404)
        return Response(seasonarchive.room_text(s["name"], room), mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=S{season_id}-R{rid}.txt"})

    @app.post("/season/<int:season_id>/rename")
    @login_required
    def season_rename(season_id):
        f = request.form
        if f.get("start"):                         # 列表上的「编辑」：名字 + 档期
            msg = season_core.edit_history(season_id, f.get("name", ""), f.get("start", ""), f.get("end", ""), f.get("supp", ""))[1]
        else:
            msg = season_core.rename_season(season_id, f.get("name", ""))[1]
        flash(msg)
        return redirect(url_for("season_page") + "#past" if f.get("back") == "list" else url_for("season_view", season_id=season_id))

    @app.post("/season/<int:season_id>/delete-history")
    @login_required
    def season_delete_history(season_id):
        s = season_core.get_season(season_id)
        back = request.form.get("back") == "list"
        if not s or request.form.get("confirm", "").strip() != s["name"]:
            flash("没有删除：确认框里要输入这个季度的名字")
            return redirect(url_for("season_page") + "#past" if back or not s else url_for("season_view", season_id=season_id))
        ok, msg = season_core.delete_history(season_id)
        flash(msg)
        return redirect(url_for("season_page") + "#past" if ok or back else url_for("season_view", season_id=season_id))

    @app.post("/season/start")
    @login_required
    def season_start():
        ok, msg = season_core.start(request.form.get("name", ""), clear=request.form.get("clear") == "1")
        flash("上季的角色和互动记录还没清空：先点「收尾」，或勾选「清空后开始」" if msg == "NEEDS_WIPE" else msg)
        return redirect(url_for("season_page"))

    @app.post("/season/end")
    @login_required
    def season_end():
        from changri.core.qqsend import make_send, make_send_image
        flash(loopbridge.run(season_core.end_and_report(make_send(), make_send_image()), timeout=600)[1])     # 每人一张海报，人多要一会儿
        return redirect(url_for("season_page"))

    # ---------- 季末报告：开关、样式、下载 ----------

    def _safe(name: str) -> str:
        import re
        return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "海报"

    def _download(data: bytes, filename: str, mimetype: str):
        from urllib.parse import quote
        return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})

    def _live_show():
        s = season_core.running() or season_core.latest_ended()
        return s["name"] if s else ""

    @app.post("/season/report")
    @login_required
    def season_report_save():
        from changri.core.render import season_poster
        f = request.form
        if "on" in f:
            set_config("season_report", "1" if f.get("on") == "1" else "0")
            flash("季末报告已" + ("打开：结束季度时会给每位有互动的玩家发海报" if f.get("on") == "1" else "关闭"))
        if f.get("skin") in season_poster.SKINS:
            set_config("season_poster_skin", f["skin"])
            flash(f"海报样式换成「{season_poster.SKINS[f['skin']]['name']}」")
        return redirect(url_for("season_page") + "#report")

    @app.route("/season/poster/live/<int:role_id>.jpg")
    @login_required
    def season_poster_live(role_id):
        from changri.core import seasonreport
        p = next((x for x in seasonreport.live_players() if x["id"] == role_id), None)
        if not p:
            abort(404)
        return _download(seasonreport.poster_bytes(p["data"], _live_show()), f"{_safe(_live_show())}-{_safe(p['name'])}.jpg", "image/jpeg")

    @app.route("/season/poster/live.zip")
    @login_required
    def season_poster_live_zip():
        from changri.core import seasonreport
        ps = seasonreport.live_players()
        if not ps:
            flash("现在没有可以出海报的玩家（还没有互动，或者已经收尾清空了；收尾后到历史季度里下载）")
            return redirect(url_for("season_page") + "#report")
        show = _live_show()
        return _download(seasonreport.zip_posters([(_safe(p["name"]), p["data"]) for p in ps], show), f"{_safe(show)}-季末海报.zip", "application/zip")

    def _archived_players(season_id):
        s = _past_or_404(season_id)
        arc = seasonarchive.get(season_id)
        ps = [p for p in (arc["summary"].get("players", []) if arc else []) if p.get("report")]
        return s, ps

    @app.route("/season/<int:season_id>/poster/<int:idx>.jpg")
    @login_required
    def season_poster_archived(season_id, idx):
        from changri.core import seasonreport
        s, ps = _archived_players(season_id)
        if not 0 <= idx < len(ps):
            abort(404)
        return _download(seasonreport.poster_bytes(ps[idx]["report"], s["name"]), f"{_safe(s['name'])}-{_safe(ps[idx]['name'])}.jpg", "image/jpeg")

    @app.route("/season/<int:season_id>/posters.zip")
    @login_required
    def season_posters_archived_zip(season_id):
        from changri.core import seasonreport
        s, ps = _archived_players(season_id)
        if not ps:
            abort(404)
        return _download(seasonreport.zip_posters([(_safe(p["name"]), p["report"]) for p in ps], s["name"]), f"{_safe(s['name'])}-季末海报.zip", "application/zip")

    @app.route("/season/poster-preview")
    @login_required
    def season_poster_preview():
        """季末海报预览：用示例数据画一张（第一次用某款皮肤会先从主站下载素材）"""
        from flask import Response
        from changri.core.render import season_poster
        data = dict(season_poster.SAMPLE)
        s = season_core.running()
        if s:
            data["show_name"] = s["name"]
        return Response(season_poster.render(data, request.args.get("skin", "")), mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})   # 季度页的样式缩略图，别每次都重画

    @app.post("/season/wipe")
    @login_required
    def season_wipe():
        if season_core.running():
            flash("季度还在进行中，先结束")
        elif request.form.get("confirm") != "收尾":
            flash("没有清空：确认框里要输入「收尾」")
        else:
            season_core.wipe()
            flash("已收尾，本季的角色和互动记录已清空")
        return redirect(url_for("season_page"))

    @app.post("/season/day")
    @login_required
    def season_day():
        raw = request.form.get("day", "").strip().upper().lstrip("D")
        if not season_core.running():
            flash("现在没有进行中的季度")
        elif not raw.isdigit():
            flash("天数写成 D3 或 3")
        else:
            season_core.set_day(int(raw))
            flash(f"现在是 D{int(raw)}")
        return redirect(url_for("season_page"))

    @app.post("/season/autoday")
    @login_required
    def season_autoday():
        on = request.form.get("on") == "1"
        set_config("auto_day", "1" if on else "0")
        if on:
            set_config("auto_day_last", season_core.today())
        flash("自动天数已" + ("开启" if on else "关闭"))
        return redirect(url_for("season_page"))
