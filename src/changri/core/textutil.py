CHUNK = 700  # 单条消息的字数上限，超过就拆成几条


def chunk_text(text: str, size: int = CHUNK) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            if cur:
                out.append(cur); cur = ""
            out.append(line[:size]); line = line[size:]
        if len(cur) + len(line) + 1 > size and cur:
            out.append(cur); cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def next_day_mark(a: str, b: str) -> str:
    """跨午夜的（结束钟点早于开始）返回「次日」，否则空串。后台、小手机模板里写 {{ a }}–{{ next_day(a, b) }}{{ b }}。"""
    return "次日" if a and b and len(a) == 5 and len(b) == 5 and b <= a else ""


def hm_text(a: str, b: str) -> str:
    """显示约会时间：跨午夜的在结束时间前加「次日」，如 23:00-次日01:00。"""
    return f"{a}-{next_day_mark(a, b)}{b}"
