"""季末结算海报（个人）：一位玩家的本季互动画成一张长图，宽 1080。

照搬「长日系统」图片服务的 season-poster（yuca_image_service/draw_card.js + yuca_skins.js），用 Pillow 重写：
7 款皮肤，上下花饰固定大小、中段平铺纸纹、花饰边缘渐隐；两遍排版（先量高度再铺纸）。
皮肤图很大（约 23MB），不进安装包，第一次用到时从主页下载（platform/remote_media.py）；下载不了就画纯色纸。

数据字段与旧存档站 /api/season_report 里每位玩家的字段同名：sessions、sms_sent/recv、gift_sent/recv、
lovemail_sent/recv、letter_sent/recv、top_sms_sent_to/recv_from、top_gift_sent_to/recv_from（[(名字, 次数)]）、
peak_slot、time_title、time_tagline、best_excerpt（[{role, text}]），再加 show_name、role_name。缺的块不画。
"""
from __future__ import annotations

import io
import logging
import math
import re
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from ...platform.layout import fonts_dir
from . import forward_image as F

log = logging.getLogger(__name__)

W = 1080

# 皮肤：配色与 yuca_skins.js 的 SKINS 一致（id 也一致，旧设置可以照搬）
SKINS: dict[str, dict] = {
    "antique": {"name": "古董日历", "paper": "#f3ecd8", "card": "#fff9e9", "ink": "#34392e", "sub": "#625c43", "accent": "#81703b", "rule": "#b7a676", "ornament": "#d8c69a", "kind": "scroll"},
    "sage": {"name": "鼠尾草花框", "paper": "#c1c2ad", "card": "#f4f3e8", "ink": "#354133", "sub": "#515a48", "accent": "#68774e", "rule": "#b8bba1", "ornament": "#e8d8b9", "kind": "floral"},
    "rose": {"name": "玫瑰花笺", "paper": "#f4eee4", "card": "#fffbf3", "ink": "#503e3e", "sub": "#72585b", "accent": "#a76672", "rule": "#ddc4be", "ornament": "#e8bdbe", "kind": "floral"},
    "botanical": {"name": "中式花笺", "paper": "#f1e8d5", "card": "#fbf6e9", "ink": "#3d4436", "sub": "#60634e", "accent": "#697b56", "rule": "#c8c2a0", "ornament": "#dbd2b1", "kind": "botanical"},
    "postal": {"name": "旧邮书信", "paper": "#eae2cc", "card": "#f8f2df", "ink": "#3c3b30", "sub": "#645e4c", "accent": "#79704d", "rule": "#c3b799", "ornament": "#d5c7a2", "kind": "postal"},
    "postcard": {"name": "旅行明信片", "paper": "#e9e3d3", "card": "#f8f3e5", "ink": "#34453e", "sub": "#58665c", "accent": "#62785d", "rule": "#b9bfaa", "ornament": "#c3d0bb", "kind": "landscape"},
    "lace": {"name": "蕾丝兔子", "paper": "#f3eee2", "card": "#fffaf0", "ink": "#304057", "sub": "#59677e", "accent": "#546b89", "rule": "#bfc4cc", "ornament": "#f0e2cb", "kind": "lace"},
}
DEFAULT_SKIN = "antique"
SKIN_FILES = [f"assets/skins/{k}.png" for k in SKINS] + ["assets/skins/botanical-branch.png", "assets/skins/sage-side-vine.png"]

# 每种花饰：顶部取原图多少比例、画多高；底部取多少比例、画多高（= yuca_skins.js 的 metrics，也是正文要躲开的高度）
METRICS = {"scroll": (.15, 230, .12, 190), "floral": (.16, 220, .16, 230), "botanical": (.16, 180, .30, 330),
           "postal": (.30, 350, .12, 160), "landscape": (.37, 460, .12, 140), "lace": (.40, 450, .16, 220)}


def skin_choices() -> list[tuple[str, str]]:
    return [(k, v["name"]) for k, v in SKINS.items()]


def get_skin(skin_id: str | None) -> tuple[str, dict]:
    sid = skin_id if skin_id in SKINS else DEFAULT_SKIN
    return sid, SKINS[sid]


