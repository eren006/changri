"""小手机 · 小游戏。由 changri.phone.app.create_phone_app 调用 register(app, ctx) 挂上路由（路由名不变）。"""
import time
from changri.core import games, profiles
from flask import jsonify, redirect, request, session, url_for


def register(app, ctx):
    _ext_or_404, _mjson, need_login, page = ctx._ext_or_404, ctx._mjson, ctx.need_login, ctx.page

    @app.route("/p/me/games")
    @app.route("/p/me/games/<game>")
    def games_page(game=None):
        _ext_or_404("games")
        role, bad = need_login()
        if bad:
            return bad
        if game is not None and game not in games.GAMES:
            return redirect(url_for("games_page"))
        if game:
            session["game_start_" + game] = time.time()
        bd, best, rank = games.board(game, role["id"]) if game else ([], None, None)
        return page("games.html", role=role, mode="games", games=games.GAMES, game=game, summary=games.summary(role["id"]), board=bd, my_best=best, my_rank=rank,
                    attempts_total=games.daily_attempts(), attempts_left=games.attempts_left(game, role["id"]) if game else None)

    def _game_user(game: str):
        _ext_or_404("games")
        role, bad = need_login()
        if bad:
            return None, (jsonify(ok=False, msg="手机登录已失效"), 401)
        if game not in games.GAMES:
            return None, _mjson(False, "没有这个游戏")
        return role, None

    @app.post("/p/me/games/<game>/restart")
    def games_restart(game: str):
        role, err = _game_user(game)
        if err:
            return err
        if not games.use_attempt(game, role["id"]):
            session.pop("game_live_" + game, None)
            return _mjson(False, f"今天的 {games.daily_attempts()} 次机会已经用完啦，明天 0 点刷新", left=0)
        session["game_live_" + game] = True
        session["game_start_" + game] = time.time()
        return _mjson(True, left=games.attempts_left(game, role["id"]))

    @app.post("/p/me/games/<game>/score")
    def games_score(game: str):
        role, err = _game_user(game)
        if err:
            return err
        score = request.form.get("score", type=int)
        started = session.get("game_start_" + game)
        if score is None:
            return _mjson(False, "分数不对")
        if not started or not session.get("game_live_" + game):
            return _mjson(False, "请重新进入游戏再提交")
        why = games.check_score(game, score, time.time() - started)
        if why:
            return _mjson(False, why)
        session["game_start_" + game] = time.time()
        session.pop("game_live_" + game, None)           # 这一局已提交，再玩要重新开局（重新扣次数）
        nick = profiles.nick_map().get(role["name"])
        new_best, best, rank = games.submit(game, role["id"], f"{nick}（{role['name']}）" if nick else role["name"], score)
        return _mjson(True, new_best=new_best, best=best, rank=rank)
