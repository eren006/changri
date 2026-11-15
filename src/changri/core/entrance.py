"""出场：抽一个出场顺序，在戏群里按顺序依次出场。轮到的人交一段出场文字，交了就发到戏群并轮到下一位；
没交的人会卡住，管理员可以「跳过」（挪到队尾）。已经提前交好的文字，轮到时自动发出。

戏群：管理员在目标群里发「设为戏群」，记成「机器人ID|群openid」（和公告群一样，openid 每个机器人各一套）。
轮到谁，就在谁的个人群提醒（并 @ 本人）；本人在个人群里「提交出场 角色名 内容」，或直接在戏群发「角色名 内容」，都会发到戏群。
出场可以带图：提交时把图片和文字一起发，图片存在数据库里，轮到时先发「名字 空行 正文」，再发图片。
"""
import random
import time

from . import season
from .botconf import configured_bot_ids
from .db import get_config, get_conn, set_config
from .roles import list_groups

TEXT_MAX = 1500
IMG_MAX = 3


def get_stage() -> tuple[str, str] | None:
    raw = get_config("stage_group", "")
    if not raw:
        return None
    if "|" in raw:
        a, b = raw.split("|", 1)
        return a, b
    ids = configured_bot_ids()
    return (ids[0], raw) if ids else None


def set_stage(bot_id: str, group_openid: str) -> None:
    set_config("stage_group", f"{bot_id}|{group_openid}" if group_openid else "")


def active() -> bool:
    return get_config("entrance_active", "0") == "1"


def queue() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT e.*, r.name AS name, r.member_openid AS member, (SELECT COUNT(*) FROM entrance_images i WHERE i.role_id=e.role_id) AS n_img "
                            "FROM entrance e JOIN roles r ON r.id=e.role_id ORDER BY e.pos").fetchall()


def current():
    return next((r for r in queue() if not r["done"]), None)


def roster() -> list:
    npcs = set(season.npc_names())
    with get_conn() as conn:
        return [r for r in conn.execute("SELECT id, name FROM roles ORDER BY id") if r["name"] not in npcs]


def draw() -> list:
    rows = roster()
    random.shuffle(rows)
    with get_conn() as conn:
        conn.execute("DELETE FROM entrance_images")
        conn.execute("DELETE FROM entrance")
        for i, r in enumerate(rows):
            conn.execute("INSERT INTO entrance(role_id, pos) VALUES(?,?)", (r["id"], i))
    set_config("entrance_active", "0")
    return rows


def start() -> tuple[bool, str]:
    if not get_stage():
        return False, "还没有戏群：管理员在戏群里发「设为戏群」。"
    if not queue():
        return False, "还没有出场顺序，先发「抽取出场顺序」。"
    if active():
        c = current()
        return False, f"出场已经在进行中（轮到「{c['name']}」），继续等待或「跳过出场」。" if c else "出场已经在进行中。"
    set_config("entrance_active", "1")
    return True, "✅ 出场开始！"


def submit(role_id: int, text: str, images: list[bytes] | None = None) -> tuple[bool, str]:
    text = text.strip()
    images = (images or [])[:IMG_MAX]
    if not text and not images:
        return False, "出场内容不能是空的。"
    if len(text) > TEXT_MAX:
        return False, f"出场内容太长了（{len(text)} 字，最多 {TEXT_MAX} 字）。"
    with get_conn() as conn:
        row = conn.execute("SELECT done FROM entrance WHERE role_id=?", (role_id,)).fetchone()
        if not row:
            return False, "出场顺序里没有你的角色（可能是 NPC，或顺序抽取后才创建的）。"
        if row["done"]:
            return False, "你已经出过场了。"
        conn.execute("UPDATE entrance SET text=?, ts=? WHERE role_id=?", (text, int(time.time()), role_id))
        conn.execute("DELETE FROM entrance_images WHERE role_id=?", (role_id,))      # 重新提交就整份覆盖
        for img in images:
            conn.execute("INSERT INTO entrance_images(role_id, data) VALUES(?,?)", (role_id, img))
    pre = not active() or (current() and current()["role_id"] != role_id)
    return True, ("✅ 出场内容已预存，轮到你时会自动发到戏群。开始前可以重复提交，后面的覆盖前面的。" if pre else "✅ 已提交，马上发到戏群。")


def mark_posted(role_id: int) -> bool:
    """玩家自己已经在戏群里发了出场：记为已出场，不用机器人再发一遍；之前预存的内容作废。"""
    with get_conn() as conn:
        n = conn.execute("UPDATE entrance SET done=1, text='' WHERE role_id=? AND done=0", (role_id,)).rowcount
        conn.execute("DELETE FROM entrance_images WHERE role_id=?", (role_id,))
    return n > 0


