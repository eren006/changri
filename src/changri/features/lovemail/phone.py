"""小手机 · 心动信箱。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import lovemail, roles as roles_core
from changri.core.db import get_conn, get_int
from flask import flash, redirect, request, session, url_for


def register(app, ctx):
    _ext_or_404, _gate, need_login, page = ctx._ext_or_404, ctx._gate, ctx.need_login, ctx.page

    @app.route("/p/me/lovemail")
    def lovemail_page():
        _ext_or_404("lovemail")
        role, bad = need_login()
        if bad:
            return bad
        view = request.args.get("view") if request.args.get("view") in ("sent", "write") else "inbox"
        if view == "inbox" and not ctx.is_admin():
            from changri.core import phoneunread
            phoneunread.mark_read(role["id"], "lovemail")
        recv, sent = lovemail.history(role["id"])
        recv = [{**m, "game_day": m["day"]} for m in recv]
        sent = [{**m, "game_day": m["day"], "to": m["to_name"]} for m in sent]
        pend = [{"id": m["id"], "to": m["to_name"], "game_day": m["day"], "content": m["content"], "signature": m["signature"]} for m in lovemail.my_pool(role["id"])]
        lm = {**lovemail.state(role["id"]), "ready": True, "pending": pend}
        with get_conn() as c:
            contacts = [r["name"] for r in c.execute("SELECT name FROM roles WHERE id!=? ORDER BY name", (role["id"],))]
        return page("lovemail.html", role=role, mode="lovemail", view=view, lm=lm, recv=recv, sent=sent, contacts=contacts, preset_to=request.args.get("to", ""),
                    draft=session.pop("lm_draft", None), max_len=get_int("lovemail_max_len", 500), sig_max=lovemail.SIGNATURE_MAX)

    @app.post("/p/me/lovemail")
    def lovemail_post():
        _ext_or_404("lovemail")
        role, bad = need_login()
        if bad:
            return bad
        f = request.form
        if f.get("action") == "revoke":
            ok, msg = lovemail.revoke_id(role["id"], int(f["mail"]) if f.get("mail", "").isdigit() else 0)
            flash(msg)
            return redirect(url_for("lovemail_page", view="sent"))
        to, content, sig = f.get("to", ""), f.get("content", "").strip(), (f.get("signature", "").strip() or "匿名")
        target = roles_core.get_role_by_name(to) if to else None
        msg = _gate(role, "心动信", content, sig)
        if not msg and (not target or target["id"] == role["id"]):
            msg = "请选一个收信人（不能是自己）。"
        if not msg:
            ok, msg, left = lovemail.add_mail(role["id"], target["id"], sig, content)
            if ok:
                flash(f"💌 已投进「{target['name']}」的信箱，今天还剩 {left} 封。派送前可以在「寄出的」里撤回。")
                return redirect(url_for("lovemail_page", view="sent"))
        flash(msg)
        session["lm_draft"] = {"to": to[:20], "content": content[:2000], "signature": f.get("signature", "")[:20]}
        return redirect(url_for("lovemail_page", view="write"))
