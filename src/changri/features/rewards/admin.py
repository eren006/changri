"""设置平台 · 结戏奖励。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
import logging
from flask import flash, redirect, render_template, request, url_for
from changri.core import loopbridge, rewards as rewards_core, rooms as rooms_core, rpg as rpg_core


def register(app, login_required):
    def _reward_rows():
        f = request.form
        fixed, err = rewards_core.rows_fixed(f.getlist("fx_ref"), f.getlist("fx_n"), f.getlist("fx_p"))
        if err:
            return None, None, err
        pool, err = rewards_core.rows_pool(f.getlist("pl_ref"), f.getlist("pl_n"), f.getlist("pl_s"), f.getlist("pl_p"))
        return fixed, pool, err

    @app.route("/rewards")
    @login_required
    def rewards_page():
        rid = request.args.get("room", type=int)
        rooms_ = rewards_core.recent_rooms()
        sel = rid or (rooms_[0]["id"] if rooms_ else None)
        return render_template("rewards.html", rules=rewards_core.rules(), rewards=rewards_core, kinds=list(rooms_core.KINDS),
                               items=[x for x in rpg_core.items()], attrs=rpg_core.attr_defs(), rooms=rooms_, sel=sel,
                               status=rewards_core.room_status(sel) if sel else [])

    @app.post("/rewards/save")
    @login_required
    def rewards_save():
        f = request.form
        fixed, pool, err = _reward_rows()
        ok = not err
        if ok:
            ok, err = rewards_core.save_rule(f.get("id", type=int), f.get("name", ""), f.getlist("kinds"), rewards_core.parse_conds(f), fixed, pool,
                                             f.get("pool_draws", type=int) or 1, f.get("enabled") == "on")
        flash("规则已保存" if ok else err)
        return redirect(url_for("rewards_page"))

    @app.post("/rewards/<int:rule_id>/delete")
    @login_required
    def rewards_delete(rule_id):
        rewards_core.delete_rule(rule_id)
        flash("规则已删除（已发出的奖励不受影响）")
        return redirect(url_for("rewards_page"))

    @app.post("/rewards/room/<int:room_id>/reissue")
    @login_required
    def rewards_reissue(room_id):
        from changri.core.qqsend import make_send
        from changri.core import messaging
        notes = rewards_core.settle(room_id)
        for role_id, items in notes.items():
            try:
                loopbridge.run(messaging.push_to_role(role_id, rewards_core.message(room_id, items), make_send(), True))
            except Exception as e:  # noqa: BLE001
                logging.warning("结戏奖励通知失败：%s", e)
        flash(f"R{room_id} 补发完成：{len(notes)} 人收到了新奖励" if notes else f"R{room_id} 没有需要补发的（都已发过，或没人满足条件）")
        return redirect(url_for("rewards_page", room=room_id))

    @app.post("/rewards/room/<int:room_id>/adjust")
    @login_required
    def rewards_adjust(room_id):
        f = request.form
        role_id = f.get("role_id", type=int) or 0
        if f.get("reset"):
            rewards_core.clear_adjust(room_id, role_id)
            flash("已恢复成实际记录的数据")
        else:
            ok, info = rewards_core.adjust(room_id, role_id, f.get("segs", type=int) or 0, f.get("chars", type=int) or 0)
            flash("已调整，记得点「补发奖励」" if ok else info)
        return redirect(url_for("rewards_page", room=room_id))
