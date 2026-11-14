"""短信 / 送礼的写法识别（不依赖 QQ/NoneBot，方便测试）。认三种：
  短信 收信人 内容                  送礼 对象 礼物 [留言]
  张三短信 收信人 内容（前面是署名） 张三送礼 对象 礼物 [留言]
  【短信】竖着写：对象：/内容：/署名：  【送礼】对象：/礼物：/留言：/署名：
"""
import re

KIND_WORD = {"短信": "sms", "送礼": "gift", "送礼物": "gift"}
_KEYS = {"对象": "to", "收信人": "to", "收件人": "to", "礼物": "gift", "内容": "content", "留言": "content", "署名": "sign", "昵称": "sign"}
_FORM_HEAD = re.compile(r"\A【(短信|送礼物|送礼)】\s*")
_KEY_LINE = re.compile(r"^(" + "|".join(_KEYS) + r")\s*[:：]\s*(.*)$")
_INLINE = re.compile(r"\A(?P<sign>[^\s【】]{0,12}?)(?P<kw>短信|送礼物|送礼)(?:\s+(?P<rest>[\s\S]*))?\Z")
PREFIX = "/.。"


def parse_form(text: str) -> dict:
    out: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        m = _KEY_LINE.match(line.strip())
        if m:
            cur = _KEYS[m.group(1)]
            out[cur] = m.group(2).strip()
        elif cur:
            out[cur] = (out[cur] + "\n" + line.strip()).strip()
    return out


def match_social(text: str) -> dict | None:
    """返回 {kind, sign, to, gift, content, bad}；不是这类消息返回 None。bad=True 表示是这类指令但写得不完整（要给用法提示）。
    带署名前缀却写不全的，多半是聊天里碰巧带了「短信」两个字，返回 None 不打扰。"""
    t = text.strip()
    if t and t[0] in PREFIX:
        t = t[1:].lstrip()
    m = _FORM_HEAD.match(t)
    if m:
        kind = KIND_WORD[m.group(1)]
        f = parse_form(t[m.end():])
        ok = bool(f.get("to")) and bool(f.get("content") if kind == "sms" else f.get("gift"))
        return {"kind": kind, "sign": f.get("sign", ""), "to": f.get("to", ""), "gift": f.get("gift", ""), "content": f.get("content", ""), "bad": not ok}
    m = _INLINE.match(t)
    if not m:
        return None
    kind, sign = KIND_WORD[m.group("kw")], m.group("sign")
    rest = (m.group("rest") or "").strip()
    if kind == "sms":
        parts = rest.split(None, 1)
        to, gift, content = (parts[0], "", parts[1]) if len(parts) == 2 else (parts[0] if parts else "", "", "")
        ok = len(parts) == 2
    else:
        parts = rest.split(None, 2)
        to, gift, content = (parts + ["", "", ""])[:3]
        ok = len(parts) >= 2
    if not ok and sign:
        return None
    return {"kind": kind, "sign": sign, "to": to, "gift": gift, "content": content, "bad": not ok}


TEMPLATES = {
    "短信": "短信 收信人 内容\n例：短信 张三 你好！\n\n也可以竖着写：\n【短信】\n对象：张三\n内容：你好！\n署名：神秘人（选填，管理员开放了自定义署名才有效）\n\n发错了：2 分钟内发「撤回」。",
    "送礼": "送礼 对象 礼物 [留言]\n例：送礼 张三 一束玫瑰 生日快乐\n\n也可以竖着写：\n【送礼】\n对象：张三\n礼物：一束玫瑰\n留言：生日快乐（选填）\n署名：匿名（选填）\n\n发错了：2 分钟内发「撤回」。",
    "撤回": "发错短信/礼物时，2 分钟内发：\n撤回\n\n会撤回你最近发的那一条（对方群里的也一起删），今天的次数返还，可以马上重发。\n超过 2 分钟请联系管理员。",
}
