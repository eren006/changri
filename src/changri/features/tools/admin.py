"""设置平台 · 小工具。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import render_template
from changri.core.db import get_conn


def register(app, login_required):
    @app.route("/tools")
    @login_required
    def tools_page():
        """小工具：随机分组。只在页面里算，不动任何数据；这里只负责把这个机器人的角色名单交给页面。"""
        with get_conn() as c:
            people = [{"name": r["name"], "npc": bool(r["is_npc"])} for r in c.execute("SELECT name, is_npc FROM roles ORDER BY id")]
        return render_template("tools.html", people=people)
