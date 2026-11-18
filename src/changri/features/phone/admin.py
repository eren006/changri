"""设置平台 · 小手机。由 changri.admin.create_app 调用 register(app, login_required) 挂上路由（路由名不变，模板里的 url_for 照常用）。"""
import json
from flask import flash, redirect, render_template, request, url_for
from changri.core import extensions as ext_core, netcheck, phonecodes, roles as roles_core
from changri.core.db import current_bot, get_config, set_config


def _sync_ext(on: bool) -> None:
    """「扩展」页的小手机卡片跟着这里变。只在真的变了时才改（改了会提示「重启后生效」，因为「手机码」指令要重启才加载）。"""
    if ext_core.is_enabled("phone") != on:
        ext_core.set_enabled("phone", on)


def register(app, login_required):
    @app.route("/phone")
    @login_required
    def phone_page():
        from changri.phone.themes import PHONE_COPY, PHONE_THEMES
        from changri.core import phoneserver
        port = phoneserver.port() if get_config(phoneserver.PORT_KEY, "") or phoneserver.phone_bots() else phoneserver.suggest_port()   # 所有机器人共用；第一次开：建议一个没被占的
        try:
            last = json.loads(get_config("phone_net_last", "") or "null")
        except ValueError:
            last = None
        try:
            theme = json.loads(get_config("phone_theme_config", "") or "{}")
        except ValueError:
            theme = {}
        return render_template("phone.html", port=port, lights=netcheck.lights(port, last), last=last, codes=phonecodes.listing(),
                               base=get_config("phone_base_url", ""), enabled=get_config("phone_enabled", "0") == "1",
                               paused=get_config("phone_paused", "0") == "1", suggest=netcheck.default_base_url() if not get_config("phone_base_url", "") else "",
                               themes=PHONE_THEMES, copies=PHONE_COPY, theme=theme,
                               feats={k: ext_core.is_enabled(k) for k in ext_core.PHONE_FEATURES},
                               fcfg={"moment_daily_posts": get_config("moment_daily_posts", "20"),
                                     "game_daily_attempts": get_config("game_daily_attempts", "3")})

    @app.post("/phone/features")
    @login_required
    def phone_features():
        """小手机自己的功能：开关 + 设置。不算额外扩展，改了立刻生效。"""
        f = request.form
        for k in ext_core.PHONE_FEATURES:
            set_config(f"ext:{k}", "1" if f.get(f"feat_{k}") == "1" else "0")
        for k, lo in (("moment_daily_posts", 0), ("game_daily_attempts", 1)):
            v = f.get(k, "").strip()
            if v.isdigit() and int(v) >= lo:
                set_config(k, v)
        flash("小手机的功能设置已保存")
        return redirect(url_for("phone_page"))

    @app.post("/phone/open-as/<int:role_id>")
    @login_required
    def phone_open_as(role_id):
        """管理员以某个角色的身份打开小手机（代发短信、查看、排查问题）：发一枚 60 秒、只能用一次的令牌，跳到小手机端换成登录状态。"""
        import secrets as _sec
        import time as _time
        base = get_config("phone_base_url", "").rstrip("/")
        if get_config("phone_enabled", "0") != "1" or not base or not roles_core.get_role(role_id):
            flash("小手机还没开启，或者找不到这个角色")
            return redirect(url_for("phone_page"))
        phonecodes.code_for_role(role_id)
        tok = _sec.token_urlsafe(24)
        set_config(f"phone_as:{tok}", f"{role_id}|{int(_time.time()) + 60}")
        return redirect(f"{base}/p/as/{tok}?b={current_bot()}")          # b=：共用端口时让小手机知道是哪个机器人

    @app.post("/phone/open-admin")
    @login_required
    def phone_open_admin():
        """打开「管理员手机」：同样发一枚 60 秒、一次性的令牌，换成手机端的管理员登录（12 小时）。"""
        import secrets as _sec
        import time as _time
        base = get_config("phone_base_url", "").rstrip("/")
        if get_config("phone_enabled", "0") != "1" or not base:
            flash("小手机还没开启")
            return redirect(url_for("phone_page"))
        tok = _sec.token_urlsafe(24)
        set_config(f"phone_admin:{tok}", str(int(_time.time()) + 60))
        return redirect(f"{base}/p/admin/enter/{tok}?b={current_bot()}")

    @app.post("/phone/enable")
    @login_required
    def phone_enable():
        """开启小手机的初始化：装的人自己决定端口（默认 5092），确认后才真正开。"""
        from changri.core import phoneserver
        port = request.form.get("phone_port", "").strip()
        if bad := phoneserver.check_port(port):
            flash(bad)
            return redirect(url_for("phone_page"))
        base = request.form.get("phone_base_url", "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            flash("网址要以 http:// 或 https:// 开头")
            return redirect(url_for("phone_page"))
        phoneserver.set_port(int(port))
        ip = netcheck.public_ip_cached()
        set_config("phone_base_url", base or (f"http://{ip}:{port}" if ip else ""))
        set_config("phone_https", "1" if base.startswith("https://") else "0")
        set_config("phone_enabled", "1")
        _sync_ext(True)                                      # 扩展卡片和这里是同一个开关
        res = phoneserver.start()
        if "没能" in res:
            set_config("phone_enabled", "0")
            _sync_ext(False)
            flash(res)
        else:
            from changri.platform import service
            fw = service.open_firewall() or service.firewall_hint()
            flash(f"{res}。" + (f"{fw}。" if fw else "") + f"最后一步：到云服务商控制台的安全组放行 {port} 端口，再点下面的「检查玩家能不能打开」。")
            from changri.core import httpsproxy
            if err := httpsproxy.sync():
                flash(err)
        return redirect(url_for("phone_page"))

    @app.post("/phone/settings")
    @login_required
    def phone_save():
        f = request.form
        from changri.core import phoneserver
        port = f.get("phone_port", str(phoneserver.port())).strip()
        if bad := phoneserver.check_port(port):
            flash(bad)
            return redirect(url_for("phone_page"))
        base = f.get("phone_base_url", "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            flash("网址要以 http:// 或 https:// 开头")
            return redirect(url_for("phone_page"))
        old_port, old_on = str(phoneserver.port()), get_config("phone_enabled", "0")
        phoneserver.set_port(int(port))          # 所有机器人共用这个端口
        set_config("phone_base_url", base)
        set_config("phone_enabled", "1" if f.get("phone_enabled") else "0")
        _sync_ext(bool(f.get("phone_enabled")))                          # 扩展卡片和这里是同一个开关
        set_config("phone_paused", "1" if f.get("phone_paused") else "0")
        set_config("phone_https", "1" if base.startswith("https://") else "0")
        theme = {"visual": f.get("visual", "modern"), "copy": f.get("copy", "modern")}
        set_config("phone_theme_config", json.dumps(theme, ensure_ascii=False))
        msg = "已保存"
        if old_port != port or old_on != get_config("phone_enabled"):
            from changri.core import phoneserver
            msg += "。" + phoneserver.start()
            if get_config("phone_enabled") == "1":
                from changri.platform import service
                fw = service.open_firewall() or service.firewall_hint()
                msg += (f"。{fw}" if fw else "") + f"。别忘了到云服务商的安全组放行 {port} 端口，然后点下面的「检查玩家能不能打开」"
            from changri.core import httpsproxy
            if err := httpsproxy.sync():
                msg += "。" + err
        flash(msg)
        return redirect(url_for("phone_page"))

    @app.post("/phone/check")
    @login_required
    def phone_check():
        from changri.core import phoneserver
        res = netcheck.probe_outside(phoneserver.port())
        set_config("phone_net_last", json.dumps(res, ensure_ascii=False))
        return redirect(url_for("phone_page"))

    @app.post("/phone/codes/generate")
    @login_required
    def phone_generate():
        flash(f"已给 {phonecodes.generate_all()} 个角色生成激活码")
        return redirect(url_for("phone_page"))

    @app.post("/phone/codes/<int:role_id>/reset")
    @login_required
    def phone_reset(role_id):
        phonecodes.reset(role_id)
        flash("已重置，旧码立刻失效")
        nxt = request.form.get("next", "")
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("phone_page"))