def _rgb(h: str, a: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a


# ───────────── 素材 ─────────────

_img_lock = threading.Lock()
_img_cache: dict[str, Image.Image | None] = {}


def _asset(name: str) -> Image.Image | None:
    """皮肤图：本机有就用，没有就从主页下载（校验 sha256）。下载失败返回 None（画纯色纸），本进程里不再反复重试。"""
    rel = f"assets/skins/{name}.png"
    with _img_lock:
        if name in _img_cache:
            return _img_cache[name]
        from ...platform import remote_media
        try:
            path = remote_media.local_path(rel)
            im = Image.open(path)
            im.load()
            im = im.convert("RGBA")
        except Exception as e:  # noqa: BLE001 下载失败、文件坏了都退回纯色纸
            log.warning("季末海报皮肤 %s 取不到，改用纯色纸：%s", name, e)
            im = None
        _img_cache[name] = im
        return im


def prefetch(skin_id: str | None) -> bool:
    """提前把要用的皮肤图准备好（第一次会下载）。返回素材是否齐全。"""
    sid, t = get_skin(skin_id)
    ok = _asset(sid) is not None
    if t["kind"] == "botanical":
        _asset("botanical-branch")
    if sid == "sage":
        _asset("sage-side-vine")
    return ok


def clear_cache() -> None:
    with _img_lock:
        _img_cache.clear()
    _tile.cache_clear()


# ───────────── 纸张 ─────────────

def _crop_scaled(image: Image.Image, sx: float, sy: float, sw: float, sh: float, w: int, h: int) -> Image.Image:
    """= canvas drawImage(image, sx, sy, sw, sh, 0, 0, w, h)"""
    box = (int(round(sx)), int(round(sy)), int(round(sx + sw)), int(round(sy + sh)))
    return image.crop(box).resize((max(1, int(w)), max(1, int(math.ceil(h)))), Image.LANCZOS)


def _vgrad(h: int, stops: list[tuple[float, float]], w: int = 1) -> Image.Image:
    """竖直方向的透明度渐变（L 模式，0–255）。stops = [(位置 0–1, 不透明度 0–1)]"""
    col = []
    for y in range(h):
        p = (y + 0.5) / h
        v = stops[-1][1]
        for (p0, a0), (p1, a1) in zip(stops, stops[1:]):
            if p0 <= p <= p1:
                v = a0 if p1 == p0 else a0 + (a1 - a0) * (p - p0) / (p1 - p0)
                break
        else:
            if p < stops[0][0]:
                v = stops[0][1]
        col.append(int(round(max(0, min(1, v)) * 255)))
    g = Image.new("L", (1, h))
    g.putdata(col)
    return g.resize((w, h)) if w != 1 else g


def _mask_alpha(layer: Image.Image, mask: Image.Image) -> Image.Image:
    """= destination-in：图层原有的透明度再乘上 mask"""
    r, g, b, a = layer.split()
    return Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, mask)))


def _mirrored_tile(image: Image.Image, sx: int, sy: int, sw: int = 192, sh: int = 192) -> Image.Image:
    """四象限镜像的纸纹小块：平铺时上下左右的接缝像素一致"""
    src = image.crop((sx, sy, sx + sw, sy + sh))
    tile = Image.new("RGBA", (sw * 2, sh * 2))
    tile.paste(src, (0, 0))
    tile.paste(ImageOps.mirror(src), (sw, 0))
    tile.paste(ImageOps.flip(src), (0, sh))
    tile.paste(ImageOps.flip(ImageOps.mirror(src)), (sw, sh))
    return tile


@lru_cache(maxsize=16)
def _tile(name: str, kind: str) -> Image.Image | None:
    image = _asset(name)
    if image is None:
        return None
    iw, ih = image.size
    sx = int(iw * .20) if kind == "landscape" else 80 if kind == "botanical" else int(iw * .40)
    sy = 400 if kind == "botanical" else int(ih * .55)
    return _mirrored_tile(image, sx, sy)


def _tile_fill(canvas: Image.Image, tile: Image.Image, x0: int, y0: int, x1: int, y1: int) -> None:
    """= fillStyle = createPattern(tile, 'repeat'); fillRect(...)：花纹从画布原点起铺"""
    tw, th = tile.size
    region = Image.new("RGBA", (x1 - x0, y1 - y0))
    ox, oy = x0 % tw, y0 % th
    for yy in range(-oy, y1 - y0, th):
        for xx in range(-ox, x1 - x0, tw):
            region.paste(tile, (xx, yy))
    canvas.alpha_composite(region, (x0, y0))


