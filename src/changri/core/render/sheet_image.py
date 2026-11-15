"""公知/二表画成一张长图：标题、正文文字、下面依次排上提交的图片（图片缩到同一宽度）。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from . import forward_image as F

W = 760
PAD = 36
INK = (31, 33, 51)
MUTE = (104, 107, 132)
PAPER = (250, 250, 253)
LINE = (217, 219, 230)


def render(title: str, sub: str, text: str, images: list[bytes]) -> bytes:
    """返回一张 PNG（很长的话由调用方决定要不要切）。"""
    f_t, f_s, f_b = F._font(True, 34), F._font(False, 22), F._font(False, 26)
    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    lines = []
    for para in F.clean(text).split("\n"):
        lines += F._wrap(probe, para, f_b, W - 2 * PAD) or [""]
    imgs = []
    for raw in images:
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            h = round(im.height * (W - 2 * PAD) / im.width)
            imgs.append(im.resize((W - 2 * PAD, max(1, h))))
        except Exception:  # noqa: BLE001
            continue
    lh = 40
    height = PAD + 46 + 34 + 24 + len(lines) * lh + (20 if imgs else 0) + sum(i.height + 18 for i in imgs) + PAD
    canvas = Image.new("RGB", (W, height), PAPER)
    d = ImageDraw.Draw(canvas)
    y = PAD
    d.text((PAD, y), F.clean(title), font=f_t, fill=INK)
    y += 46
    d.text((PAD, y), F.clean(sub), font=f_s, fill=MUTE)
    y += 34
    d.line((PAD, y + 8, W - PAD, y + 8), fill=LINE, width=2)
    y += 24
    for ln in lines:
        d.text((PAD, y), ln, font=f_b, fill=INK)
        y += lh
    if imgs:
        y += 20
    for im in imgs:
        canvas.paste(im, (PAD, y))
        y += im.height + 18
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()
