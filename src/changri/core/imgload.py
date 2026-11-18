"""打开玩家上传的图片：先看尺寸再解码。

Pillow 的 Image.open 只读文件头、不解码像素；load() 才真正解码，一张 8900×8900 的纯色 PNG 只有三百多 KB，
解出来加上转换要吃掉 600 MB 左右内存。小手机和机器人在同一个进程里，几张这样的图一起传就可能把整个程序撑爆。
MAX_IMAGE_PIXELS 也挡不住：超过它 Pillow 只是警告，超过两倍才报错。所以这里在 load() 之前按像素数拒绝。"""
import io

MAX_PIXELS = 40_000_000          # 真正解码的像素数上限（JPEG 按缩小后的算）：4000 万像素，RGB 约 120 MB
OPEN_MAX_PIXELS = 300_000_000    # 给 Pillow 的全局上限（它超过两倍才报错）：只用来兜底别处打开的图


def open_upload(raw: bytes, need: tuple[int, int] | None = None):
    """返回已解码的 PIL 图片。打不开、像素太多抛 ValueError（文字给玩家看）。
    need：后面最多要用到的尺寸（宽, 高）；JPEG 会直接按接近这个尺寸解码，大照片能省很多内存。"""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = OPEN_MAX_PIXELS     # 放宽 Pillow 自己的检查：2 亿像素的 JPEG 缩着解码完全没问题；真正的尺寸由下面自己查
    try:
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
    except Exception:  # noqa: BLE001
        raise ValueError("不是能识别的图片") from None
    if need and img.format == "JPEG":
        img.draft("RGB", need)          # 按 1/2、1/4、1/8 缩着解码，解出来不会小于 need：手机拍的大照片也只解一小份
    dw, dh = img.size                   # 真正要解码的尺寸（JPEG 是缩过的）
    if dw * dh > MAX_PIXELS:
        raise ValueError(f"像素太多（{w}×{h}），缩小到 4000 万像素以内再传")
    try:
        img.load()
    except Exception:  # noqa: BLE001
        raise ValueError("不是能识别的图片") from None
    return img
