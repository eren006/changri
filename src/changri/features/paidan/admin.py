"""设置平台 · 排单（个性化模块）。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由。
规则都在 core/paidan.py，这里只管收表单、显示页面。"""
import re
import time

from flask import abort, flash, redirect, render_template, request, url_for

from changri.core import paidan as pd
from changri.core.db import current_bot, get_config, set_config

HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _int(name: str, default: int = 0) -> int:
    try:
        return int(request.form.get(name, request.args.get(name, "")) or default)
    except ValueError:
        return default


def _send_all(text_for) -> str:
    """网页上触发的群发（暂停通知）：交给机器人主循环发。"""
    try:
        from changri.core import loopbridge
        from changri.core.qqsend import make_send
        ok, bad = loopbridge.run(pd.broadcast(make_send(), current_bot(), text_for), timeout=60)
    except Exception as e:  # noqa: BLE001 机器人不在线也不能让页面报错
        return f"通知没发出去（{str(e)[:60]}）"
    return f"已通知 {ok} 个群" + (f"，{bad} 个群没发出去" if bad else "")


def _send_one(gid: str, text: str) -> str:
    """网页上触发、发给一个群的通知（订单完成、取消）。没绑定群就不发。返回给 owner 看的结果。"""
    if not gid:
        return "（客户群没绑定，没通知）"
    try:
        from changri.core import loopbridge
        from changri.core.qqsend import make_send
        loopbridge.run(make_send()(current_bot(), gid, text), timeout=20)
    except Exception as e:  # noqa: BLE001
        return f"（通知没发出去：{str(e)[:60]}）"
    return "，已通知客户"


