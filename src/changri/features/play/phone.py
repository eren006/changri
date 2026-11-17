"""小手机 · 邀约与网页聊天室。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
import time
from changri.core import extensions, loopbridge, outage, phonecodes, rooms as rooms_core, season
from changri.core.db import get_conn
from flask import abort, flash, jsonify, redirect, request, url_for


def register(app, ctx):
    _chat_or_404, _gate, _mjson, _room_for, _room_row, need_login, page = ctx._chat_or_404, ctx._gate, ctx._mjson, ctx._room_for, ctx._room_row, ctx.need_login, ctx.page

    def _play():
        from changri.plugins import play          # 依赖 NoneBot，真要发之前再导入（测试里替换下面三个 _room_* 函数）
        return play

    def _room_start(kind, me, targets, time_text, place):
        return loopbridge.run(_play().start_room(kind, me, targets, time_text, place), timeout=120)

    def _room_join_notify(me, room, qid):
        return loopbridge.run(_play().notify_join_request(me, room, qid), timeout=60)

    def _room_answer_notify(me, q, accept):
        return loopbridge.run(_play().notify_join_answer(me, q, accept), timeout=60)

    def _room_end(room_id):
        return loopbridge.run(_play()._finish_room(room_id, "ended", ""), timeout=60)

    app.config["ROOM_HOOKS"] = {"start": _room_start, "join": _room_join_notify, "answer": _room_answer_notify, "end": _room_end}

    def _hook(name):
        return app.config["ROOM_HOOKS"][name]

    @app.route("/p/me/appoint")
    def appoint_page():
        _chat_or_404()
        role, bad = need_login()
        if bad:
            return bad
        with get_conn() as c:
            others = c.execute("SELECT id, name FROM roles WHERE id!=? ORDER BY name", (role["id"],)).fetchall()
        return page("appoint.html", role=role, mode="discover", others=others, rooms=[_room_row(r) for r in rooms_core.my_open(role["id"])],
                    reqs=rooms_core.pending_requests(role["id"]), today=season.current_day(),
                    chat_on=extensions.is_enabled("chatroom"), gc_on=extensions.is_enabled("groupchat"))

    @app.post("/p/me/appoint/new")
    def appoint_new():
        _chat_or_404()
        role, bad = need_login()
        if bad:
            return bad
        f = request.form
        kind = f.get("kind", "")
        ids = [int(x) for x in f.getlist("to") if x.isdigit()]
        targets = [t for t in (phonecodes.role_by_id(i) for i in ids) if t]
        place = f.get("place", "").strip()
        if kind not in ("约会", "电话", "微信") or not targets:
            flash("选一种约会，再选至少一位对象")
            return redirect(url_for("appoint_page"))
        if not extensions.is_enabled("groupchat" if kind == "微信" else "chatroom"):
            abort(404)
        why = _gate(role, "邀约", place)
        if why:
            flash(why)
            return redirect(url_for("appoint_page"))
        try:
            ok, text, rid = _hook("start")(kind, role, targets, f.get("time", "").strip(), "" if kind != "约会" else place)
        except Exception as e:  # noqa: BLE001
            ok, text, rid = False, f"发起失败：{str(e)[:60]}", 0
        flash(text if ok else f"❌ {text}")
        return redirect(url_for("room_page", rid=rid) if ok and rid else url_for("appoint_page"))

    @app.post("/p/me/appoint/join")
    def appoint_join():
        _chat_or_404()
        role, bad = need_login()
        if bad:
            return bad
        to = request.form.get("to", "")
        target = phonecodes.role_by_id(int(to)) if to.isdigit() else None
        if not target:
            flash("选一位想加入的人")
            return redirect(url_for("appoint_page"))
        cands = rooms_core.find_joinable(target["id"], request.form.get("start", "").strip())
        if not cands:
            flash(f"「{target['name']}」现在没有可以加入的约会（电话和微信不能加入）")
        elif len(cands) > 1:
            flash(f"「{target['name']}」有好几场，请填上开始时间（比如 14:00）：" + "；".join(f"{r['start_hm']} {r['kind']}" for r in cands))
        else:
            ok, why, qid = rooms_core.request_join(cands[0]["id"], role["id"])
            if ok:
                try:
                    _hook("join")(role, cands[0], qid)
                except Exception:  # noqa: BLE001
                    pass
                flash("申请已发出，等对方回复")
            else:
                flash(why)
        return redirect(url_for("appoint_page"))

    @app.post("/p/me/appoint/answer")
    def appoint_answer():
        _chat_or_404()
        role, bad = need_login()
        if bad:
            return bad
        qid = request.form.get("qid", type=int) or 0
        accept = request.form.get("accept") == "1"
        ok, why, q = rooms_core.answer_request(qid, role["id"], accept)
        if not ok:
            flash(why)
        else:
            try:
                _hook("answer")(role, q, accept)
            except Exception:  # noqa: BLE001
                pass
            flash("已同意" if accept else "已拒绝")
        return redirect(url_for("appoint_page"))

    def _room_open(role, rid):
        """房间页：照旧要开着聊天室 / 多人群聊 / 前置电话之一；QQ 号掉线期间，掉线的房间也能进（core/outage.py）。"""
        r = _room_for(role, rid)
        if not outage.room_down(r):
            _chat_or_404()
        return r

    @app.route("/p/me/room/<int:rid>")
    def room_page(rid):
        role, bad = need_login()
        if bad:
            return bad
        r = _room_open(role, rid)
        row = _room_row(r)
        row["status"] = r["status"]
        row["outage"] = outage.room_down(r)
        row["web"] = not r["pool_id"] or row["outage"]          # QQ 群房间平时只能看；掉线期间在这里写
        return page("room.html", role=role, mode="discover", room=row, lines=roomlog_lines(rid), me_id=role["id"], outage_rooms=[])   # 房间页自己有提示，不再挂顶部横幅

    def roomlog_lines(rid, after=0):
        from changri.core import roomlog
        out = []
        for ln in roomlog.lines(rid):
            if ln["id"] > after:
                out.append({"id": ln["id"], "name": ln["name"], "text": ln["text"], "role_id": ln["role_id"], "time": time.strftime("%H:%M", time.localtime(ln["ts"]))})
        return out

    @app.route("/p/me/room/<int:rid>/lines")
    def room_lines(rid):
        role, bad = need_login()
        if bad:
            return jsonify(ok=False), 401
        r = _room_open(role, rid)
        return jsonify(ok=True, status=r["status"], lines=roomlog_lines(rid, request.args.get("after", 0, type=int)))

    @app.post("/p/me/room/<int:rid>/say")
    def room_say(rid):
        role, bad = need_login()
        if bad:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        r = _room_open(role, rid)
        if r["status"] != "open":
            return _mjson(False, "这个房间已经结束了")
        if r["pool_id"] and not outage.room_down(r):
            return _mjson(False, "这个房间在 QQ 群里聊，请去群里发言")
        text = request.form.get("text", "").strip()[:2000]
        why = _gate(role, "聊天室", text)
        if why:
            return _mjson(False, why)
        from changri.core import roomlog
        ok, msg = roomlog.add_line(rid, role["id"], text)
        return _mjson(ok, "" if ok else msg)

    @app.post("/p/me/room/<int:rid>/end")
    def room_end(rid):
        role, bad = need_login()
        if bad:
            return bad
        r = _room_open(role, rid)
        if r["status"] == "open":
            try:
                _hook("end")(rid)
            except Exception as e:  # noqa: BLE001
                flash(f"结束失败：{str(e)[:60]}")
                return redirect(url_for("room_page", rid=rid))
            flash("已结束")
        return redirect(url_for("appoint_page"))
