"""设置平台 · 季度、出场与 NPC。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import loopbridge, npc as npc_core, season as season_core
from changri.core.db import get_config, get_conn, set_config


def register(app, login_required):
    def _all_roles():
        with get_conn() as c:
            return [dict(r) for r in c.execute("SELECT id, name, is_npc FROM roles ORDER BY name")]

    @app.route("/season")
    @login_required
    def season_page():
        from changri.core import entrance as entrance_core
        return render_template("season.html", running=season_core.running(), phase=season_core.phase(), day=season_core.day_label(),
                               auto=get_config("auto_day") == "1", booked=season_core.booked(), past=[x for x in season_core.all_seasons() if x["status"] == "ended"],
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
        return Response(season_poster.render(data, request.args.get("skin", "")), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

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
