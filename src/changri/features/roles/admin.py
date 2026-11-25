"""设置平台 · 角色与群。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
from flask import flash, redirect, request, url_for
from changri.core import roles as roles_core


def deleted_text(res: dict | None) -> str:
    """后台删角色后的提示：删了哪些东西"""
    if not res:
        return "这个角色已经不在了。"
    extra = []
    if res["rooms"]:
        extra.append(f"TA 发起的 {res['rooms']} 场约会/电话整场删除")
    if res["joined"]:
        extra.append(f"从 {res['joined']} 场别人发起的房间里移除")
    if res["qqs"]:
        extra.append(f"解除 {res['qqs']} 个绑定的 QQ 号")
    return f"已彻底删除角色「{res['name']}」：群、短信、关系线、背包、朋友圈等全部清掉" + ("；" + "，".join(extra) if extra else "") + "。玩家可以重新「创建新角色」。"


def register(app, login_required):
    @app.route("/roles")
    @login_required
    def roles_page():
        return redirect(url_for("god_page"))                      # 角色与群并进「角色」页（原上帝模式）

    @app.post("/roles/<int:role_id>/slot")
    @login_required
    def add_slot(role_id):
        ok, res = roles_core.add_slot(role_id, request.form.get("real_gid", "").strip())
        if not ok:
            flash(res, "error")
            return redirect(url_for("god_page", role=role_id))
        flash("群位已登记，请到那个群里发认群码")
        return redirect(url_for("god_page", role=role_id, code=res))

    @app.post("/roles/<int:role_id>/delete")
    @login_required
    def delete_role(role_id):
        flash(deleted_text(roles_core.delete_role(role_id)))
        return redirect(request.form.get("next") if request.form.get("next", "").startswith("/") else url_for("god_page"))

    @app.template_filter("ts")
    def _ts_filter(v):
        import time as _t
        return _t.strftime("%m-%d %H:%M", _t.localtime(v)) if v else "—"
