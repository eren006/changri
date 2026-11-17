"""围猎地图出图：格子、行列号、陷阱 / 线索远近 / 打空，下面一行图例。表情符号画不出来（字体没有），所以全用色块 + 汉字。
只画公开信息；猎物位置只在 reveal 给了时画（对局结束后）。"""
import io

from PIL import Image, ImageDraw

from . import forward_image as F

CELL, PAD, HEAD = 84, 36, 44
BG, INK, MUTE, LINE = (250, 247, 240), (52, 50, 46), (128, 122, 110), (214, 206, 190)
STYLE = {                     # 标记 → (底色, 字色, 字)
    "trap": ((122, 92, 60), (255, 255, 255), "陷"),
    "h4": ((176, 38, 38), (255, 255, 255), "◎"),
    "h3": ((222, 92, 80), (255, 255, 255), "近"),
    "h2": ((236, 160, 70), (255, 255, 255), "中"),
    "h1": ((96, 140, 196), (255, 255, 255), "远"),
    "miss": ((170, 166, 158), (255, 255, 255), "×"),
    "prey": ((40, 120, 80), (255, 255, 255), "鹿"),
}
LEGEND = [("trap", "陷阱"), ("h3", "很近"), ("h2", "不远"), ("h1", "很远"), ("miss", "打空")]


def render(st: dict, title: str, reveal: tuple[int, int] | None = None) -> bytes:
    n = st["n"]
    grid = n * CELL
    w = PAD * 2 + HEAD + grid
    h = PAD + 56 + HEAD + grid + 30 + 40 + PAD
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    f_title, f_lab, f_cell, f_leg = F._font(True, 30), F._font(False, 24), F._font(True, 34), F._font(False, 22)
    d.text((PAD, PAD), title, font=f_title, fill=INK)
    x0, y0 = PAD + HEAD, PAD + 56 + HEAD
    for c in range(n):
        d.text((x0 + c * CELL + CELL // 2, y0 - HEAD // 2), "ABCDEFGHIJ"[c], font=f_lab, fill=MUTE, anchor="mm")
    for r in range(n):
        d.text((x0 - HEAD // 2, y0 + r * CELL + CELL // 2), str(r + 1), font=f_lab, fill=MUTE, anchor="mm")
    mark = {}
    for x in st.get("traps", []):
        mark[(x["r"], x["c"])] = "trap"
    for x in st.get("misses", []):
        mark[(x["r"], x["c"])] = "miss"
    for x in st.get("clues", []):
        mark[(x["r"], x["c"])] = f"h{x['heat']}"
    if reveal:
        mark[tuple(reveal)] = "prey"
    for r in range(n):
        for c in range(n):
            bx, by = x0 + c * CELL, y0 + r * CELL
            k = mark.get((r, c))
            fill = STYLE[k][0] if k else (255, 253, 248)
            d.rounded_rectangle((bx + 4, by + 4, bx + CELL - 4, by + CELL - 4), radius=12, fill=fill, outline=LINE, width=2)
            if k:
                d.text((bx + CELL // 2, by + CELL // 2), STYLE[k][2], font=f_cell, fill=STYLE[k][1], anchor="mm")
    ly = y0 + grid + 30
    lx = PAD
    for k, label in LEGEND:
        d.rounded_rectangle((lx, ly, lx + 26, ly + 26), radius=6, fill=STYLE[k][0])
        d.text((lx + 34, ly + 13), label, font=f_leg, fill=MUTE, anchor="lm")
        lx += 34 + int(d.textlength(label, font=f_leg)) + 22
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
