"""设置平台 · 漂流瓶记录（照老系统管理手机「漂流瓶」）：每个瓶子真正的抛瓶人、捡瓶人，和来回的每一句。玩家那边永远是匿名的，只有这里看得到是谁。"""
from flask import render_template, request

from changri.core.db import get_conn


def register(app, login_required):
    @app.route("/bottles")
    @login_required
    def bottles_page():
        q = (request.args.get("q") or "").strip()
        with get_conn() as c:
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM roles")}
            bottles = [dict(b) for b in c.execute("SELECT * FROM drift_bottles ORDER BY id DESC LIMIT 300")]
            for b in bottles:
                b["thrower"], b["catcher"] = names.get(b["thrower_id"], "（已删除）"), names.get(b["catcher_id"], "（已删除）")
                b["msgs"] = [{**dict(m), "who": names.get(m["from_id"], "（已删除）"), "side": "抛瓶人" if m["from_id"] == b["thrower_id"] else "捡瓶人"}
                             for m in c.execute("SELECT * FROM drift_msgs WHERE bottle_id=? ORDER BY id", (b["id"],))]
        if q:
            bottles = [b for b in bottles if q in b["thrower"] or q in b["catcher"] or any(q in m["content"] for m in b["msgs"])]
        return render_template("bottles.html", bottles=bottles, q=q)
