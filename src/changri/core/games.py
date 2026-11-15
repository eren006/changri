"""小手机小游戏：2048、贪吃蛇、打地鼠、跳一支、合成大西瓜、飞鸽传书。分数在前端算，score_per_sec / min_secs 只是挡手滑和乱刷的粗略上限。
每人每个游戏每天 3 次开局（本机日期 0 点刷新，次数 get_config game_daily_attempts），每个游戏一张排行榜（取每人最高分，同分先到的靠前）。"""
import time

from .db import get_config, get_conn, get_int

GAMES = {
    "2048": {"name": "2048", "icon": "🔢", "desc": "滑动合并方块，越大越好", "unit": "分", "score_per_sec": 600, "min_secs": 8},
    "snake": {"name": "贪吃蛇", "icon": "🐍", "desc": "吃得越多分越高，别撞墙", "unit": "分", "score_per_sec": 40, "min_secs": 5},
    "whack": {"name": "打地鼠", "icon": "🔨", "desc": "30 秒内敲中多少只", "unit": "只", "score_per_sec": 7, "min_secs": 29},
    "jump": {"name": "跳一跳", "icon": "🦘", "desc": "按住蓄力，松开跳跃", "unit": "分", "score_per_sec": 4, "min_secs": 0},
    "watermelon": {"name": "合成大西瓜", "icon": "🍉", "desc": "相同水果碰一碰，一路合成大西瓜", "unit": "分", "score_per_sec": 100, "min_secs": 0},
    "pigeon": {"name": "飞鸽传书", "icon": "🕊️", "desc": "点按扇翅膀，穿过缝隙，接住信件", "unit": "分", "score_per_sec": 3, "min_secs": 3},
}


def daily_attempts() -> int:
    return max(1, get_int("game_daily_attempts", 3))


def today() -> str:
    return time.strftime("%Y-%m-%d")


def attempts_left(game: str, role_id: int) -> int:
    with get_conn() as c:
        r = c.execute("SELECT n FROM game_attempts WHERE game=? AND role_id=? AND day=?", (game, role_id, today())).fetchone()
    return max(0, daily_attempts() - (r["n"] if r else 0))


def use_attempt(game: str, role_id: int) -> bool:
    """开一局扣一次；条件更新保证并发/连点也不会超过上限。"""
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO game_attempts(game, role_id, day, n) VALUES(?,?,?,0)", (game, role_id, today()))
        return c.execute("UPDATE game_attempts SET n=n+1 WHERE game=? AND role_id=? AND day=? AND n<?", (game, role_id, today(), daily_attempts())).rowcount == 1


def board(game: str, role_id: int, limit: int = 50) -> tuple[list[dict], int | None, int | None]:
    with get_conn() as c:
        rows = c.execute("SELECT role_id, label, score FROM game_scores WHERE game=? ORDER BY score DESC, updated_ts ASC LIMIT ?", (game, limit)).fetchall()
        out = [{"rank": i + 1, "label": r["label"], "score": r["score"], "me": r["role_id"] == role_id} for i, r in enumerate(rows)]
        mine = c.execute("SELECT score, updated_ts FROM game_scores WHERE game=? AND role_id=?", (game, role_id)).fetchone()
        rank = None
        if mine:
            rank = 1 + c.execute("SELECT COUNT(*) FROM game_scores WHERE game=? AND (score>? OR (score=? AND updated_ts<?))", (game, mine["score"], mine["score"], mine["updated_ts"])).fetchone()[0]
    return out, (mine["score"] if mine else None), rank


def check_score(game: str, score: int, elapsed: float) -> str:
    g = GAMES[game]
    if score < 0 or elapsed < g["min_secs"] or score > max(50, elapsed * g["score_per_sec"]):
        return "这个分数好像不太对，没有记入排行榜"
    return ""


def submit(game: str, role_id: int, label: str, score: int) -> tuple[bool, int | None, int | None]:
    """记分，返回 (是不是新纪录, 最高分, 排名)。"""
    now = int(time.time() * 1000)
    with get_conn() as c:
        cur = c.execute("SELECT score FROM game_scores WHERE game=? AND role_id=?", (game, role_id)).fetchone()
        new_best = not cur or score > cur["score"]
        if not cur:
            c.execute("INSERT INTO game_scores(game, role_id, label, score, updated_ts) VALUES(?,?,?,?,?)", (game, role_id, label, score, now))
        elif new_best:
            c.execute("UPDATE game_scores SET score=?, label=?, updated_ts=? WHERE game=? AND role_id=?", (score, label, now, game, role_id))
        else:
            c.execute("UPDATE game_scores SET label=? WHERE game=? AND role_id=?", (label, game, role_id))
    _, best, rank = board(game, role_id, 1)
    return new_best, best, rank


def summary(role_id: int) -> dict:
    out = {}
    for g in GAMES:
        _, best, rank = board(g, role_id, 1)
        out[g] = {"best": best, "rank": rank, "left": attempts_left(g, role_id)}
    return out


def clear_board(game: str | None = None) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM game_scores" + (" WHERE game=?" if game else ""), (game,) if game else ())
