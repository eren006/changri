"""小手机 · 心愿。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import loopbridge, rooms as rooms_core, rpg, season, wishes
from flask import flash, redirect, request, url_for
from changri.core.textutil import hm_text


def register(app, ctx):
    _ext_or_404, _gate, _send_fn, need_login, page = ctx._ext_or_404, ctx._gate, ctx._send_fn, ctx.need_login, ctx.page

    @app.route("/p/me/wishes")
    def wishes_page():
        _ext_or_404("wishes")
        role, bad = need_login()
        if bad:
            return bad
        from changri.core import godmode, windows
        why = ("现在不是进行中的季度，不能挂心愿。" if season.phase() != "playing" else "") or windows.check("wish") or godmode.check(role["id"], "wish")
        wall = [{"id": w["id"], "who": wishes.label(w), "mine": w["poster_id"] == role["id"], "day": "摘的当天", "time": f"{hm_text(w['start_hm'], w['end_hm'])}",
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
        try:                                    # 跟 QQ 里挂的一样：戏群 + （开了的话）公告群
            loopbridge.run(wishes.broadcast(wishes.get(int(info)), _send_fn()))
        except Exception:  # noqa: BLE001
            pass
        from changri.core import letters
        ctx.draft_done("wish")
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
        why = _gate(role, "心愿")
        if why:
            flash(why)
            return redirect(url_for("wishes_page"))

        async def go():
            from changri.plugins import play
            ok, info, room_id = wishes.take(wid, role["id"])
            if not ok:
                return info
            if not await play.settle_wish_room(wid, room_id):
                return play.NO_GROUP_TEXT.format(kind="心愿")
            w, room = wishes.get(wid), rooms_core.get(room_id)
            await play._push(w["poster_id"], f"🌠 你的心愿 #{wid} 被 {role['name']} 摘走了！")
            await play._invite(w["poster_id"], room, fix=("💌 心愿", "💌 心愿约会"))
            await play._announce_in_room(room_id)
            return f"✅ 你摘了心愿 #{wid}，对方已收到通知。\n{play._slot(room)}\n{play._group_line(room)}\n房间号 R{room_id}" + play._qq_hint(role["id"], room)
        try:
            flash(loopbridge.run(go()))
        except Exception as e:  # noqa: BLE001
            flash(loopbridge.friendly_error(e, "没摘成"))
        return redirect(url_for("wishes_page"))
