"""设置平台 · 匿名舞会。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import Response, abort, flash, redirect, render_template, request, url_for
from changri.core import masquerade as masq_core
from changri.core.db import set_config


def register(app, login_required):
    @app.route("/masquerade")
    @login_required
    def masq_admin_page():
        return render_template("masquerade.html", v=masq_core.admin_view())

    @app.get("/masquerade/export/<int:no>.txt")
    @login_required
    def masq_admin_export(no):
        text = masq_core.session_text(no)
        if text is None:
            abort(404)
        return Response(text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=masquerade_{no}.txt"})

    @app.post("/masquerade/op")
    @login_required
    def masq_admin_op():
        f = request.form
        act = f.get("action", "")
        msg = ""
        if act == "new":
            had = masq_core.new_session()
            masq_core.reopen()
            msg = "已新开一场" + ("，上一场的回放存进了往期" if had else "") + "，大家需要重新选面具"
        elif act in ("on", "off"):
            masq_core.set_on(act == "on")
            if act == "on" and not masq_core.has_data():
                masq_core.narrate("舞会开场了，请戴上面具入场。")
            msg = "匿名舞会已开场" if act == "on" else "匿名舞会已打烊（入口消失，记录保留）"
        elif act == "music":
            msg = "背景音乐已更新" if masq_core.set_music(f.get("music", "")) else "请选择列表中的背景音乐"
        elif act == "nick":
            msg = masq_core.admin_set_nick(f.get("role_id", type=int) or 0, f.get("nick", "")) or "昵称已改"
        elif act == "narrate":
            text = f.get("text", "").strip()
            if not text or len(text) > masq_core.NARR_MAX:
                msg = f"旁白要写 1~{masq_core.NARR_MAX} 个字"
            elif not masq_core.is_on():
                msg = "舞会还没有开场，旁白发不出去"
            else:
                masq_core.narrate(text)
                msg = "旁白已发到舞厅"
        elif act == "mute":
            if masq_core.is_on():
                masq_core.set_mute(True)
                masq_core.narrate("舞会进入禁言时间，请先安静地听一听音乐。")
                msg = "已全员禁言，直到你手动解除"
            else:
                msg = "舞会还没有开场"
        elif act == "unmute":
            masq_core.set_mute(False)
            if masq_core.is_on():
                masq_core.narrate("禁言解除了，可以说话了。")
            msg = "已解除禁言"
        elif act == "sched_add":
            msg = masq_core.add_sched(masq_core.parse_dt(f.get("at", "")), f.get("text", "")) or "已定时，到点自动发到舞厅（舞会开着时）"
        elif act == "sched_del":
            masq_core.del_sched(f.get("id", type=int) or 0)
            msg = "已取消这条定时旁白"
        elif act == "auto":
            o, c2 = masq_core.parse_dt(f.get("open", "")), masq_core.parse_dt(f.get("close", ""))
            if o < 0 or c2 < 0:
                msg = "时间格式不对"
            elif o and c2 and c2 <= o:
                msg = "打烊时间要晚于开场时间"
            else:
                set_config("masq_open_at", str(o))
                set_config("masq_close_at", str(c2))
                set_config("masq_open_mode", "new" if f.get("open_mode") == "new" else "continue")
                msg = "已设置自动开场 / 打烊时间" if (o or c2) else "已取消自动开场 / 打烊"
        elif act == "del_msg":
            masq_core.delete_msg(f.get("id", type=int) or 0)
            msg = "已删除"
        elif act == "del_invite":
            masq_core.delete_invite(f.get("id", type=int) or 0)
            msg = "已移除这段邀请（发起方名额退回，双方可重新邀请）"
        elif act == "del_dance_msg":
            masq_core.delete_dance_msg(f.get("id", type=int) or 0)
            msg = "已删除"
        flash(msg)
        return redirect(url_for("masq_admin_page"))
