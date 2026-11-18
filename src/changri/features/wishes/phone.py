"""小手机 · 心愿。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import loopbridge, publicfeed, rooms as rooms_core, rpg, season, wishes
from changri.core.db import get_config
from flask import flash, redirect, request, url_for


def register(app, ctx):
    _ext_or_404, _gate, _send_fn, need_login, page = ctx._ext_or_404, ctx._gate, ctx._send_fn, ctx.need_login, ctx.page

    @app.route("/p/me/wishes")
    def wishes_page():
        _ext_or_404("wishes")
        role, bad = need_login()
        if bad:
            return bad
        from changri.core import godmode, windows
        why = ("NPC 不能挂心愿。" if role["is_npc"] else "" ) or ("现在不是进行中的季度，不能挂心愿。" if season.phase() != "playing" else "") or windows.check("wish") or godmode.check(role["id"], "wish")
        wall = [{"id": w["id"], "who": wishes.label(w), "mine": w["poster_id"] == role["id"], "day": f"D{w['day']}", "time": f"{w['start_hm']}-{w['end_hm']}",
                 "place": w["place"], "content": w["content"], "left": wishes.left_hours(w)} for w in wishes.list_open()]
        return page("wishes.html", role=role, mode="wishes", why=why, wall=wall, places=[p["name"] for p in rpg.places()], max_content=200, rewards=[], room_on=True)

    @app.post("/p/me/wishes/post")
    def wish_post():
        _ext_or_404("wishes")
        role, bad = need_login()
        if bad:
            return bad
        f = request.form
        t1, t2 = f.get("t1", "").replace(":", ""), f.get("t2", "").replace(":", "")
        why = _gate(role, "心愿", f.get("content", ""), f.get("place", ""), f.get("nick", ""))
        if why:
            flash(why)
            return redirect(url_for("wishes_page"))
        ok, info = wishes.post(role["id"], f"{t1}-{t2}", f.get("place", "").strip(), f.get("content", "").strip(), f.get("nick", "").strip())
        if not ok:
            flash(info)
            return redirect(url_for("wishes_page"))
        if get_config("wish_public_send", "0") == "1":
            from changri.core import announce
            ann = announce.get_announce()
            if ann:
                text = wishes.broadcast_text(wishes.get(int(info)))
                try:
                    loopbridge.run(_send_fn()(ann[0], ann[1], text))
                    publicfeed.record("wish", text)
                except Exception:  # noqa: BLE001
                    pass
        from changri.core import letters
        flash(wishes.post_ok_text(wishes.get(int(info))) + letters.paid_note(role["id"], "wish"))
        return redirect(url_for("wishes_page"))

    @app.post("/p/me/wishes/<int:wid>/withdraw")
    def wish_withdraw(wid):
        _ext_or_404("wishes")
        role, bad = need_login()
        if bad:
            return bad
        ok, why = wishes.withdraw(wid, role["id"])
        flash(f"心愿 #{wid} 已撤回。" if ok else why)
        return redirect(url_for("wishes_page"))

    @app.post("/p/me/wishes/<int:wid>/pick")
    def wish_pick(wid):
        _ext_or_404("wishes")
        role, bad = need_login()
        if bad:
            return bad
        if role["is_npc"]:
            flash("NPC 不能摘心愿。")
            return redirect(url_for("wishes_page"))
        why = _gate(role, "心愿")
        if why:
            flash(why)
            return redirect(url_for("wishes_page"))

        async def go():
            from changri.plugins import play
            ok, info, room_id = wishes.take(wid, role["id"])
            if not ok:
                return info
            w, room = wishes.get(wid), rooms_core.get(room_id)
            await play._push(w["poster_id"], f"🌠 你的心愿 #{wid} 被 {role['name']} 摘走了！")
            await play._invite(w["poster_id"], room, fix=("💌 心愿", "💌 心愿约会"))
            await play._announce_in_room(room_id)
            return f"✅ 你摘了心愿 #{wid}，对方已收到通知。\n{play._slot(room)}\n{play._group_line(room)}\n房间号 R{room_id}"
        try:
            flash(loopbridge.run(go()))
        except Exception as e:  # noqa: BLE001
            flash(f"没摘成：{str(e)[:60]}")
        return redirect(url_for("wishes_page"))
