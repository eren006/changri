"""画「睡莲」主题的侧栏底图（照莫奈《睡莲》）：src/changri/admin/static/lily-pond.webp。

作者用的小工具，不随安装包；改了这里要重新跑一次，把生成的图一起提交：
    .venv/bin/python tools/paint_lily.py

莫奈的水面没有硬的线条：是很多层半透明的短色块叠出来的，颜色互相透着、糊在一起。
所以这里一层一层地「罩色」：每层都是半透明的笔触，叠六层以后颜色才融在一起。
主调是蓝绿、水绿，淡紫只是一点倾向（作者：紫色的条看着吓人，不像莫奈）；天光倒影是暖粉和浅金的一大片；
右上角垂柳的倒影是往下淡掉的绿；睡莲叶是一簇簇压扁的绿色椭圆，点几朵粉白的花。整体浅、软、对比低，
因为侧栏上的字是深色的。随机数种子固定，每次画出来一样。
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "src" / "changri" / "admin" / "static" / "lily-pond.webp"
W, H = 520, 1800
S = 3
rnd = random.Random(1916)

# 水：蓝绿为主，淡紫只做倾向；没有深色
WATER_GREEN = [(150, 196, 190), (168, 206, 200), (140, 186, 186), (182, 214, 206), (160, 200, 214)]
WATER_BLUE = [(170, 192, 222), (186, 202, 228), (160, 186, 216)]
WATER_LILAC = [(206, 200, 228), (214, 206, 232), (198, 196, 226)]
SKY_WARM = [(246, 232, 226), (244, 226, 214), (240, 236, 220), (248, 240, 236)]
WILLOW = [(118, 164, 140), (136, 178, 150), (104, 150, 130), (156, 192, 160)]
PAD = [(116, 160, 106), (134, 178, 118), (102, 148, 102), (150, 188, 128), (126, 168, 128)]
FLOWER = [(238, 176, 196), (246, 202, 214), (252, 236, 238), (232, 156, 182)]

CLUMPS = [(0.80, 0.26, 0.10), (0.66, 0.47, 0.11), (0.90, 0.62, 0.09), (0.42, 0.75, 0.10), (0.78, 0.87, 0.12), (0.22, 0.96, 0.07)]


def jitter(c, k=8):
    return tuple(max(0, min(255, v + rnd.randint(-k, k))) for v in c)


def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def P(x, y):
    return x * W * S, y * H * S


def dab(draw, x, y, length, width, rgba, angle=0.0):
    """一笔短短的、两头圆的色块。rgba 带透明度；同一层里后画的盖前画的，层和层之间是半透明叠加。"""
    pts = []
    for i in range(5):
        t = (i / 4 - 0.5) * length / W
        bend = math.sin(i / 4 * math.pi) * width * 0.15 / H
        pts.append(P(x + math.cos(angle) * t, y + math.sin(angle) * t * W / H + bend))
    draw.line(pts, fill=rgba, width=int(width * S), joint="curve")
    r = width * S / 2
    for px, py in (pts[0], pts[-1]):
        draw.ellipse((px - r, py - r, px + r, py + r), fill=rgba)


def water_color(x, y):
    """某处水面该偏什么颜色：上面多天光（暖白），有的区域偏水绿、有的偏蓝、偶尔一点淡紫。"""
    sky = 0.42 * max(0.0, 1 - y) ** 1.5 + 0.16 * max(0.0, math.sin(y * 9 + x * 2.5))
    zone = 0.5 + 0.5 * math.sin(x * 3.6 + y * 5.5 + 1.1) * math.cos(y * 2.7 - x * 1.7)
    r = rnd.random()
    if r < sky:
        return rnd.choice(SKY_WARM)
    r = rnd.random()
    if r < 0.12:
        return rnd.choice(WATER_LILAC)
    return rnd.choice(WATER_GREEN) if r < 0.12 + 0.6 * zone + 0.2 else rnd.choice(WATER_BLUE)


def glaze(img, fn):
    """画一层半透明的罩色，叠到画上。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fn(ImageDraw.Draw(layer))
    return Image.alpha_composite(img, layer)


