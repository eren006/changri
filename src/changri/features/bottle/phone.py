"""小手机 · 漂流瓶。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import bottle
from changri.core.db import get_int
from flask import abort, flash, redirect, request, url_for


def register(app, ctx):
    _ext_or_404, _fmt, _gate, _push, need_login, page = ctx._ext_or_404, ctx._fmt, ctx._gate, ctx._push, ctx.need_login, ctx.page

    def _bottle_why(role) -> str:
        cap, used = bottle.daily_limit(), bottle.day_count(role["id"])
        return "NPC 不能用漂流瓶。" if role["is_npc"] else (f"今天的漂流瓶次数用完了（{used}/{cap}）。" if cap > 0 and used >= cap else "")

    @app.route("/p/me/bottles")
    def bottle_page():
        _ext_or_404("bottle")
        role, bad = need_login()
        if bad:
            return bad
        threads = [{"id": b["id"], "state": "", "tag": "我扔的" if b["thrown"] else "我捡到的", "preview": b["msgs"][-1]["text"][:40] if b["msgs"] else "", "time": _fmt(b["msgs"][-1]["ts"]) if b["msgs"] else ""}
                   for b in bottle.my_bottles(role["id"])]
        return page("bottles.html", role=role, mode="bottles", threads=threads, why=_bottle_why(role), max_msg=get_int("msg_max_len", 200))

    @app.route("/p/me/bottles/<int:bid>")
    def bottle_thread(bid):
        _ext_or_404("bottle")
        role, bad = need_login()
        if bad:
            return bad
        b = next((x for x in bottle.my_bottles(role["id"]) if x["id"] == bid), None)
        if not b:
            abort(404)
        msgs = [{"mine": m["mine"], "text": m["text"], "pending": False, "time": _fmt(m["ts"])} for m in b["msgs"]]
        return page("bottle_thread.html", role=role, mode="bottle", bottle_id=bid, i_threw=b["thrown"], tag_known=True, msgs=msgs, failed=None,
                    why=_bottle_why(role), max_msg=get_int("msg_max_len", 200))

    @app.post("/p/me/bottles/throw")
    def bottle_throw():
        _ext_or_404("bottle")
        role, bad = need_login()
        if bad:
            return bad
        text = request.form.get("text", "").strip()
        tip = _bottle_why(role) or _gate(role, "漂流瓶", text)
        if not tip:
            ok, tip, catcher, new_id = bottle.throw(role, text)
            if ok:
                ctx.draft_done("bottle")
                if not _push(catcher, f"🍾 你捡到一个漂流瓶（编号{new_id}）：\n「{text}」\n\n想回信就发送：漂流瓶 {new_id} 你想说的话"):
                    tip += "（瓶子漂得有点远，对方那边暂时没收到。）"
        flash(tip)
        return redirect(url_for("bottle_page"))

    @app.post("/p/me/bottles/<int:bid>/reply")
    def bottle_reply(bid):
        _ext_or_404("bottle")
        role, bad = need_login()
        if bad:
            return bad
        text = request.form.get("text", "").strip()
        tip = _bottle_why(role) or _gate(role, "漂流瓶", text)
        if not tip:
            ok, tip, other, deliver = bottle.reply(role, bid, text)
            if ok:
                ctx.draft_done(f"bottle:{bid}")
            if ok and deliver:
                _push(other, f"🍾 漂流瓶（编号{bid}）有了新的回信：\n「{text}」\n\n想继续回信就发送：漂流瓶 {bid} 你想说的话")
        flash(tip)
        return redirect(url_for("bottle_thread", bid=bid))
