"""设置平台 · 故事历。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import storycal as cal


def register(app, login_required):
    @app.route("/storycal")
    @login_required
    def storycal_page():
        rows = []
        for d in range(cal.MAX_STAGES):
            n = len(cal.day_list(d))
            st = cal.stages().get(d)
            rows.append({"d": d, "text": cal.span_text(d), "n": n, "len": cal.fmt_len(1440 / n) if n else "",
                         "s": f"{st[0][0]},{st[0][1]}" if st else "", "e": f"{st[1][0]},{st[1][1]}" if st else ""})
        return render_template("storycal.html", era=cal.era(), rows=rows, now=cal.now_label(), enabled=cal.enabled())

    @app.post("/storycal")
    @login_required
    def storycal_save():
        errs = cal.save(request.form.get("era", ""), {d: request.form.get(f"d{d}", "") for d in range(cal.MAX_STAGES)})
        if errs:
            for e in errs:
                flash("没保存：" + e)
        else:
            flash("故事历已保存")
        return redirect(url_for("storycal_page"))
