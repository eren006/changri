"""小手机 · 角色（背包、属性、抽卡）。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import extensions, gacha, rpg
from changri.core.db import get_conn
from flask import jsonify, request


def register(app, ctx):
    _ext_or_404, current, need_login, page = ctx._ext_or_404, ctx.current, ctx.need_login, ctx.page

    def _rpg_view(role_id: int) -> dict:
        import json as _json
        inv = rpg.inventory(role_id)
        with get_conn() as c:
            have = {r["name"]: r["value"] for r in c.execute("SELECT name, value FROM role_attrs WHERE role_id=?", (role_id,))}
        attrs = [{"name": d["name"], "value": have.get(d["name"], d["default_value"]), "min": d["min_value"], "max": d["max_value"], "desc": d["description"] or ""} for d in rpg.attr_defs()]
        items = []
        for x in inv:
            if x["kind"] == "currency":
                continue
            eff = _json.loads(x["attr_effects"] or "{}")
            items.append({"name": x["name"], "count": x["count"], "desc": x["description"] or "", "effect": "、".join(f"{k}{'+' if v > 0 else ''}{v}" for k, v in eff.items()),
                          "uses": x["remaining_uses"] if x["remaining_uses"] >= 0 else None})
        return {"currencies": [{"name": x["name"], "count": x["count"]} for x in inv if x["kind"] == "currency"], "attrs": attrs, "items": items}

    def _draw_state(role) -> dict:
        st = {s["id"]: s for s in gacha.my_status(role["id"])}
        pools = []
        for p in gacha.pools(True):
            s = st.get(p["id"], {})
            pools.append({"name": p["name"], "left": s.get("tickets", 0), "can": s.get("tickets", 0) > 0 and bool(p["prizes"]),
                          "pity": f"距保底 {s['left']} 抽" if p["pity_n"] and s.get("has_rare") else ""})
        return {"ok": True, "enabled": True, "can": not role["is_npc"], "why": "NPC 不参加抽取。" if role["is_npc"] else "", "pools": pools, "pending": 0, "results": gacha.history(role["id"], 20)}

    @app.route("/p/me/char")
    def char_page():
        role, bad = need_login()
        if bad:
            return bad
        draw_on = extensions.is_enabled("gacha")
        view = request.args.get("view") if request.args.get("view") in ("attrs", "draw") else "bag"
        if view == "draw" and not draw_on:
            view = "bag"
        return page("char.html", role=role, mode="char", view=view, rpg=_rpg_view(role["id"]), draw_on=draw_on, draw_state=_draw_state(role) if view == "draw" else None)

    @app.route("/p/me/draw/state")
    def draw_state():
        _ext_or_404("gacha")
        role = current()
        if not role:
            return jsonify(ok=False), 401
        return jsonify(_draw_state(role))

    @app.post("/p/me/draw")
    def draw():
        _ext_or_404("gacha")
        role = current()
        if not role:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        if role["is_npc"]:
            return jsonify(ok=False, msg="NPC 不参加抽取。")
        ok, msg = gacha.draw(role["id"], request.form.get("pool", ""), 1)
        return jsonify(ok=ok, msg=msg if not ok else "")
