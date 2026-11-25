import re


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


_WIDE = str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
                      "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_BRACKETS = ("[]", "【】", "()", "（）", "「」", "<>", "《》")


def parse_id(text: str, prefix: str = "") -> int | None:
    """玩家填的编号 / 序号 / 座位号 → 数字；不是编号写法返回 None。全部指令共用，写法统一。
    作者定的：能认的放宽（玩家照按钮「摘 #1」、房间号「R12」抄也能用），用法回执里只教一种写法（纯数字，房间号写 R12），不列一串变体。
    认的写法：
    3、03、#3、＃3、[3]、【3】、（3）、全角 ３ 都认；prefix 给了字母（房间号是 "R"）时 R3、r3、[R3]、#R3 也认，别的字母不认。
    上标² 这类 isdigit() 为真、int() 却会报错的字符不认（以前直接崩）。"""
    import re
    t = (text or "").strip().translate(_WIDE)
    for a, b in _BRACKETS:
        if len(t) >= 2 and t[0] == a and t[-1] == b:
            t = t[1:-1].strip()
            break
    m = re.fullmatch(r"[#＃﹟]?\s*([A-Za-z]?)\s*([0-9]{1,9})", t)
    if not m or (m[1] and m[1].upper() not in prefix.upper()):
        return None
    return int(m[2])


# 时间段里的连接号：输入法常打出来的各种横线、波浪线都算「-」
_DASHES = "-~～〜—–－﹣―‐−"
_TIME_PUNCT = str.maketrans({"：": ":", **{c: "-" for c in _DASHES}})


def norm_time(text: str) -> str:
    """约时间用的写法统一成半角：全角数字 → 半角，「：」→「:」，「～ — － – ﹣」这类横线 / 波浪线 → 「-」，去掉首尾空白。
    「１４：００～１５：００」→「14:00-15:00」。只给时间这一段用，别拿去洗角色名（名字里的全角字母要保留）。"""
    return (text or "").strip().translate(_WIDE).translate(_TIME_PUNCT)


_D = "[0-9０-９]"
# 只认开头的时间段：前面最多带几个「R12」「D1」「#12」这样的编号词（修改时间线 R12 D1 1400 - 1500）。
# 不在中间找：故事历写「3月5日 花园 …」、心愿内容里写「想 2000 - 2100 看星星」时，后面的数字原样不动。
_RANGE_GAP = re.compile(rf"^((?:\s*[RrＲｒDdＤｄ#＃]{_D}+\s+)*)\s*({_D}{{1,2}}[:：]?{_D}{{2}})\s*(?:[{_DASHES}]+|到|至)\s*({_D}{{1,2}}[:：]?{_D}{{2}})(?=\s|$)")


def join_time_range(text: str) -> str:
    """指令参数按空格拆之前先调这个：把开头的「1400 - 1500」「14：00～15：00」这种中间有空格 / 全角符号的时间段并成一个词「1400-1500」，
    不然按空格一拆，时间就被拆成三段，后面的地点、名字全错位。只动第一个时间段，别的字原样不动。"""
    return _RANGE_GAP.sub(lambda m: f"{m[1]}{norm_time(m[2])}-{norm_time(m[3])}", text or "", count=1)
