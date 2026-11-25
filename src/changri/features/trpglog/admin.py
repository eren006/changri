"""设置平台 · 跑团日志。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
import re
from flask import abort, flash, make_response, redirect, render_template, request, url_for
from changri.core import trpgexport as trpgexport_core, trpgimport as trpgimport_core, trpglog as trpglog_core, trpgrender as trpgrender_core
from changri.core.db import get_config, get_conn, set_config


def register(app, login_required):
    @app.route("/trpglog")
    @login_required
    def trpglog_page():
        with get_conn() as conn:
            logs = [dict(x) for x in conn.execute(
                "SELECT l.*, (SELECT COUNT(*) FROM trpg_log_lines WHERE log_id=l.id) AS n FROM trpg_logs l ORDER BY l.id DESC")]
        return render_template("trpglog.html", logs=logs, rules=trpglog_core.rules(), status=trpglog_core.STATUS_TEXT,
                               prefixes=get_config("trpg_log_skip_prefixes", trpglog_core.DEFAULT_PREFIXES))

    @app.post("/trpglog/rules")
    @login_required
    def trpglog_rules():
        set_config("trpg_log_skip_brackets", "1" if request.form.get("brackets") else "0")
        set_config("trpg_log_skip_cmds", "1" if request.form.get("cmds") else "0")
        set_config("trpg_log_skip_prefixes", "\n".join(x.strip() for x in request.form.get("prefixes", "").splitlines() if x.strip()))
        flash("记录规则已保存，对之后新记的内容生效")
        return redirect(url_for("trpglog_page"))

    @app.route("/trpglog/<int:log_id>")
    @login_required
    def trpglog_view(log_id):
        log = trpglog_core.get(log_id)
        if not log:
            flash("没有这个 log", "error")
            return redirect(url_for("trpglog_page"))
        ls = trpglog_core.lines(log_id)
        style = trpgrender_core.parse_style(log["palette"])
        cmap = trpgrender_core.color_map(ls, style)
        st = trpglog_core.stats(log_id)
        return render_template("trpglog_view.html", log=log, stats=st, highlights=trpglog_core.highlights(st), status=trpglog_core.STATUS_TEXT,
                               style=style, themes=trpgrender_core.THEMES, colors=[(n, c[0]) for n, c in cmap.items()],
                               preview=trpgrender_core.render_html(log, ls, style, stats=st, highlights=trpglog_core.highlights(st)),
                               url=trpglog_core.share_url(log))

    @app.post("/trpglog/<int:log_id>/style")
    @login_required
    def trpglog_style(log_id):
        log = trpglog_core.get(log_id)
        if not log:
            return redirect(url_for("trpglog_page"))
        ls = trpglog_core.lines(log_id)
        defaults = {n: c[0] for n, c in trpgrender_core.color_map(ls, trpgrender_core.parse_style("{}")).items()}
        colors = {}
        for i, n in enumerate(trpgrender_core.speakers(ls)):
            c = request.form.get(f"color_{i}", "")
            if c and c.lower() != defaults[n].lower():
                colors[n] = c
        trpglog_core.save_style(log_id, {"theme": request.form.get("theme", "auto"), "colors": colors,
                                          "dice_hl": bool(request.form.get("dice_hl")), "dim_bot": bool(request.form.get("dim_bot"))})
        flash("样式已保存，分享页和下载的文件都会用这套")
        return redirect(url_for("trpglog_view", log_id=log_id))

    @app.route("/trpglog/<int:log_id>/download/<fmt>")
    @login_required
    def trpglog_download(log_id, fmt):
        from urllib.parse import quote
        log = trpglog_core.get(log_id)
        if not log or fmt not in ("zip", "html", "txt", "json", "summary"):
            abort(404)
        data, fname, mime = trpgexport_core.build(log, fmt)
        resp = make_response(data)
        resp.headers["Content-Type"] = mime
        resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(fname)}"
        return resp

    @app.post("/trpglog/<int:log_id>/import")
    @login_required
    def trpglog_import(log_id):
        log = trpglog_core.get(log_id)
        if not log:
            return redirect(url_for("trpglog_page"))
        up = request.files.get("file")
        raw = up.read(trpgimport_core.MAX_BYTES + 1) if up and up.filename else request.form.get("text", "").encode()
        if len(raw) > trpgimport_core.MAX_BYTES:
            flash("文件太大了，最多 2MB", "error")
        else:
            entries = trpgimport_core.parse(trpgimport_core.decode(raw))
            if not entries:
                flash("没有认出聊天记录的格式。需要每条消息前有一行「2026-11-14 21:03:11 昵称」或「昵称(2026-11-14 21:03:11)」。", "error")
            else:
                skip = {x.strip() for x in re.split(r"[,，\n]", request.form.get("skip", "")) if x.strip()}
                r = trpgimport_core.import_into(log_id, entries, skip)
                flash(f"导入完成：新增 {r['added']} 条，重复 {r['dup']} 条，跳过 {r['skipped']} 条（机器人昵称或不记录规则）")
        return redirect(url_for("trpglog_view", log_id=log_id))

    @app.post("/trpglog/<int:log_id>/share")
    @login_required
    def trpglog_share(log_id):
        log = trpglog_core.get(log_id)
        if log:
            on = bool(request.form.get("on"))
            trpglog_core.mark_shared(log_id, on)
            flash("已公开分享链接" if on else "已关闭分享链接（旧链接立刻失效）")
        return redirect(url_for("trpglog_view", log_id=log_id))

    @app.post("/trpglog/<int:log_id>/delete")
    @login_required
    def trpglog_delete(log_id):
        trpglog_core.delete(log_id)
        flash("已删除")
        return redirect(url_for("trpglog_page"))
