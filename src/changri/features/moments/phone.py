"""小手机 · 朋友圈。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import moments, phonegate
from flask import abort, jsonify, make_response, request


def register(app, ctx):
    _ext_or_404, _mjson, need_login, page = ctx._ext_or_404, ctx._mjson, ctx.need_login, ctx.page

    def _moment_user():
        """朋友圈写接口：扩展开着 + 登录；返回 (role, None) 或 (None, 错误响应)。CSRF 由 guard 统一查（表单里要带 _csrf）。"""
        _ext_or_404("moments")
        role, bad = need_login()
        if bad:
            return None, (jsonify(ok=False, msg="手机登录已失效"), 401)
        return role, None

    def _moment_why(role) -> str:
        """现在不能发/赞/评论的原因，能就返回空串。"""
        return phonegate.PAUSE_MSG if phonegate.comm_paused() else ""

    @app.route("/p/me/moments")
    def moments_feed():
        _ext_or_404("moments")
        role, bad = need_login()
        if bad:
            return bad
        before = request.args.get("before", type=int) or None
        mine_only = request.args.get("mine") == "1"
        posts, more = moments.feed(role["id"], before=before, role_id=role["id"] if mine_only else None)
        return page("moments.html", role=role, posts=posts, more=more, mine_only=mine_only, why=_moment_why(role), mode="moments",
                    img_used=moments.used_images(role["id"]), img_quota=moments.image_quota(), tenant_full=moments.total_bytes() >= moments.total_limit(),
                    max_images=moments.MAX_IMAGES, max_text=moments.MAX_TEXT, max_comment=moments.MAX_COMMENT)

    @app.route("/p/me/moments/img/<int:image_id>")
    def moments_image(image_id: int):
        _ext_or_404("moments")
        role, bad = need_login()
        if bad:
            abort(404)
        data = moments.image(image_id, request.args.get("s") == "thumb")
        if not data:
            abort(404)
        resp = make_response(data)
        resp.headers["Content-Type"] = "image/jpeg"
        return resp

    @app.post("/p/me/moments/post")
    def moments_post():
        role, err = _moment_user()
        if err:
            return err
        why = _moment_why(role)
        if why:
            return _mjson(False, why)
        if request.content_length is None or request.content_length > 40 * 1024 * 1024:
            return _mjson(False, "图片太大了，少选几张再试")
        text = request.form.get("content", "").strip()
        if text and phonegate.blocked_hit(role["name"], "朋友圈", text):
            return _mjson(False, "内容里有不能发送的词，请改一改再发")
        files = [f.read(moments.MAX_FILE + 1) for f in request.files.getlist("images") if f]
        ok, msg, mid = moments.post(role["id"], text, files)
        return _mjson(ok, msg, id=mid)

    @app.post("/p/me/moments/<int:mid>/like")
    def moments_like(mid: int):
        role, err = _moment_user()
        if err:
            return err
        why = _moment_why(role)
        if why:
            return _mjson(False, why)
        ok, msg, liked = moments.toggle_like(mid, role["id"])
        return _mjson(ok, msg, liked=liked)

    @app.post("/p/me/moments/<int:mid>/comment")
    def moments_comment(mid: int):
        role, err = _moment_user()
        if err:
            return err
        why = _moment_why(role)
        text = request.form.get("content", "").strip()
        if not why and text and phonegate.blocked_hit(role["name"], "朋友圈评论", text):
            why = "内容里有不能发送的词，请改一改再发"
        if why:
            return _mjson(False, why)
        ok, msg = moments.comment(mid, role["id"], text, request.form.get("reply_to", "").strip())
        return _mjson(ok, msg)

    @app.post("/p/me/moments/<int:mid>/delete")
    def moments_delete(mid: int):
        role, err = _moment_user()
        if err:
            return err
        ok = moments.delete_post(mid, role["id"])
        return _mjson(ok, "" if ok else "只能删自己的朋友圈")

    @app.post("/p/me/moments/comment/<int:cid>/delete")
    def moments_comment_delete(cid: int):
        role, err = _moment_user()
        if err:
            return err
        ok = moments.delete_comment(cid, role["id"])
        return _mjson(ok, "" if ok else "不能删这条评论")

    @app.post("/p/me/moments/image/<int:image_id>/delete")
    def moments_image_delete(image_id: int):
        role, err = _moment_user()
        if err:
            return err
        ok = moments.delete_image(image_id, role["id"])
        return _mjson(ok, "" if ok else "只能删自己的图片", used=moments.used_images(role["id"]), quota=moments.image_quota())
