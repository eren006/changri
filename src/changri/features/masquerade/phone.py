"""小手机 · 匿名舞会。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import masquerade, phonegate
from flask import jsonify, redirect, request, url_for


def register(app, ctx):
    _ext_or_404, _mjson, need_login, page = ctx._ext_or_404, ctx._mjson, ctx.need_login, ctx.page

    def _masq_user(write: bool = True, allow_npc: bool = False):
        """舞会写接口：扩展开着 + 登录 + 舞会开场（写操作再看暂停通讯）+ NPC 限制。返回 (role, None) 或 (None, 错误响应)。"""
        _ext_or_404("masquerade")
        role, bad = need_login()
        if bad:
            return None, (jsonify(ok=False, msg="手机登录已失效"), 401)
        if not masquerade.is_on():
            return None, _mjson(False, "舞会还没有开场")
        if not allow_npc and role["is_npc"]:
            return None, _mjson(False, "NPC 不能戴面具、邀请和回应，只能用真名在舞厅说话")
        if write and phonegate.comm_paused():
            return None, _mjson(False, phonegate.PAUSE_MSG)
        return role, None

    def _masq_why(role) -> str:
        return phonegate.PAUSE_MSG if phonegate.comm_paused() else ""

    @app.route("/p/me/ball")
    def masq_page():
        _ext_or_404("masquerade")
        role, bad = need_login()
        if bad:
            return bad
        masquerade.tick()
        if not masquerade.is_on():
            return redirect(url_for("inbox"))
        m = masquerade.music()
        m["url"] = url_for("static", filename="masquerade/" + m["file"])
        return page("masquerade.html", role=role, mode="masq", masq_state=masquerade.state(role["id"]), masq_music=m, open_dance=request.args.get("dance", type=int) or 0,
                    why=_masq_why(role), share_ro=False)

    @app.route("/p/me/ball/state")
    def masq_state():
        _ext_or_404("masquerade")
        role, bad = need_login()
        if bad:
            return jsonify(ok=False, msg="手机登录已失效"), 401
        masquerade.tick()
        if not masquerade.is_on():
            return jsonify(ok=False, msg="舞会还没有开场", closed=True), 400
        st = masquerade.state(role["id"])
        st["why"] = _masq_why(role)
        m = st["music"]
        m["url"] = url_for("static", filename="masquerade/" + m["file"])
        return jsonify(st)

    @app.post("/p/me/ball/mask")
    def masq_mask():
        role, err = _masq_user(write=False)
        if err:
            return err
        ok, msg, redraw = masquerade.pick_mask(role["id"], request.form.get("mask", "").strip())
        return _mjson(ok, msg, redraw=redraw)

    @app.post("/p/me/ball/nick")
    def masq_nick():
        role, err = _masq_user()
        if err:
            return err
        nick = request.form.get("nick", "").strip()
        if nick and phonegate.blocked_hit(role["name"], "舞会昵称", nick):
            return _mjson(False, "内容里有不能发送的词，请改一改再发")
        ok, msg = masquerade.set_nick(role["id"], nick)
        return _mjson(ok, msg)

    @app.post("/p/me/ball/say")
    def masq_say():
        role, err = _masq_user(allow_npc=True)
        if err:
            return err
        text = request.form.get("text", "").strip()
        if text and phonegate.blocked_hit(role["name"], "匿名舞会", text):
            return _mjson(False, "内容里有不能发送的词，请改一改再发")
        ok, msg = masquerade.say(role["id"], text)
        return _mjson(ok, msg)

    @app.post("/p/me/ball/react")
    def masq_react():
        role, err = _masq_user()
        if err:
            return err
        ok, msg = masquerade.react(role["id"], request.form.get("id", type=int) or 0, request.form.get("emoji", "").strip())
        return _mjson(ok, msg)

    @app.post("/p/me/ball/invite")
    def masq_invite():
        role, err = _masq_user()
        if err:
            return err
        ok, msg = masquerade.invite(role["id"], request.form.get("to", type=int) or 0)
        return _mjson(ok, msg)

    @app.post("/p/me/ball/invite/<int:iid>/answer")
    def masq_answer(iid: int):
        role, err = _masq_user()
        if err:
            return err
        ok, msg = masquerade.answer(role["id"], iid, request.form.get("accept") == "1")
        return _mjson(ok, msg)

    @app.post("/p/me/ball/dance/<int:iid>/say")
    def masq_dance_say(iid: int):
        role, err = _masq_user()
        if err:
            return err
        text = request.form.get("text", "").strip()
        if text and phonegate.blocked_hit(role["name"], "舞会私聊", text):
            return _mjson(False, "内容里有不能发送的词，请改一改再发")
        ok, msg = masquerade.dance_say(role["id"], iid, text)
        return _mjson(ok, msg)
