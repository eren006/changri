"""设置平台 · 功能简介：个性化模块（/modules/help）、核心功能和辅助功能（/features/help）各是干嘛的。"""
import re

from flask import render_template, url_for
from markupsafe import Markup, escape

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

    @app.route("/features/help")
    @login_required
    def features_help():
        return render_template("features_help.html", fh_groups=feature_groups())


def feature_groups() -> list[dict]:
    """[{key, title, lede, items: [{id, name, on, core, page, page_label, what, player, admin}]}]，顺序跟「功能与设置」一样。"""
    from changri.core import extensions
    from .guide import GUIDE
    groups = [{"key": "core", "title": "核心功能", "lede": "一直开着，不能关。", "items": []},
              {"key": "aux", "title": "辅助功能", "lede": "有开关：关着的功能，指令不响应、后台页面也不显示。打开或关掉以后点「重启并生效」。", "items": []}]
    cards = [(0, c) for c in extensions.CORE_CARDS] + [(1, e) for e in extensions.OFFICIAL if not e.get("personal")]
    for gi, c in cards:
        g = GUIDE.get(c["id"])
        if not g:
            continue
        core = gi == 0
        on = core or extensions.is_enabled(c["id"])
        links = [(ep, label) for ep, label in (c.get("links") or []) if ep != "dashboard"]
        page, page_label = "", ""
        if on and links:
            page, page_label = url_for(links[0][0]), links[0][1]
        groups[gi]["items"].append({"id": c["id"], "name": c["name"], "on": on, "core": core,
                                    "page": page, "page_label": page_label, **{k: _codes(v) for k, v in g.items()}})
    return groups


def _codes(text: str) -> Markup:
    """`指令` → <code>指令</code>，其余照常转义。"""
    return Markup(re.sub(r"`([^`]+)`", r"<code>\1</code>", str(escape(text))))
