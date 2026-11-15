"""小手机 · 点歌台。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
from changri.core import announce, blocks, loopbridge, phonecodes, song as song_core
from changri.core.db import get_conn
from flask import flash, redirect, request, url_for


def register(app, ctx):
    _ext_or_404, _gate, need_login, page = ctx._ext_or_404, ctx._gate, ctx.need_login, ctx.page

    def _song_announce(req, sg):
        from changri.plugins import song as song_plugin
        return loopbridge.run(song_plugin.announce_song(req, sg), timeout=60)

    app.config["SONG_ANNOUNCE"] = _song_announce

    app.config["SONG_SEARCH"] = lambda kw, pf: song_core.search(kw, pf)

    app.config["SONG_BILI"] = lambda found: song_core.bili_lookup(found)

    @app.route("/p/me/songs")
    def songs_page():
        _ext_or_404("song")
        role, bad = need_login()
        if bad:
            return bad
        with get_conn() as c:
            others = c.execute("SELECT id, name FROM roles WHERE id!=? ORDER BY name", (role["id"],)).fetchall()
        cap = song_core.daily_limit()
        return page("songs.html", role=role, mode="discover", others=others, used=song_core.day_count(role["id"]), cap=cap,
                    recent=song_core.recent(15), has_ann=bool(announce.get_announce()))

    @app.post("/p/me/songs/pick")
    def songs_pick():
        _ext_or_404("song")
        role, bad = need_login()
        if bad:
            return bad
        f = request.form
        text = f.get("keyword", "").strip()[:80]
        msg = f.get("message", "").strip()[:60]
        to_id = f.get("to", type=int) or 0
        pf = f.get("platform") or None
        if pf not in ("qq", "163"):
            pf = None
        back = redirect(url_for("songs_page"))
        if not text:
            flash("写上歌名（可以加歌手），或者贴一个 B 站链接")
            return back
        why = _gate(role, "点歌", text, msg)
        if why:
            flash(why)
            return back
        why = song_core.limit_reached(role["id"])
        if why:
            flash(why.replace("🎵 ", ""))
            return back
        if not announce.get_announce():
            flash("管理员还没设置公告群，点歌暂时发不出去")
            return back
        to_name = ""
        if to_id:
            to = phonecodes.role_by_id(to_id)
            if not to or to["id"] == role["id"]:
                flash("选一个有效的对象，或者选「大家」")
                return back
            blk = blocks.get_block(to["id"], role["id"])
            if blk and not blk["silent"]:
                flash(f"{to['name']} 已拒绝你的联络")
                return back
            to_name = to["name"]
        req = {"platform": pf, "keyword": text, "to": to_name, "message": msg, "signer": ""}
        try:
            bili, _rest = song_core.find_bili(text)
            if bili:
                sg, source = app.config["SONG_BILI"](bili), "bili"
            else:
                sg, source = app.config["SONG_SEARCH"](text, pf), "search"
        except RuntimeError as e:
            flash(str(e))
            return back
        if not sg:
            flash(f"没搜到「{text}」，试试加上歌手")
            return back
        rid = song_core.record(role["id"], req, sg, source)
        err = app.config["SONG_ANNOUNCE"](req, sg)
        if err:
            song_core.delete(rid)
            flash("没能送到公告群，稍后再试")
            return back
        song_core.mark_announced(rid)
        flash(f"已把《{sg['title']}》点{('给 ' + to_name) if to_name else '给大家'}，公告群里能看到")
        return back