def skip() -> tuple[bool, str]:
    if not active():
        return False, "出场流程没在进行中。"
    c = current()
    if not c:
        return False, "已经全部出完了。"
    with get_conn() as conn:
        last = conn.execute("SELECT MAX(pos) FROM entrance").fetchone()[0]
        conn.execute("UPDATE entrance SET pos=? WHERE role_id=?", (last + 1, c["role_id"]))
    return True, f"⏭️ 已跳过「{c['name']}」，排到队尾（之后补交仍会被念出）。"


async def advance(send, announce_current: bool = False, send_image=None) -> None:
    """从当前位往后推：已经交了的连续发出，遇到没交的就停下提醒。send(bot_id, gid, text)，send_image(bot_id, gid, bytes)。"""
    stage = get_stage()
    if not (stage and active()):
        return
    while True:
        c = current()
        if not c:
            set_config("entrance_active", "0")
            await send(*stage, "出场已全部完成！")
            return
        if not (c["text"] or c["n_img"]):
            if announce_current:
                text = f"轮到你出场啦！直接在这个群里发「提交出场 {c['name']} 你的出场内容」，会马上发到戏群；你自己去戏群发「{c['name']} 你的出场内容」也行（那样机器人不会再重发）。可以带图片。"
                ok, info = await notify_at(c["role_id"], c["name"], text, send)          # 提醒发到本人的个人群，并 @ 本人
                if not ok:                                                    # 个人群发不出去，退回戏群提醒，免得卡住没人知道
                    await send(*stage, f"轮到【{c['name']}】出场啦，但没能通知到 TA 的个人群（{info}）。管理员可以「跳过出场」。")
            return
        await send(*stage, f"{c['name']}\n\n{c['text']}" if c["text"] else c["name"])
        if c["n_img"] and send_image:
            with get_conn() as conn:
                imgs = [r["data"] for r in conn.execute("SELECT data FROM entrance_images WHERE role_id=? ORDER BY id", (c["role_id"],))]
            for data in imgs:
                try:
                    await send_image(*stage, data)
                except Exception as e:  # noqa: BLE001
                    await send(*stage, f"（{c['name']} 的一张图片没发出去：{str(e)[:60]}）")
        with get_conn() as conn:
            conn.execute("UPDATE entrance SET done=1 WHERE role_id=?", (c["role_id"],))
            conn.execute("DELETE FROM entrance_images WHERE role_id=?", (c["role_id"],))
        announce_current = True        # 发完一位，下一位没交就要提醒


async def notify_at(role_id: int, name: str, text: str, send) -> tuple[bool, str]:
    """通知角色本人：主群优先，失败试备用群；开头 @ 本人（有 member_openid 才 @）。返回 (是否送达, 说明)。"""
    errs = []
    for g in sorted(list_groups(role_id), key=lambda r: r["kind"] != "main"):
        if not g["group_openid"]:
            continue
        body = ("{{AT:%s:%s}}" % (g["member_openid"], name.replace("}", "")) + text) if g["member_openid"] else text
        try:
            await send(g["bot_id"], g["group_openid"], body)
            return True, "主群" if g["kind"] == "main" else "备用群"
        except Exception as e:  # noqa: BLE001
            errs.append(str(e)[:60])
    return False, "；".join(errs) or "没有可用的群"


def status_text() -> str:
    q = queue()
    if not q:
        return "还没有出场顺序。"
    cur = current()
    if not active():
        head = "出场状态：" + ("已全部完成" if not cur else f"尚未开始（共 {len(q)} 人，发「开始出场」启动）")
    else:
        i = [r["role_id"] for r in q].index(cur["role_id"]) + 1 if cur else len(q)
        head = f"出场状态：进行中（第 {i}/{len(q)} 位）\n⏳ 当前轮到：{cur['name']}" + ("" if (cur["text"] or cur["n_img"]) else "（还没提交）") if cur else "出场状态：已全部完成"
    lines = []
    for r in q:
        if r["done"]:
            lines.append(f"✅ {r['name']}（已出场）")
        elif cur and r["role_id"] == cur["role_id"] and active():
            lines.append(f"⏳ {r['name']}（当前轮到，{'已提交，即将发出' if (r['text'] or r['n_img']) else '还没提交'}）")
        else:
            lines.append(f"📝 {r['name']}（已提前提交）" if (r["text"] or r["n_img"]) else f"⬜ {r['name']}（还没提交）")
    return head + "\n\n" + "\n".join(lines)


def split_name_text(raw: str, role_name: str) -> str | None:
    """「角色名 内容」「角色名\\n内容」「角色名：内容」→ 内容；只有角色名（可能带图）返回 ""；开头不是该角色名返回 None。"""
    raw = raw.strip()
    if not raw.startswith(role_name):
        return None
    rest = raw[len(role_name):]
    if rest and rest[0] not in " \n\t:：":
        return None
    return rest.lstrip(" \t:：").strip()
