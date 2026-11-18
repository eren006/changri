"""复盘网页的路由（挂在小手机服务上，不用登录）：/v/<token> 主页、/day/<D几>、/room/<R号>（.txt 下载）、/events、/role/<名字>。
内容来自季度档案，收尾以后照样能看；逻辑在 core/seasonview.py。"""
from urllib.parse import quote

from changri.core import seasonview as sv
from flask import abort, make_response, render_template


def register(app, ctx):  # noqa: ARG001

    def _season(tok) -> int:
        sid = sv.season_for(tok)
        if sid is None:
            abort(404)
        return sid

    def _page(tpl, tok, sid, **kw):
        resp = make_response(render_template(tpl, name=sv.season_name(sid), root=f"/v/{tok}", fmt=sv.fmt_ts, **kw))
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Robots-Tag"] = "noindex"
        return resp

    @app.route("/v/<tok>")
    def sv_home(tok):
        sid = _season(tok)
        evts = sv.load(sid)
        return _page("sv/home.html", tok, sid, nav="rooms", days=sv.days(evts), t=sv.totals(evts))

    @app.route("/v/<tok>/day/<label>")
    def sv_day(tok, label):
        sid = _season(tok)
        evts = [e for e in sv.load(sid) if e["day_label"] == label]
        rs = [e for e in evts if e["type"] == "room"]
        kinds: dict[str, int] = {}
        for r in rs:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        return _page("sv/day.html", tok, sid, nav="rooms", label=label, rooms=rs, kinds=sorted(kinds.items()),
                     evs=[e for e in evts if e["type"] != "room"])

    @app.route("/v/<tok>/room/<int:rid>")
    def sv_room(tok, rid):
        sid = _season(tok)
        s = sv.room(sid, rid)
        if not s:
            abort(404)
        return _page("sv/room.html", tok, sid, nav="rooms", s=s)

    @app.route("/v/<tok>/room/<int:rid>.txt")
    def sv_room_txt(tok, rid):
        sid = _season(tok)
        text = sv.room_text(sid, rid)
        if not text:
            abort(404)
        resp = make_response(text.encode("utf-8"))
        resp.headers["Content-Type"] = "text/plain; charset=utf-8"
        resp.headers["Content-Disposition"] = f"attachment; filename=\"R{rid}.txt\"; filename*=UTF-8''{quote(f'{sv.season_name(sid)}_R{rid}.txt')}"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/v/<tok>/events")
    def sv_events(tok):
        sid = _season(tok)
        evs = [e for e in sv.load(sid) if e["type"] != "room"][::-1]          # 新的在上面，照 RP 端
        counts = {k: sum(1 for e in evs if e["type"] == k) for k in ("lovemail", "sms", "gift")}
        return _page("sv/events.html", tok, sid, nav="events", evs=evs, counts=counts)

    @app.route("/v/<tok>/role/<who>")
    def sv_role(tok, who):
        sid = _season(tok)
        rs = [e for e in sv.load(sid) if e["type"] == "room" and who in e["who"]][::-1]
        return _page("sv/role.html", tok, sid, nav="rooms", who=who, rooms=rs)
