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