def register(app, login_required):
    @app.template_filter("pd_ts")
    def _pd_ts(ts):
        if not ts:
            return ""
        t = time.localtime(int(ts))
        return f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}"

    @app.template_filter("pd_md")
    def _pd_md(day):
        return pd._md(day) if day and pd.valid_date(day) else ""

    def render(tpl, tab, **kw):
        return render_template(tpl, tab=tab, enabled=pd.enabled(), who=pd.who, today=pd.today(), **kw)

    # ---------- 总览 ----------
    @app.route("/paidan")
    @login_required
    def paidan_page():
        return render("paidan.html", "home", ov=pd.overview(), biz_count=len(pd.businesses()))

    # ---------- 业务 ----------
    @app.route("/paidan/biz")
    @login_required
    def paidan_biz():
        rows = [{"b": b, "missing_qq": pd.template_missing_qq(b["template"]), "code": pd.bind_code_of("work", b["id"]),
                 "fmt": pd.order_format(b)} for b in pd.businesses()]
        return render("paidan_biz.html", "biz", rows=rows)

    @app.post("/paidan/biz/save")
    @login_required
    def paidan_biz_save():
        bid = _int("id") or None
        new_id, err = pd.save_business(request.form.get("letter", ""), request.form.get("name", ""), _int("days", 7),
                                       _int("slots", 1), request.form.get("template", ""), bid)
        if err:
            flash("没保存：" + err, "error")
        else:
            b = pd.business(new_id)
            flash(f"已保存「{b['name']}」" + ("。记得在下单格式里加上「QQ号：」这一栏（没写会自动补在最后）" if pd.template_missing_qq(b["template"]) else ""))
        return redirect(url_for("paidan_biz"))

    @app.post("/paidan/biz/<int:bid>/sync")
    @login_required
    def paidan_biz_sync(bid):
        flash(f"已同步给 {pd.sync_default_slots(bid)} 个没单独改过单口的客户")
        return redirect(url_for("paidan_biz"))

    @app.post("/paidan/biz/<int:bid>/code")
    @login_required
    def paidan_biz_code(bid):
        if not pd.business(bid):
            abort(404)
        flash(f"工作群绑定码：{pd.new_bind_code('work', bid)}。在这个业务的工作群里发「绑定 绑定码」")
        return redirect(url_for("paidan_biz"))

    # ---------- 客户 ----------
    @app.route("/paidan/clients")
    @login_required
    def paidan_clients():
        day = pd.today()
        rows = []
        for cl in pd.clients():
            left = {b["letter"]: pd.quota_left(cl["id"], b["id"], day) for b in pd.businesses()}
            rows.append({"c": cl, "left": left, "open": len(pd.orders("open", cid=cl["id"])), "short": len(pd.shortfall_orders(cl["id"]))})
        return render("paidan_clients.html", "clients", rows=[r for r in rows if not r["c"]["archived"]],
                      done=[r for r in rows if r["c"]["archived"]], bizs=pd.businesses())

    @app.post("/paidan/clients/new")
    @login_required
    def paidan_client_new():
        slots = {b["id"]: _int(f"slots_{b['id']}", b["default_slots"]) for b in pd.businesses()}
        cid, err = pd.create_client(request.form.get("name", ""), slots, request.form.get("note", ""))
        if err:
            flash("没保存：" + err, "error")
            return redirect(url_for("paidan_clients"))
        for fn, val in ((pd.set_client_qq, request.form.get("qq", "")), (pd.set_client_gid, request.form.get("real_gid", ""))):
            e = fn(cid, val)
            if e:
                flash(e, "error")
        code = pd.new_bind_code("client", cid)
        flash(f"已建客户。绑定码：{code}，让客户本人在他的群里发「绑定 {code}」")
        return redirect(url_for("paidan_client", cid=cid))

    @app.route("/paidan/clients/<int:cid>")
    @login_required
    def paidan_client(cid):
        cl = pd.client(cid)
        if not cl:
            abort(404)
        day = pd.today()
        bizs = pd.businesses()
        quotas = [{"q": q, "b": pd.business(q["business_id"]), "state": pd.quota_state(q, day), "ext": pd.quota_extends(q["id"])}
                  for q in pd.quotas_of(cid)]
        slots = [{"b": b, "n": pd.slots_of(cid, b["id"]), "open": pd.open_count(cid, b["id"]), "left": pd.quota_left(cid, b["id"], day)} for b in bizs]
        return render("paidan_client.html", "clients", c=cl, quotas=quotas, slots=slots, bizs=bizs,
                      orders=pd.orders(cid=cid), short=pd.shortfall_orders(cid), code=pd.bind_code_of("client", cid))

    @app.post("/paidan/clients/<int:cid>/save")
    @login_required
    def paidan_client_save(cid):
        if not pd.client(cid):
            abort(404)
        errs = [pd.rename_client(cid, request.form.get("name", ""), request.form.get("note", "")),
                pd.set_client_qq(cid, request.form.get("qq", "")), pd.set_client_gid(cid, request.form.get("real_gid", ""))]
        for b in pd.businesses():
            n = _int(f"slots_{b['id']}", 0)
            if n and n != pd.slots_of(cid, b["id"]):
                errs.append(pd.set_slots(cid, b["id"], n))
        errs = [e for e in errs if e]
        flash("没全部保存：" + "；".join(errs) if errs else "已保存")
        return redirect(url_for("paidan_client", cid=cid))

    @app.post("/paidan/clients/<int:cid>/quota")
    @login_required
    def paidan_quota_add(cid):
        _, err = pd.add_quota(cid, _int("bid"), _int("total"), request.form.get("expires", ""))
        flash("没录上：" + err if err else "已录额度")
        return redirect(url_for("paidan_client", cid=cid))

    @app.post("/paidan/quota/<int:qid>/extend")
    @login_required
    def paidan_quota_extend(qid):
        q = pd.quota(qid)
        if not q:
            abort(404)
        err = pd.extend_quota(qid, request.form.get("expires", ""))
        flash("没延期：" + err if err else "已延期")
        return redirect(url_for("paidan_client", cid=q["client_id"]))

    @app.post("/paidan/clients/<int:cid>/code")
    @login_required
    def paidan_client_code(cid):
        if not pd.client(cid):
            abort(404)
        code = pd.new_bind_code("client", cid)
        flash(f"新的绑定码：{code}，让客户本人在他的群里发「绑定 {code}」（旧码作废）")
        return redirect(url_for("paidan_client", cid=cid))

    @app.post("/paidan/clients/<int:cid>/archive")
    @login_required
    def paidan_client_archive(cid):
        if not pd.client(cid):
            abort(404)
        on = request.form.get("on") == "1"
        err = pd.set_archived(cid, on)
        flash(err or ("已标记已完结：不能再下单、不再提醒，数据都留着" if on else "已恢复"))
        return redirect(url_for("paidan_client", cid=cid))

    @app.post("/paidan/clients/<int:cid>/unbind")
    @login_required
    def paidan_client_unbind(cid):
        pd.unbind_client(cid)
        flash("已解绑。重新生成绑定码，让客户本人再绑一次")
        return redirect(url_for("paidan_client", cid=cid))

    # ---------- 订单 ----------
    @app.route("/paidan/orders")
    @login_required
    def paidan_orders():
        st = request.args.get("st", "open")
        bid = _int("bid") or None
        rows = pd.orders(st if st in ("open", "done", "cancelled") else None, bid)
        return render("paidan_orders.html", "orders", rows=rows, st=st, bid=bid, bizs=pd.businesses(),
                      biz={b["id"]: b for b in pd.businesses()}, clients=pd.clients())

    @app.post("/paidan/orders/new")
    @login_required
    def paidan_order_new():
        """owner 手动补录：不管暂停、单口、额度。客户留空 = 散单。"""
        cid = _int("cid") or None
        o, err = pd.place_order(_int("bid"), request.form.get("content", ""), cid=cid, orderer_name=request.form.get("orderer", ""),
                                qq=request.form.get("qq", ""), manual=True)
        if err:
            flash("没补录：" + err, "error")
            return redirect(url_for("paidan_orders"))
        flash(f"已补录 {o['no']}")
        return redirect(url_for("paidan_order", oid=o["id"]))

    @app.route("/paidan/orders/<int:oid>")
    @login_required
    def paidan_order(oid):
        o = pd.order(oid)
        if not o:
            abort(404)
        preview = None
        if o["status"] == "open" and request.args.get("n") is not None:
            n, q = _int("n", 1), _int("q") or None
            plan, short, err = pd.plan_charge(oid, n, q)
            preview = {"n": n, "q": q or "", "plan": [(pd.quota(qid), k) for qid, k in plan], "short": short, "err": err}
        usable = pd.usable_quotas(o["client_id"], o["business_id"]) if o["client_id"] is not None else []
        charged = [(pd.quota(ch["quota_id"]), ch["n"]) for ch in pd.charges(oid)]
        return render("paidan_order.html", "orders", o=o, b=pd.business(o["business_id"]), usable=usable,
                      preview=preview, charged=charged)

    @app.post("/paidan/orders/<int:oid>/complete")
    @login_required
    def paidan_order_complete(oid):
        o = pd.order(oid)
        if not o:
            abort(404)
        first = o["status"] == "open"
        fn = pd.complete_order if first else pd.recharge
        res, err = fn(oid, _int("n", 1), _int("q") or None)
        if err:
            flash("没扣：" + err, "error")
            return redirect(url_for("paidan_order", oid=oid))
        done = pd.order(oid)
        sent = _send_one(pd.order_target(done)[0], pd.done_notice(done)) if first else ""
        if res["shortfall"]:
            flash(f"已完成，额度不够，扣到 0，还差 {res['shortfall']} 张，请跟客户沟通" + sent)
        else:
            flash(("已完成" + sent) if first else "已改好扣法")
        return redirect(url_for("paidan_order", oid=oid))

    @app.post("/paidan/orders/<int:oid>/edit")
    @login_required
    def paidan_order_edit(oid):
        err = pd.edit_content(oid, request.form.get("content", ""))
        flash(err or "已改好下单内容", "error" if err else "message")
        return redirect(url_for("paidan_order", oid=oid))

    @app.post("/paidan/orders/<int:oid>/unlock")
    @login_required
    def paidan_order_unlock(oid):
        err = pd.unlock_uploads(oid)
        flash(err or "已打开传图，客户可以继续补图，补完让他再发「已上传所有图」", "error" if err else "message")
        return redirect(url_for("paidan_order", oid=oid))

    @app.post("/paidan/orders/<int:oid>/cancel")
    @login_required
    def paidan_order_cancel(oid):
        err = pd.cancel_order(oid)
        if err:
            flash(err, "error")
        else:
            o = pd.order(oid)
            flash("已取消" + _send_one(pd.order_target(o)[0], pd.cancel_notice(o)))
        return redirect(url_for("paidan_order", oid=oid))

    # ---------- 散单 ----------
    @app.route("/paidan/sandan")
    @login_required
    def paidan_sandan():
        day = pd.today()
        bizs = pd.businesses()
        biz = {b["id"]: b for b in bizs}
        rows = [{"r": r, "state": pd.code_state(r, day), "b": biz.get(r["business_id"]), "o": pd.order(r["order_id"]) if r["order_id"] else None}
                for r in pd.codes()]
        fresh = request.args.get("fresh", "")
        return render("paidan_sandan.html", "sandan", rows=rows, bizs=bizs, fresh=[c for c in fresh.split(",") if c],
                      group=get_config("pd_sandan_group", ""), real_gid=get_config("pd_sandan_real_gid", ""),
                      code=pd.bind_code_of("sandan"))

    @app.post("/paidan/sandan/gen")
    @login_required
    def paidan_codes_gen():
        codes, err = pd.gen_codes(_int("bid"), _int("n", 1), request.form.get("expires", ""))
        if err:
            flash("没生成：" + err, "error")
            return redirect(url_for("paidan_sandan"))
        flash(f"已生成 {len(codes)} 个兑换码")
        return redirect(url_for("paidan_sandan", fresh=",".join(codes)))

    @app.post("/paidan/sandan/settings")
    @login_required
    def paidan_sandan_settings():
        err = pd.set_sandan_real_gid(request.form.get("real_gid", ""))
        flash(err or "已保存", "error" if err else "message")
        return redirect(url_for("paidan_sandan"))

    @app.post("/paidan/sandan/code")
    @login_required
    def paidan_sandan_code():
        flash(f"散单群绑定码：{pd.new_bind_code('sandan')}。在散单群里发「绑定 绑定码」")
        return redirect(url_for("paidan_sandan"))

    # ---------- 暂停 ----------
    @app.route("/paidan/pause")
    @login_required
    def paidan_pause():
        day = pd.today()
        return render("paidan_pause.html", "pause", upcoming=pd.pauses(day), history=[p for p in pd.pauses() if p["day"] < day][:30],
                      paused=pd.is_paused(day))

    @app.post("/paidan/pause/today")
    @login_required
    def paidan_pause_today():
        day = pd.today()
        err = pd.add_pause(day, day)
        if err:
            flash(err, "error")
        else:
            set_config("pd_pause_notified", day)          # 定时任务就不再发一遍
            flash("今天已暂停，期限都顺延了 1 天。" + _send_all(lambda cid: pd.pause_notice(cid, day)))
        return redirect(url_for("paidan_pause"))

    @app.post("/paidan/pause/add")
    @login_required
    def paidan_pause_add():
        day = request.form.get("day", "")
        if day == pd.today():
            return paidan_pause_today()
        err = pd.add_pause(day)
        if err:
            flash(err, "error")
        else:
            flash(f"已设 {pd._md(day)} 暂停，到那天 0 点生效。" + _send_all(lambda cid: pd.pause_preview([day])))
        return redirect(url_for("paidan_pause"))

    @app.post("/paidan/pause/cancel")
    @login_required
    def paidan_pause_cancel():
        day = request.form.get("day", "")
        err = pd.cancel_pause(day)
        if err:
            flash(err, "error")
        else:
            flash(f"已取消 {pd._md(day)} 的暂停。" + _send_all(lambda cid: f"📢 {pd._md(day)} 恢复正常接单～"))
        return redirect(url_for("paidan_pause"))

    # ---------- 设置 ----------
    @app.route("/paidan/settings")
    @login_required
    def paidan_settings():
        return render("paidan_settings.html", "settings", owner=pd.owner_openid(), code=pd.bind_code_of("owner"),
                      remind=get_config("pd_remind_hm", "12:00"), pause_hm=get_config("pd_pause_hm", "00:00"),
                      dm_fail=get_config("pd_dm_fail", ""))

    @app.post("/paidan/settings")
    @login_required
    def paidan_settings_save():
        remind, pause_hm = request.form.get("remind", ""), request.form.get("pause_hm", "")
        if not (HM.match(remind) and HM.match(pause_hm)):
            flash("时间要写成 12:00 这样", "error")
        else:
            set_config("pd_remind_hm", remind)
            set_config("pd_pause_hm", pause_hm)
            flash("已保存")
        return redirect(url_for("paidan_settings"))

    @app.post("/paidan/settings/owner")
    @login_required
    def paidan_owner_code():
        flash(f"owner 绑定码：{pd.new_bind_code('owner')}。owner 本人在机器人在的群里发「绑定 绑定码」")
        return redirect(url_for("paidan_settings"))