def _faded_cap(canvas: Image.Image, image: Image.Image, source_y: float, source_h: float, y: int, h: int, top: bool) -> None:
    w = canvas.width
    layer = _crop_scaled(image, 0, source_y, image.width, source_h, w, h)
    stops = [(0, 1), (.67, 1), (1, 0)] if top else [(0, 0), (.33, 1), (1, 1)]
    layer = _mask_alpha(layer, _vgrad(layer.height, stops, w))
    canvas.alpha_composite(layer, (0, int(y)))


def _side_vines(canvas: Image.Image, t: dict) -> None:
    vine = _asset("sage-side-vine")
    if vine is None:
        return
    w, h = canvas.size
    _, top_h, _, bottom_h = METRICS[t["kind"]]
    tile = _crop_scaled(vine, vine.width * .35, 0, vine.width * .32, vine.height, 90, 900)
    tile = _mask_alpha(tile, _vgrad(900, [(0, 0), (.10, 1), (.90, 1), (1, 0)], 90))
    tile = _mask_alpha(tile, Image.new("L", tile.size, int(255 * .68)))
    layer = Image.new("RGBA", (110, h))
    y, i = top_h - 120, 0
    while y < h - bottom_h + 120:
        piece = ImageOps.flip(tile) if i % 2 else tile
        _paste_clipped(layer, piece, 10, y)
        y += 740
        i += 1
    p = lambda v: max(0.0, min(1.0, v / h))  # noqa: E731
    layer = _mask_alpha(layer, _vgrad(h, [(0, 0), (p(top_h - 40), 0), (p(top_h + 100), 1), (p(h - bottom_h - 100), 1), (p(h - bottom_h + 40), 0), (1, 0)], 110))
    canvas.alpha_composite(layer, (20, 0))
    canvas.alpha_composite(ImageOps.mirror(layer), (w - 20 - 110, 0))


def _paste_clipped(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    """alpha_composite 不接受负坐标：先裁掉画布外的部分"""
    cy = max(0, -y)
    if cy >= src.height or y >= dst.height:
        return
    piece = src.crop((0, cy, src.width, min(src.height, dst.height - y)))
    dst.alpha_composite(piece, (x, y + cy))


def paper(canvas: Image.Image, skin_id: str) -> bool:
    """铺纸。返回是否用上了皮肤图（False = 纯色纸）。"""
    sid, t = get_skin(skin_id)
    w, h = canvas.size
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, w, h), fill=_rgb(t["paper"]))
    image = _asset(sid)
    if image is None:
        d.rectangle((20, 20, w - 21, h - 21), outline=_rgb(t["rule"]), width=1)
        return False
    tile = _tile(sid, t["kind"])
    _tile_fill(canvas, tile, 0, 0, w, h)
    if t["kind"] == "botanical":                     # 连续的米纸与侧栏，花枝是单独的透明画层
        band_x, band_w = int(w * .745), int(w * .225)
        c0, c1, span = _rgb("#d6c49e"), _rgb("#dfcda8"), w - band_x      # 横向渐变从侧栏左边一直到纸边
        band = Image.new("RGBA", (band_w, 1))
        band.putdata([tuple(round(a + (b - a) * i / span) for a, b in zip(c0, c1)) for i in range(band_w)])
        canvas.alpha_composite(band.resize((band_w, h)), (band_x, 0))
        branch = _asset("botanical-branch")
        if branch is not None:
            art_w = int(w * .50)
            art_h = int(round(branch.height / branch.width * art_w))
            art = branch.resize((art_w, art_h), Image.LANCZOS)
            _paste_clipped(canvas, art, w - art_w - 10, h - art_h)
        return True
    top_ratio, top_h, bottom_ratio, bottom_h = METRICS[t["kind"]]
    if t["kind"] == "scroll":
        d.line((40, 0, 40, h), fill=_rgb(t["rule"]), width=1)
        d.line((w - 40, 0, w - 40, h), fill=_rgb(t["rule"]), width=1)
    if sid == "sage":
        _side_vines(canvas, t)
    if t["kind"] == "landscape":                     # 明信片：左右各一条原图边缘的纸纹
        sw, sy = 32, int(image.height * .55)
        for right in (False, True):
            strip = _mirrored_tile(image, image.width - sw if right else 0, sy, sw, 192)
            _tile_fill(canvas, strip, w - sw if right else 0, 0, w if right else sw, h)
    extra = 100                                      # 花饰固定大小，只在素纸处渐入渐出
    top_src = image.height * top_ratio * (top_h + extra) / top_h
    _faded_cap(canvas, image, 0, top_src, 0, top_h + extra, True)
    bottom_src = image.height * bottom_ratio * (bottom_h + extra) / bottom_h
    _faded_cap(canvas, image, image.height - bottom_src, bottom_src, h - bottom_h - extra, bottom_h + extra, False)
    return True


