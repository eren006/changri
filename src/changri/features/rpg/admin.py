"""设置平台 · 物品、属性、地点与商城。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import rpg as rpg_core
from changri.core.db import get_conn


def register(app, login_required):
    def _role_rows():
        with get_conn() as conn:
            return [dict(x) for x in conn.execute("SELECT id,name FROM roles ORDER BY name")]

    @app.route("/rpg")
    @login_required
    def rpg_page():
        import json as _json
        items = rpg_core.items()
        for x in items:                                              # 「体力 +2」这样的小标签，和编辑框里的「体力+2」写法
            try:
                eff = _json.loads(x.get("attr_effects") or "{}")
            except ValueError:
                eff = {}
            x["chips"] = [{"text": f"{k} {v:+d}" if isinstance(v, int) else f"{k} {v}", "dir": "up" if isinstance(v, int) and v > 0 else "down" if isinstance(v, int) and v < 0 else ""} for k, v in eff.items()]
            x["eff_text"] = " ".join(f"{k}{v:+d}" if isinstance(v, int) else f"{k}{v}" for k, v in eff.items())
        return render_template("rpg.html", items=items, plain_items=[x for x in items if x["kind"] != "currency"],
                               currencies=[x for x in items if x["kind"] == "currency"], shop=rpg_core.listings(), roles=_role_rows(), exchanges=rpg_core.exchanges(), recipes=rpg_core.recipes())

    @app.route("/attrs")
    @login_required
    def rpg_attrs_page():
        return render_template("rpg_attrs.html", defs=rpg_core.attr_defs(), currencies=[x for x in rpg_core.items() if x["kind"] == "currency"])

    @app.route("/places")
    @login_required
    def rpg_places_page():
        places = rpg_core.places()
        with get_conn() as conn:
            holders: dict[int, list[str]] = {}
            for r in conn.execute("SELECT k.place_id, r.name FROM rpg_place_keys k JOIN roles r ON r.id=k.role_id ORDER BY r.name"):
                holders.setdefault(r["place_id"], []).append(r["name"])
        for p in places:
            p["holders"] = holders.get(p["id"], [])
        return render_template("rpg_places.html", places=places, roles=_role_rows())

    @app.post("/attrs/save")
    @login_required
    def rpg_attr_save():
        ok, msg = rpg_core.save_attr_def(request.form.get("name", ""), request.form.get("min"), request.form.get("max"),
                                         request.form.get("default", 0), request.form.get("description", ""))
        flash("属性已保存" if ok else msg)
        return redirect(url_for("rpg_attrs_page"))

    @app.post("/rpg/item")
    @login_required
    def rpg_item_save():
        ok, msg = rpg_core.save_item(request.form.get("code", ""), request.form.get("name", ""), request.form.get("kind", "item"),
                                     request.form.get("description", ""), request.form.get("effects", ""), request.form.get("max_uses", -1),
                                     bool(request.form.get("can_give")))
        flash(("货币已保存" if request.form.get("kind") == "currency" else "物品已保存") if ok else msg)
        return redirect(url_for("rpg_attrs_page" if request.form.get("back") == "attrs" else "rpg_page"))

    @app.post("/rpg/items/bulk")
    @login_required
    def rpg_items_bulk():
        flash(rpg_core.bulk_summary(*rpg_core.bulk_items(request.form.get("text", ""))))
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/inventory")
    @login_required
    def rpg_inventory_adjust():
        ok, msg = rpg_core.adjust(int(request.form.get("role_id", 0)), request.form.get("item", ""), request.form.get("delta", 0), "后台调整")
        flash("背包已调整" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/shop")
    @login_required
    def rpg_shop_save():
        ok, msg = rpg_core.set_listing(request.form.get("item", ""), request.form.get("price", 0), request.form.get("currency", ""))
        flash("已上架" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/exchange")
    @login_required
    def rpg_exchange_save():
        f = request.form
        ok, msg = rpg_core.save_exchange(f.get("from_item", ""), f.get("from_n"), f.get("to_item", ""), f.get("to_n"), f.get("enabled", "on") == "on", f.get("id", type=int))
        flash("兑换已保存" if ok else msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/recipe")
    @login_required
    def rpg_recipe_save():
        f = request.form
        mats = list(zip(f.getlist("mat_item"), f.getlist("mat_n")))
        ok, msg = rpg_core.save_recipe(f.get("name", ""), mats, f.get("out_item", ""), f.get("out_n"))
        flash(msg)
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/recipe/<int:recipe_id>/delete")
    @login_required
    def rpg_recipe_delete(recipe_id):
        rpg_core.delete_recipe(recipe_id)
        flash("配方已删除")
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/exchange/<int:ex_id>/delete")
    @login_required
    def rpg_exchange_delete(ex_id):
        rpg_core.delete_exchange(ex_id)
        flash("兑换已删除")
        return redirect(url_for("rpg_page"))

    @app.post("/rpg/shop/<code>/delete")
    @login_required
    def rpg_shop_delete(code):
        with get_conn() as conn:
            conn.execute("DELETE FROM rpg_shop WHERE item_code=?", (code,))
        return redirect(url_for("rpg_page"))

    @app.post("/places/save")
    @login_required
    def rpg_place_save():
        ok, msg = rpg_core.save_place(request.form.get("name", ""), request.form.get("description", ""), bool(request.form.get("locked")))
        flash("地点已保存" if ok else msg)
        return redirect(url_for("rpg_places_page"))

    @app.post("/places/bulk")
    @login_required
    def rpg_places_bulk():
        flash(rpg_core.bulk_summary(*rpg_core.bulk_places(request.form.get("text", ""))))
        return redirect(url_for("rpg_places_page"))

    @app.post("/places/key")
    @login_required
    def rpg_key_save():
        rpg_core.set_key(int(request.form.get("role_id", 0)), int(request.form.get("place_id", 0)), bool(request.form.get("on")))
        return redirect(url_for("rpg_places_page"))
