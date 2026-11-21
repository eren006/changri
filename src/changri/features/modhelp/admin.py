"""设置平台 · 个性化模块介绍。"""
from flask import render_template, url_for

# 模块 id → 它在后台的页面（没有页面的写 None）。介绍文字在模板 modules_help.html 里。
PAGES = {"longterm": "longterm_page", "paidan": "paidan_page", "letters": None, "storycal": "storycal_page"}


def register(app, login_required):
    @app.route("/modules/help")
    @login_required
    def modules_help():
        from changri.core import extensions
        mods = []
        for mid, ep in PAGES.items():
            on = extensions.is_enabled(mid)
            mods.append({"id": mid, "on": on, "page": url_for(ep) if (on and ep) else ""})
        return render_template("modules_help.html", mods={m["id"]: m for m in mods})