# ───────────── 字 ─────────────

@lru_cache(maxsize=64)
def _serif(size: int) -> ImageFont.FreeTypeFont:
    p = fonts_dir() / "SerifSC-Regular.otf"
    for cand in (p, Path("/System/Library/Fonts/Supplemental/Songti.ttc"), Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc")):
        if cand.exists():
            return ImageFont.truetype(str(cand), size)
    return F._font(True, size)


def _font(size: int, bold: bool = False, face: str = "sans") -> ImageFont.FreeTypeFont:
    return _serif(size) if face == "serif" else F._font(bold, size)


def text(v) -> str:
    if v is None or isinstance(v, (dict, list, tuple)):
        return ""
    s = F.clean(str(v)).strip()
    return "" if s == "未知" else s


def num(v) -> int:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0
    return int(max(0, n)) if math.isfinite(n) else 0


def _list(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def fit(f: ImageFont.FreeTypeFont, s: str, width: float) -> str:
    if f.getlength(s) <= width:
        return s
    out = ""
    for ch in s:
        if f.getlength(out + ch + "…") > width:
            break
        out += ch
    return out + "…"


_CLOSING = re.compile(r"^[，。、；：？！!?,.;:％%”’）》」』】〉〕…—]+$")


def wrap(f: ImageFont.FreeTypeFont, raw: str, width: float) -> list[str]:
    """标点粘在前一个字上一起换行，避免行首出现逗号；原文换行保留。"""
    out = []
    for para in raw.split("\n"):
        tokens: list[str] = []
        for ch in para:
            if _CLOSING.match(ch) and tokens:
                tokens[-1] += ch
            else:
                tokens.append(ch)
        cur = ""
        for tk in tokens:
            if cur and f.getlength(cur + tk) > width:
                out.append(cur)
                cur = ""
            cur += tk
        if cur:
            out.append(cur)
    return out


# ───────────── 排版（两遍：dry=True 只量高度） ─────────────

class _Painter:
    def __init__(self, canvas: Image.Image | None, t: dict):
        self.c = canvas
        self.t = t
        self.d = ImageDraw.Draw(canvas) if canvas is not None else None

    def put(self, s: str, x: float, y: float, size: int, color: str, bold: bool = False, face: str = "sans", align: str = "left", width: float | None = None):
        if self.d is None:
            return
        f = _font(size, bold, face)
        s = fit(f, s, width) if width else s
        anchor = {"left": "ls", "center": "ms", "right": "rs"}[align]
        stroke = 1 if face == "serif" and size >= 40 else 0      # 只有常规体宋体：大字描一圈，接近原版的粗宋
        self.d.text((x, y), s, font=f, fill=_rgb(color), anchor=anchor, stroke_width=stroke, stroke_fill=_rgb(color))

    def line(self, x0, y0, x1, y1, color: str, width: int = 1):
        if self.d is not None:
            self.d.line((x0, y0, x1, y1), fill=_rgb(color), width=width)

    def box(self, x, y, w, h):
        if self.c is None:
            return
        layer = Image.new("RGBA", (int(w) + 1, int(h) + 1))
        ImageDraw.Draw(layer).rectangle((0, 0, int(w), int(h)), fill=_rgb(self.t["card"], 0xdd), outline=_rgb(self.t["rule"], 0x70), width=1)
        self.c.alpha_composite(layer, (int(x), int(y)))

    def heading(self, label: str, x, y, w):
        f = _serif(32)
        self.put(fit(f, label, w), x, y, 32, self.t["ink"], face="serif")
        if self.d is not None:
            self.d.rectangle((x, y + 14, x + 47, y + 16), fill=_rgb(self.t["accent"]))

    def circle(self, cx, cy, r, color: str):
        if self.d is not None:
            self.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgb(color))

    def stamp(self, title: str, cx, cy, sw):
        """印章：描边方框 + 字，转 -0.07 弧度"""
        if self.c is None:
            return
        pad = 8
        lw, lh = int(sw) + pad * 2, 76 + pad * 2
        layer = Image.new("RGBA", (lw, lh))
        d = ImageDraw.Draw(layer)
        d.rectangle((pad, pad, pad + int(sw), pad + 76), outline=_rgb(self.t["accent"]), width=4)
        f = _serif(40)
        s = fit(f, title, sw - 32)
        d.text((lw / 2, pad + 38 + 13), s, font=f, fill=_rgb(self.t["accent"]), anchor="ms", stroke_width=1, stroke_fill=_rgb(self.t["accent"]))
        rot = layer.rotate(math.degrees(0.07), resample=Image.BICUBIC, expand=True)      # canvas 正角度是顺时针，PIL 是逆时针
        self.c.alpha_composite(rot, (int(cx - rot.width / 2), int(cy - rot.height / 2)))


def _podium(p: _Painter, title: str, items, x, y, w):
    t = p.t
    p.heading(title, x, y + 30, w)
    data = _list(items)[:3]
    if not data:
        p.put("暂无", x, y + 100, 26, t["sub"])
        return
    for i, it in enumerate(data):
        yy = y + 96 + i * 52
        p.circle(x + 18, yy - 9, 18, t["accent"] if i == 0 else t["rule"])
        p.put(str(i + 1), x + 18, yy, 22, "#ffffff" if i == 0 else t["ink"], bold=True, align="center")
        cnt = f"{num(it[1])} 次"
        tw = _font(26).getlength(cnt)
        p.put(text(it[0]), x + 52, yy, 28, t["ink"], bold=True, width=w - 52 - tw - 16)
        p.put(cnt, x + w - tw, yy, 26, t["sub"])


def _body(p: _Painter, d: dict, x0: int, cw: int, y0: int) -> float:
    t = p.t
    show, role = text(d.get("show_name")) or "本季", text(d.get("role_name")) or "玩家"
    y = y0 + 10
    p.put("CHANGRI / SEASON FINALE", x0, y, 22, t["sub"], bold=True)
    p.put(f"「{show}」已落幕", x0, y + 66, 40, t["accent"], face="serif", width=cw)
    p.put(fit(_serif(104), role, cw), x0, y + 184, 104, t["ink"], face="serif")
    p.put("你的本季互动报告", x0, y + 240, 28, t["sub"])
    p.line(x0, y + 272, x0 + cw, y + 272, t["rule"])
    y += 320

    g = lambda k: num(d.get(k))  # noqa: E731
    letters_out, letters_in = g("lovemail_sent") + g("letter_sent"), g("lovemail_recv") + g("letter_recv")
    stats = [("参与场次", g("sessions"), "场戏"),
             ("短信", g("sms_sent") + g("sms_recv"), f"发 {g('sms_sent')} / 收 {g('sms_recv')}"),
             ("礼物", g("gift_sent") + g("gift_recv"), f"送 {g('gift_sent')} / 收 {g('gift_recv')}"),
             ("信件", letters_out + letters_in, f"发 {letters_out} / 收 {letters_in}")]
    bw, bh = (cw - 24) / 2, 168
    for i, (label, n, sub) in enumerate(stats):
        x, yy = x0 + (i % 2) * (bw + 24), y + (i // 2) * (bh + 24)
        p.box(x, yy, bw, bh)
        p.put(label, x + 24, yy + 44, 24, t["sub"], bold=True)
        p.put(str(n), x + 24, yy + 112, 68, t["accent"], face="serif", width=bw - 48)
        p.put(sub, x + 24, yy + 150, 22, t["ink"], width=bw - 40)
    y += 2 * (bh + 24) + 20

    boards = [(name, d.get(k)) for name, k in (("你发短信最多的", "top_sms_sent_to"), ("给你发短信最多的", "top_sms_recv_from"),
                                               ("你送礼最多的", "top_gift_sent_to"), ("给你送礼最多的", "top_gift_recv_from")) if _list(d.get(k))]
    if boards:
        cols = 2 if cw >= 800 else 1
        col_w, cell_h = (cw - (cols - 1) * 40) / cols, 250
        for i, (name, items) in enumerate(boards):
            _podium(p, name, items, x0 + (i % cols) * (col_w + 40), y + (i // cols) * cell_h, col_w)
        y += math.ceil(len(boards) / cols) * cell_h + 10

    title = text(d.get("time_title"))
    if title:
        tg = text(d.get("time_tagline"))
        t_rows = wrap(_font(26), tg, cw - 56) if tg else []
        th = 150 + len(t_rows) * 40 + 30
        p.box(x0, y, cw, th)
        p.put("你最喜欢互动的时间", x0 + 28, y + 50, 24, t["sub"], bold=True)
        p.put(text(d.get("peak_slot")) or "—", x0 + 28, y + 122, 60, t["ink"], face="serif", width=cw - 340)
        for i, r in enumerate(t_rows):
            p.put(r, x0 + 28, y + 172 + i * 40, 26, t["ink"])
        sw = min(300, _serif(40).getlength(title) + 56)
        p.stamp(title, x0 + cw - 28 - sw / 2, y + 86, sw)
        y += th + 40

    ex = []
    for e in _list(d.get("best_excerpt"))[:6]:
        if isinstance(e, dict) and text(e.get("text")):
            ex.append((text(e.get("role")), wrap(_font(30), text(e.get("text")), cw - 56)))
    if ex:
        eh = 100 + sum(48 + len(rows) * 46 + 14 for _, rows in ex)
        p.box(x0, y, cw, eh)
        p.heading("你本季最精彩的一场戏", x0 + 28, y + 56, cw - 56)
        yy = y + 120
        for who, rows in ex:
            p.put(who, x0 + 28, yy, 26, t["accent"], bold=True)
            yy += 48
            for r in rows:
                p.put(r, x0 + 28, yy, 30, t["ink"])
                yy += 46
            yy += 14
        y += eh + 40
    p.put("长日将尽 / 感谢你本季的每一场戏", x0 + cw / 2, y + 20, 22, t["sub"], align="center")
    return y + 50


def _watermark(canvas: Image.Image) -> None:
    w, h = canvas.size
    mark = max(18, round(w / 50))
    layer = Image.new("RGBA", canvas.size)
    ImageDraw.Draw(layer).text((w - round(mark * 1.2), h - round(mark * 0.9)), "长日出品", font=F._font(True, mark), fill=(25, 23, 25, 128), anchor="rs")
    canvas.alpha_composite(layer)


def render_image(data: dict, skin: str | None = None) -> Image.Image:
    sid, t = get_skin(skin)
    top_h, bottom_h = METRICS[t["kind"]][1], METRICS[t["kind"]][3]
    x0, cw, y0 = 100, 680 if t["kind"] == "botanical" else 880, top_h + 30
    end = _body(_Painter(None, t), data, x0, cw, y0)          # 第一遍：只量高度
    h = max(900, math.ceil(end + bottom_h + 10))              # 页脚要落在底部花饰之上
    canvas = Image.new("RGBA", (W, h))
    paper(canvas, sid)
    _body(_Painter(canvas, t), data, x0, cw, y0)
    _watermark(canvas)
    return canvas.convert("RGB")


def render(data: dict, skin: str | None = None) -> bytes:
    """画好的海报（JPEG 字节；纸纹细节多，PNG 要好几 MB，发群太慢）"""
    out = io.BytesIO()
    render_image(data, skin).save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


SAMPLE = {
    "show_name": "示例季度", "role_name": "示例角色", "sessions": 7, "sms_sent": 42, "sms_recv": 38, "gift_sent": 6, "gift_recv": 9,
    "lovemail_sent": 2, "lovemail_recv": 3,
    "top_sms_sent_to": [["阿林", 18], ["苏晚", 12], ["顾川", 5]], "top_sms_recv_from": [["阿林", 20], ["顾川", 9]],
    "top_gift_sent_to": [["苏晚", 3], ["阿林", 2]], "top_gift_recv_from": [["顾川", 4], ["阿林", 3], ["苏晚", 2]],
    "peak_slot": "22:00–24:00", "time_title": "深夜电台长", "time_tagline": "深夜还不睡，把私人群当电台在开",
    "best_excerpt": [{"role": "示例角色", "text": "雨停了。你说要送我回去，可是伞一直往我这边歪。"},
                     {"role": "阿林", "text": "我只是怕你的书淋湿。"},
                     {"role": "示例角色", "text": "书在包里。"}],
}
