"""一键通知（只在后台用）：给「很久没人说话」或「开了很久」的进行中房间群发一条提醒。
QQ 号管着的房间群要靠监听的 QQ 号发话（还能 @ 绑定了 QQ 的参与者），没连上就发不了；官方机器人认过的群直接发。"""
import time

from . import pool, rooms, roles
from .db import get_conn


def candidates(hours: float, mode: str) -> list[dict]:
    """mode=idle：最近一条发言（没有发言就按开房时间）距今超过 hours 小时；mode=open：开房超过 hours 小时。"""
    now = time.time()
    out = []
    with get_conn() as c:
        for r in c.execute("SELECT * FROM rooms WHERE status='open' AND pool_id IS NOT NULL ORDER BY id").fetchall():
            last = c.execute("SELECT MAX(ts) FROM room_lines WHERE room_id=?", (r["id"],)).fetchone()[0]
            ref = r["created_ts"] if mode == "open" else (last or r["created_ts"])
            if now - ref >= hours * 3600:
                out.append({"room": r, "hours": round((now - ref) / 3600, 1), "members": rooms.members(r["id"])})
    return out


async def notify(items: list[dict], text: str, bot_id: str, send, say_qq) -> tuple[int, list[str]]:
    """send(bot_id, group_openid, text) 发官方机器人的群；say_qq(bot_id, gid, text, at_qqs) 发 QQ 号管的群，返回空串=成功。
    返回 (成功几个, 没发出去的说明)。"""
    ok, bad = 0, []
    for it in items:
        r = it["room"]
        g = pool.get(r["pool_id"])
        if not g:
            bad.append(f"R{r['id']}：没有分配到群")
            continue
        who = "、".join(m["name"] for m in it["members"])
        try:
            if pool.is_qq_only(g):
                qqs = [q for m in it["members"] for q in roles.qqs_of_role(m["role_id"])]
                err = await say_qq(g["bot_id"] or bot_id, str(g["group_openid"])[len(pool.QQ_PREFIX):], text, qqs)
                if err:
                    bad.append(f"R{r['id']}（{who}）：{err}")
                    continue
            else:
                await send(g["bot_id"] or bot_id, g["group_openid"], text)
            ok += 1
        except Exception as e:  # noqa: BLE001
            bad.append(f"R{r['id']}（{who}）：{str(e)[:40]}")
    return ok, bad
