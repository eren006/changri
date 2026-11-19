"""恋综角色卡和背包的图片（玩家自己看）：角色卡 = 名字、属性、货币、背包摘要、关系线/约会数；背包 = 货币 + 全部物品。
用 PIL 画，字体和长图共用 core/render/forward_image 里找到的中文字体。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from . import roles, rpg
from .db import get_config, get_conn
from .render import forward_image as F

W, PAD = 760, 36
INK, MUTE = (31, 33, 51), (104, 107, 132)
PAPER, CARD, LINE = (238, 239, 244), (250, 250, 253), (217, 219, 230)
ACCENT, SOFT, OK = (196, 58, 99), (228, 230, 239), (31, 122, 102)


def data(role_id: int) -> dict | None:
    with get_conn() as c:
        role = c.execute("SELECT id, name FROM roles WHERE id=?", (role_id,)).fetchone()
        if not role:
            return None
        attrs = [dict(r) for r in c.execute(
            "SELECT a.name, a.value, d.min_value, d.max_value FROM role_attrs a LEFT JOIN rpg_attr_defs d ON d.name=a.name WHERE a.role_id=? ORDER BY a.name", (role_id,))]
        rels = c.execute("SELECT COUNT(*) FROM relations WHERE role_lo=? OR role_hi=?", (role_id, role_id)).fetchone()[0]
        dates = c.execute("SELECT COUNT(*) FROM room_members m JOIN rooms r ON r.id=m.room_id WHERE m.role_id=? AND r.status='ended'", (role_id,)).fetchone()[0]
    from . import extensions
    shop = extensions.is_enabled("rpg")                  # 「物品与商城」关着：角色卡照常看，只是不显示货币和背包
    inv = rpg.inventory(role_id) if shop else []
    tix = []
    if extensions.is_enabled("gacha"):
        from . import gacha
        tix = gacha.tickets(role_id)
    from . import charprofile
    return {"profile": charprofile.get(role_id), "shop": shop, "tickets": tix, "name": role["name"], "attrs": attrs, "money": [x for x in inv if x["kind"] == "currency"],
            "items": [x for x in inv if x["kind"] != "currency"], "rels": rels, "dates": dates}


def _title() -> str:
    return get_config("brand_name", "") or F.BRAND_LABEL


class _Pen:
    """顺着往下画：先在一张足够高的画布上画，最后按实际高度裁掉。"""

    def __init__(self, height: int):
        self.img = Image.new("RGB", (W, height), PAPER)          # 底：背景、色块、卡片
        self.bd = ImageDraw.Draw(self.img)
        self.txt = Image.new("RGBA", (W, height), (0, 0, 0, 0))   # 字画在另一层，最后盖上去，卡片不会遮住字
        self.d = ImageDraw.Draw(self.txt)
        self.y = PAD
        self.f_big, self.f_h, self.f_b, self.f_s = F._font(True, 44), F._font(True, 26), F._font(False, 25), F._font(False, 21)

    def card(self, top: int, bottom: int) -> None:
        self.bd.rounded_rectangle((PAD - 12, top, W - PAD + 12, bottom), radius=18, fill=CARD, outline=LINE, width=2)

    def heading(self, text: str, note: str = "") -> None:
        self.d.text((PAD + 6, self.y), text, font=self.f_h, fill=INK)
        if note:
            w = self.d.textlength(note, font=self.f_s)
            self.d.text((W - PAD - 6 - w, self.y + 5), note, font=self.f_s, fill=MUTE)
        self.y += 46

    def row(self, left: str, right: str, bar: float | None = None, accent: bool = False) -> None:
        self.d.text((PAD + 6, self.y), F.clean(left)[:18], font=self.f_b, fill=INK)
        w = self.d.textlength(right, font=self.f_b)
        self.d.text((W - PAD - 6 - w, self.y), right, font=self.f_b, fill=ACCENT if accent else INK)
        self.y += 38
        if bar is not None:
            x0, x1 = PAD + 6, W - PAD - 6
            self.d.rounded_rectangle((x0, self.y - 4, x1, self.y + 5), radius=5, fill=SOFT)
            if bar > 0:
                self.d.rounded_rectangle((x0, self.y - 4, x0 + max(10, int((x1 - x0) * min(bar, 1))), self.y + 5), radius=5, fill=OK)
            self.y += 14

    def note(self, text: str) -> None:
        self.d.text((PAD + 6, self.y), text, font=self.f_s, fill=MUTE)
        self.y += 34

    def png(self) -> bytes:
        out = io.BytesIO()
        Image.alpha_composite(self.img.convert("RGBA"), self.txt).convert("RGB").crop((0, 0, W, self.y + PAD - 8)).save(out, "PNG")
        return out.getvalue()


def _use_text(x: dict) -> str:
    return f"×{x['count']}" + (f"（每件剩 {x['remaining_uses']} 次）" if x["remaining_uses"] >= 0 else "")


def render_card(role_id: int) -> bytes | None:
    info = data(role_id)
    if not info:
        return None
    pf = info["profile"]
    p = _Pen(860 + 60 * (len(info["attrs"]) + len(info["money"]) + len(info["items"]) + len(info["tickets"])))
    p.bd.rectangle((0, 0, W, 150), fill=(29, 31, 51))
    p.d.text((PAD, 34), F.clean(info["name"]), font=p.f_big, fill=(230, 231, 242))
    p.d.text((PAD, 98), f"{_title()} · 角色卡", font=p.f_s, fill=(147, 150, 179))
    p.y = 176
    top = p.y - 12
    p.heading("档案")
    p.row("性别 · 年龄", f"{pf['gender']} · {pf['age']} 岁")
    p.row("皮相", F.clean(pf["look"])[:14])
    if pf["short"]:
        p.row("简称", F.clean(pf["short"]))
    if pf["bio"]:
        bio = F.clean(pf["bio"])
        p.note(bio[:26] + ("…" if len(bio) > 26 else ""))
    p.card(top, p.y + 8)
    p.y += 30
    if info["attrs"]:
        top = p.y - 12
        p.heading("属性", f"{len(info['attrs'])} 项")
        for a in info["attrs"]:
            hi, lo = a["max_value"], a["min_value"] or 0
            p.row(a["name"], f"{a['value']}" + (f" / {hi}" if hi is not None else ""), bar=(a["value"] - lo) / (hi - lo) if hi is not None and hi > lo else None)
        p.card(top, p.y + 8)
        p.y += 30
    if info["money"]:
        top = p.y - 12
        p.heading("货币")
        for x in info["money"]:
            p.row(x["name"], str(x["count"]), accent=True)
        p.card(top, p.y + 8)
        p.y += 30
    if info["tickets"]:
        top = p.y - 12
        p.heading("抽取次数")
        for x in info["tickets"]:
            p.row(x["name"], f"{x['tickets']} 次", accent=True)
        p.card(top, p.y + 8)
        p.y += 30
    if not info["shop"]:
        return p.png()
    top = p.y - 12
    p.heading("背包", f"{len(info['items'])} 种")
    if info["items"]:
        for x in info["items"][:8]:
            p.row(x["name"], _use_text(x))
        if len(info["items"]) > 8:
            p.note(f"……还有 {len(info['items']) - 8} 种，发「背包」看全部")
    else:
        p.note("背包是空的")
    p.card(top, p.y + 8)
    return p.png()


def render_bag(role_id: int) -> bytes | None:
    info = data(role_id)
    if not info:
        return None
    p = _Pen(420 + 60 * (len(info["money"]) + len(info["items"])))
    p.bd.rectangle((0, 0, W, 130), fill=(29, 31, 51))
    p.d.text((PAD, 28), f"{F.clean(info['name'])} 的背包", font=p.f_big, fill=(230, 231, 242))
    p.d.text((PAD, 90), _title(), font=p.f_s, fill=(147, 150, 179))
    p.y = 156
    top = p.y - 12
    p.heading("货币")
    if info["money"]:
        for x in info["money"]:
            p.row(x["name"], str(x["count"]), accent=True)
    else:
        p.note("没有货币")
    p.card(top, p.y + 8)
    p.y += 30
    top = p.y - 12
    p.heading("物品", f"{len(info['items'])} 种")
    if info["items"]:
        for x in info["items"]:
            p.row(x["name"], _use_text(x))
    else:
        p.note("没有物品")
    p.card(top, p.y + 8)
    return p.png()
