"""画「星夜」主题的侧栏底图（照梵高《星月夜》的笔触）：src/changri/admin/static/starry-sky.webp。

作者用的小工具，不随安装包；改了这里要重新跑一次，把生成的图一起提交：
    .venv/bin/python tools/paint_starry.py

做法：在 3 倍大的画布上画几万笔短而粗的笔触，每一笔顺着一个「流场」走（天上横向的气流 + 几个漩涡），
颜色按位置从梵高的调色板里挑（钴蓝、群青、天蓝，靠近星星和月亮的地方换成铬黄、奶白）；
星星和月亮是一圈圈短笔触围成的光晕；右下角一棵深绿的柏树，底下一道暗色山丘和几点窗灯。
最后缩小到 520×1800（侧栏宽 260px 的 2 倍），存成 webp。随机数种子固定，每次画出来一样。
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "src" / "changri" / "admin" / "static" / "starry-sky.webp"
W, H = 520, 1800          # 成品尺寸
S = 3                     # 超采样倍数：先画大再缩小，笔触边缘才是圆润的
rnd = random.Random(1889)  # 《星月夜》画于 1889 年

# 调色板（RGB）
SKY = [(17, 33, 92), (24, 46, 120), (31, 62, 146), (40, 78, 165), (55, 98, 182), (78, 122, 198), (104, 148, 210)]
PALE = [(150, 182, 222), (186, 206, 226), (214, 226, 214)]
GOLD = [(242, 205, 74), (233, 176, 52), (248, 226, 140), (255, 240, 190)]
CYPRESS = [(9, 22, 18), (14, 33, 27), (20, 45, 36), (28, 58, 44), (12, 28, 40)]
HILL = [(12, 22, 58), (16, 30, 72), (22, 40, 88)]

# 漩涡：(中心 x, 中心 y, 半径, 方向) —— 坐标按成品尺寸的比例
VORTICES = [(0.70, 0.30, 0.34, 1), (0.30, 0.47, 0.22, -1), (0.78, 0.66, 0.20, 1)]
# 星星：(x, y, 光晕半径)；都放在右半边，左边是菜单文字
STARS = [(0.80, 0.17, 0.055), (0.66, 0.40, 0.040), (0.88, 0.52, 0.045), (0.72, 0.78, 0.035), (0.92, 0.90, 0.030)]
MOON = (0.82, 0.055, 0.085)


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def jitter(c, k=14):
    return tuple(max(0, min(255, v + rnd.randint(-k, k))) for v in c)


def field(x, y):
    """(x, y) 是成品坐标的比例 0~1（y 可以超过 1）。返回笔触方向（弧度）。"""
    # 天上横向的气流：缓缓起伏的波
    ang = 0.35 * math.sin(y * 9.0 + x * 3.0) + 0.18 * math.sin(y * 23.0 - x * 5.0)
    vx, vy = math.cos(ang), math.sin(ang)
    for cx, cy, r, d in VORTICES:
        dx, dy = x - cx, (y - cy) * (W / H) ** -1 * (W / H)
        dy = (y - cy) * H / W
        dist = math.hypot(dx, dy)
        if dist < r * 1.6:
            w = max(0.0, 1 - dist / (r * 1.6)) ** 1.3 * 2.4
            tx, ty = -dy * d, dx * d                      # 绕中心转
            n = math.hypot(tx, ty) or 1
            vx += w * tx / n
            vy += w * ty / n
    for sx, sy, r in STARS + [MOON]:                     # 星星周围：笔触绕着星星转圈
        dx, dy = x - sx, (y - sy) * H / W
        dist = math.hypot(dx, dy)
        if dist < r * 2.2:
            w = (1 - dist / (r * 2.2)) * 3.0
            vx += w * -dy / (dist or 1)
            vy += w * dx / (dist or 1)
    return math.atan2(vy, vx)


def glow(x, y):
    """离星星 / 月亮有多近：0（远）~ 1（就在光晕里）。"""
    best = 0.0
    for sx, sy, r in STARS + [MOON]:
        d = math.hypot(x - sx, (y - sy) * H / W)
        best = max(best, max(0.0, 1 - d / (r * 2.4)))
    return best


def in_cypress(x, y):
    """右下角柏树的轮廓：底宽顶尖、像一团往上烧的火苗。"""
    top, bot = 0.60, 1.02
    if not (top < y < bot):
        return False
    t = (y - top) / (bot - top)                         # 0 顶 ~ 1 底
    half = 0.012 + 0.075 * t ** 0.8 + 0.012 * math.sin(t * 22)
    cx = 0.885 + 0.02 * math.sin(t * 6)
    return abs(x - cx) < half


def hill_y(x):
    return 0.955 - 0.025 * math.sin(x * 5.5 + 0.6) - 0.012 * math.sin(x * 13)


def sky_color(x, y):
    g = glow(x, y)
    base = SKY[min(len(SKY) - 1, max(0, int(rnd.gauss(2.6 + 1.5 * (1 - y), 1.4))))]
    if rnd.random() < 0.10 + 0.25 * g:
        base = rnd.choice(PALE)
    if g > 0.35 and rnd.random() < g * 0.9:
        base = mix(rnd.choice(PALE), rnd.choice(GOLD), min(1, g * 1.3))
    for cx, cy, r, _ in VORTICES:                        # 漩涡里亮一些
        d = math.hypot(x - cx, (y - cy) * H / W)
        if d < r and rnd.random() < 0.18 * (1 - d / r):
            base = rnd.choice(PALE[:2] + SKY[-2:])
    return jitter(base, 10)


def stroke(draw, x0, y0, length, width, color, flow=field, highlight=True):
    """从 (x0, y0)（成品比例坐标）顺着流场画一笔。"""
    pts = [(x0 * W * S, y0 * W * S * (H / W) / (H / W) if False else y0 * H * S)]
    x, y = x0, y0
    step = 2.6 / W
    for _ in range(int(length / 2.6)):
        a = flow(x, y)
        x += math.cos(a) * step
        y += math.sin(a) * step * W / H
        pts.append((x * W * S, y * H * S))
    if len(pts) < 2:
        return
    draw.line(pts, fill=color, width=int(width * S), joint="curve")
    r = width * S / 2
    for px, py in (pts[0], pts[-1]):                      # 圆头
        draw.ellipse((px - r, py - r, px + r, py + r), fill=color)
    if highlight and width > 3:                           # 厚涂的高光：笔触一侧一道细的亮边
        hl = tuple(min(255, int(v * 1.22 + 18)) for v in color)
        off = width * S * 0.22
        draw.line([(px - off, py - off) for px, py in pts[1:-1]] or pts, fill=hl, width=max(1, int(width * S * 0.28)))


def ring_strokes(draw, cx, cy, radius, rings, colors, width):
    """一圈圈短笔触围成的光晕（星星、月亮）。"""
    for k in range(rings):
        rr = radius * (0.35 + 0.65 * (k + 1) / rings)
        n = int(10 + 26 * rr / radius)
        c = colors[min(len(colors) - 1, k)]
        for i in range(n):
            a = i / n * math.tau + rnd.uniform(-0.12, 0.12)
            seg = rnd.uniform(0.35, 0.55) * math.tau / n * 2.2
            pts = []
            for j in range(5):
                aa = a + seg * j / 4
                pts.append(((cx + math.cos(aa) * rr) * W * S, (cy * H + math.sin(aa) * rr * W) * S))
            draw.line(pts, fill=jitter(c, 12), width=int(width * S), joint="curve")


def paint():
    img = Image.new("RGB", (W * S, H * S), SKY[1])
    draw = ImageDraw.Draw(img)
    # 底色：上亮下暗的夜空
    for yy in range(0, H * S, 6):
        t = yy / (H * S)
        draw.rectangle((0, yy, W * S, yy + 6), fill=mix((22, 42, 112), (11, 20, 60), t))

    # 天空：两层笔触，第二层错开、更短
    for layer, (gap, lmin, lmax, wmin, wmax) in enumerate([(13, 18, 34, 6.0, 8.5), (10, 10, 22, 4.0, 6.0)]):
        ys = [i * gap for i in range(int(H / gap) + 2)]
        for yy in ys:
            for xx in range(-10, W + 10, gap):
                x = (xx + rnd.uniform(-gap, gap) * 0.6) / W
                y = (yy + rnd.uniform(-gap, gap) * 0.6) / H
                if in_cypress(x, y) or y > hill_y(x):
                    continue
                stroke(draw, x, y, rnd.uniform(lmin, lmax), rnd.uniform(wmin, wmax) * (0.85 if layer else 1), sky_color(x, y))

    # 月亮：橙黄的弯月 + 一圈圈光晕
    mx, my, mr = MOON
    ring_strokes(draw, mx, my, mr * 2.1, 5, [(255, 240, 180), (246, 214, 110), (230, 190, 90), (160, 180, 190), (110, 140, 190)], 5.5)
    for _ in range(260):                                  # 月亮本身：密集的短笔触填满一个弯月
        a, rr = rnd.uniform(0, math.tau), mr * math.sqrt(rnd.random()) * 0.95
        px, py = mx + math.cos(a) * rr, my * H / W + math.sin(a) * rr
        if math.hypot(px - (mx + mr * 0.42), py - (my * H / W - mr * 0.25)) < mr * 0.78:   # 挖掉一块成弯月
            continue
        ang = a + math.pi / 2
        x2, y2 = px + math.cos(ang) * 0.012, py + math.sin(ang) * 0.012
        draw.line([(px * W * S, py * W * S), (x2 * W * S, y2 * W * S)], fill=jitter(rnd.choice([(250, 196, 60), (246, 176, 40), (255, 226, 120)]), 10), width=int(6 * S))

    # 星星：亮芯 + 光晕
    for sx, sy, r in STARS:
        ring_strokes(draw, sx, sy, r * 1.9, 4, [(255, 248, 210), (244, 220, 120), (200, 210, 200), (120, 150, 200)], 4.6)
        for _ in range(60):
            a, rr = rnd.uniform(0, math.tau), r * 0.55 * math.sqrt(rnd.random())
            px, py = sx + math.cos(a) * rr, sy * H / W + math.sin(a) * rr
            draw.ellipse(((px - .006) * W * S, (py - .006) * W * S, (px + .006) * W * S, (py + .006) * W * S), fill=jitter((255, 244, 196), 8))

    # 山丘和村子：底部暗色的起伏，几点暖黄的窗灯
    for xx in range(-10, W + 10, 9):
        for k in range(14):
            x = (xx + rnd.uniform(-5, 5)) / W
            y = hill_y(x) + k * 0.004 + rnd.uniform(0, 0.004)
            a = 0.25 * math.sin(x * 9 + k)
            pts = [((x - 0.03 * math.cos(a)) * W * S, (y - 0.002 * math.sin(a)) * H * S), ((x + 0.03 * math.cos(a)) * W * S, (y + 0.002 * math.sin(a)) * H * S)]
            draw.line(pts, fill=jitter(rnd.choice(HILL), 8), width=int(7 * S))
    for x in (0.18, 0.27, 0.33, 0.46, 0.58):
        y = hill_y(x) + 0.012
        draw.rectangle(((x - .008) * W * S, (y - .003) * H * S, (x + .008) * W * S, (y + .002) * H * S), fill=(242, 200, 96))

    # 柏树：顺着火苗往上卷的深绿笔触
    def cyp_flow(x, y):
        return -math.pi / 2 + 0.55 * math.sin(y * 70 + x * 40)
    for _ in range(2600):
        x, y = rnd.uniform(0.78, 0.99), rnd.uniform(0.58, 1.0)
        if in_cypress(x, y):
            stroke(draw, x, y, rnd.uniform(10, 26), rnd.uniform(4.5, 7.5), jitter(rnd.choice(CYPRESS), 6), flow=cyp_flow, highlight=rnd.random() < 0.3)

    img = img.filter(ImageFilter.GaussianBlur(S * 0.35))
    img = img.resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "WEBP", quality=82, method=6)
    print(f"{OUT}  {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    paint()
