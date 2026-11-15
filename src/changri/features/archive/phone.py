"""小手机 · 戏录。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
import time
from changri.core import rooms as rooms_core
from flask import flash, redirect, request, url_for


def register(app, ctx):
    _csrf, _push, _room_for, need_login, page = ctx._csrf, ctx._push, ctx._room_for, ctx.need_login, ctx.page

    def _archive_send(role_id, text):
        from changri.core.qqsend import IMG
        return _push(role_id, IMG + text)

    app.config["ARCHIVE_SEND"] = _archive_send

    @app.route("/p/me/archive")
    def archive_page():
        role, bad = need_login()
        if bad:
            return bad
        from changri.core import roomlog
        items = roomlog.archive_for(role["id"])
        items.sort(key=lambda x: x["status"] != "open")
        for x in items:
            x["who"] = "、".join(m["name"] for m in rooms_core.members(x["id"]) if m["role_id"] != role["id"]) or "自己"
        return page("archive.html", role=role, mode="discover", items=items)

    def _archive_render(r, role):
        rid = r["id"]
        from changri.core import roomlog
        from changri.core.overview import slot_text
        chat = request.args.get("chat") == "1"
        allines = roomlog.lines(rid)
        me_id = role["id"] if role else None
        shown = [{"name": x["name"], "text": x["text"], "rp": bool(x["is_rp"]), "me": x["role_id"] == me_id,
                  "time": time.strftime("%H:%M", time.localtime(x["ts"]))} for x in allines if chat or x["is_rp"]]
        view = "archive_view" if role else "admin_room_view"
        return page("archive_view.html", role=role, mode="discover", room={"id": rid, "kind": r["kind"], "slot": slot_text(r), "status": r["status"],
                    "who": "、".join(m["name"] for m in rooms_core.members(rid))},
                    lines=shown, stats=[x for x in roomlog.stats(rid) if x["rp"] or chat], chat=chat, view=view,
                    back=url_for("archive_page") if role else url_for("admin_rooms"), back_name="戏录" if role else "全部房间",
                    has_chat=any(not x["is_rp"] for x in allines), can_send=bool(role) and bool(roomlog.review_text(rid)))

    ctx.archive_render = _archive_render          # 管理员手机看房间也用这一页

    @app.route("/p/me/archive/<int:rid>")
    def archive_view(rid):
        role, bad = need_login()
        if bad:
            return bad
        return _archive_render(_room_for(role, rid), role)

    @app.route("/p/me/archive/<int:rid>/send", methods=["POST"])
    def archive_send(rid):
        role, bad = need_login()
        if bad:
            return bad
        _csrf()
        _room_for(role, rid)
        from changri.core import roomlog
        text = roomlog.review_text(rid)
        if not text:
            flash("这场没有记到戏文。")
        elif app.config["ARCHIVE_SEND"](role["id"], text):
            flash("已发到你的个人群。")
        else:
            flash("发送失败：没有可用的个人群，或机器人暂时发不出去。")
        return redirect(url_for("archive_view", rid=rid))