def paint():
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 255))
    base = ImageDraw.Draw(img)
    for yy in range(0, H * S, 6):                          # 底色：上面暖亮（天光），往下慢慢转成水绿
        t = yy / (H * S)
        base.rectangle((0, yy, W * S, yy + 6), fill=mix((236, 234, 236), (186, 208, 210), t) + (255,))
    # 底下一大片一大片柔和的色区：有的偏淡紫、有的偏深一点的蓝绿、有的是暖的天光——笔触罩在上面才有层次
    wash = Image.new("RGBA", img.size, (0, 0, 0, 0))
    wd = ImageDraw.Draw(wash)
    for _ in range(26):
        cx, cy = rnd.uniform(-0.1, 1.1), rnd.uniform(-0.05, 1.05)
        rx, ry = rnd.uniform(0.25, 0.55), rnd.uniform(0.05, 0.11)
        c = rnd.choice([(196, 188, 226), (204, 194, 230), (128, 176, 178), (140, 182, 196), (246, 226, 214), (238, 232, 206)])
        wd.ellipse((*P(cx - rx, cy - ry), *P(cx + rx, cy + ry)), fill=c + (rnd.randint(90, 150),))
    img = Image.alpha_composite(img, wash.filter(ImageFilter.GaussianBlur(S * 40)))

    # 水面：六层半透明的横向短色块，一层比一层小、一层比一层淡
    for k in range(6):
        gap = 16 - k * 1.6
        alpha = 92 - k * 8                               # 薄一点，让底下的色区透出来

        def water(d, gap=gap, alpha=alpha, k=k):
            yy = -gap
            while yy < H + gap:
                xx = -14
                while xx < W + 14:
                    x, y = (xx + rnd.uniform(-gap, gap)) / W, (yy + rnd.uniform(-gap, gap) * .6) / H
                    ang = rnd.gauss(0, 0.07) + 0.06 * math.sin(y * 16 + x * 2)
                    dab(d, x, y, rnd.uniform(16, 34) * (1 - k * 0.08), rnd.uniform(6, 10) * (1 - k * 0.09),
                        jitter(water_color(x, y)) + (int(alpha * rnd.uniform(.7, 1)),), ang)
                    xx += gap
                yy += gap * 0.7
        img = glaze(img, water)

    # 垂柳的倒影：右上角往下淡掉的绿，跟水色混一点
    def willow(d):
        for _ in range(1100):
            x = 1.02 - abs(rnd.gauss(0, 0.16))
            if x < 0.5:
                continue
            top, L = rnd.uniform(-0.03, 0.04), rnd.uniform(0.05, 0.20) * (0.5 + x)
            c = jitter(mix(rnd.choice(WILLOW), rnd.choice(WATER_GREEN), rnd.uniform(0.1, 0.45)))
            a0 = rnd.randint(80, 140) * min(1.0, (x - 0.5) / 0.3)
            ph, wob, w = rnd.uniform(0, math.tau), rnd.uniform(0.003, 0.007), rnd.uniform(5, 9) * S
            prev = None
            for i in range(9):
                t = i / 8
                pt = P(x + wob * math.sin(ph + t * 6), top + L * t)
                if prev:
                    d.line([prev, pt], fill=c + (int(a0 * (1 - t) ** 1.6),), width=int(w * (1 - 0.35 * t)))
                prev = pt
    img = glaze(img, willow)

    # 睡莲叶：先压一层淡淡的影子，再画叶子，最后点亮笔和花
    pads = []
    for cx, cy, size in CLUMPS:
        for _ in range(int(4 + size * 36)):
            pads.append((cx + rnd.gauss(0, size * 0.5), cy + rnd.gauss(0, size * 0.18), rnd.uniform(0.032, 0.068) * (0.7 + size * 3)))

    def pad_layer(d, kind):
        for cx, cy, rx in pads:
            ry = rx * 0.38
            notch = (cx * 97 + cy * 31) % math.tau
            n = int(10 + rx * 700) if kind == "leaf" else int(6 + rx * 200)
            for _ in range(n):
                a = rnd.uniform(0, math.tau)
                if kind == "leaf" and abs((a - notch + math.pi) % math.tau - math.pi) < 0.32:
                    continue
                rr = math.sqrt(rnd.random()) * (1.08 if kind == "shadow" else 1)
                x = cx + math.cos(a) * rx * rr
                y = cy + (0.005 if kind == "shadow" else 0) + math.sin(a) * ry * rr * W / H
                if kind == "shadow":
                    dab(d, x, y, rnd.uniform(10, 16), rnd.uniform(5, 7), (120, 150, 170, 55), rnd.uniform(-.2, .2))
                elif kind == "leaf":
                    leaf = mix(rnd.choice(PAD), rnd.choice([(178, 200, 120), (110, 160, 150), (150, 190, 140)]), rnd.uniform(0, .45))
                    dab(d, x, y, rnd.uniform(6, 12), rnd.uniform(4, 6), jitter(leaf, 10) + (rnd.randint(120, 175),), rnd.uniform(-.25, .25))
                elif a > math.pi:                                 # 叶面上半边反着天光
                    dab(d, x, y, rnd.uniform(4, 8), rnd.uniform(2.5, 3.5), jitter((200, 224, 184)) + (110,), rnd.uniform(-.2, .2))
    for kind in ("shadow", "leaf", "light"):
        img = glaze(img, lambda d, kind=kind: pad_layer(d, kind))

    def flowers(d):
        for cx, cy, size in CLUMPS:
            for _ in range(rnd.randint(1, 3)):
                fx, fy, fs = cx + rnd.gauss(0, size * .4), cy + rnd.gauss(0, size * .12), rnd.uniform(9, 14)
                for _ in range(int(16 + fs * 2)):
                    a, rr = rnd.uniform(0, math.tau), fs * math.sqrt(rnd.random())
                    dab(d, fx + math.cos(a) * rr / W, fy + math.sin(a) * rr * .6 / H, rnd.uniform(4, 8), rnd.uniform(3, 4.5),
                        jitter(rnd.choice(FLOWER)) + (220,), a + math.pi / 2)
                dab(d, fx, fy, 3, 3, (246, 216, 130, 230))
    img = glaze(img, flowers)

    img = img.convert("RGB").filter(ImageFilter.GaussianBlur(S * 0.9))   # 再糊一点：莫奈的笔触边是软的
    img = img.resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "WEBP", quality=84, method=6)
    print(f"{OUT}  {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    paint()
