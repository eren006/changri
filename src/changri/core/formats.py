"""「格式」菜单：每个指令一份可以复制的写法模板。只列出现在开着的功能，关掉的功能不出现、也不给模板。

发「格式」= 菜单（有按钮权限时是一排按钮，点一下就拿到那份模板）；发「格式短信」「格式心动信」……直接拿模板。
加一个：往 ENTRIES 里加一行 (名字, 开没开, 模板)。开没开是个函数，每次发「格式」时现查，后台改了开关不用重启这里。
"""
from __future__ import annotations

from typing import Callable

from . import forms


def _ext(fid: str) -> Callable[[], bool]:
    def on() -> bool:
        from . import extensions
        return extensions.is_enabled(fid)
    return on


def _always() -> bool:
    return True


def _stakeout() -> bool:
    from . import rooms
    return rooms.stakeout_enabled()


def _joinreq() -> bool:
    from . import rooms
    return rooms.joinreq_enabled()


def _lovemail() -> bool:
    from . import lovemail
    return _ext("lovemail")() and lovemail.enabled()


def _relations() -> bool:
    from . import relations
    return _ext("relations")() and relations.enabled()


# (名字, 开没开, 模板)。顺序 = 菜单里按钮的顺序：常用的在前
ENTRIES: list[tuple[str, Callable[[], bool], str]] = [
    ("短信", _always, forms.TEMPLATES["短信"]),
    ("送礼", _always, forms.TEMPLATES["送礼"]),
    ("心动信", _lovemail,
     "发送心动信\n【发送对象】角色名\n【内容】想说的话\n【署名】自定义昵称（选填，不写就是匿名）\n\n"
     "也可以一行写完：心动信 角色名 内容\n管理员统一送出前，发「撤回心动信」可以取消。"),
    ("约会", _always, "约会 时间 地点 对方（多人用 / 隔开）\n例：约会 1400-1500 咖啡厅 张三\n例：约会 1400-1500 咖啡厅 张三/李四\n\n聊完在约会群里发「结束约会」。"),
    ("电话", _always, "电话 时间 对方（多人用 / 隔开）\n例：电话 1400-1500 张三\n\n聊完在群里发「结束电话」。"),
    ("关系线", _relations, "拉线 对方名 内容\n例：拉线 张三 那天在花园里，你递给我一把伞……\n\n查看：查看关系线（列表）／查看关系线 名字（细节）\n撤回：撤回关系 对方名 内容里的一句"),
    ("公知", _ext("relations"), "提交公知 你的内容（可以同时发图片）\n重复提交会覆盖上一次。\n\n查看：查看公知／查看公知 名字"),
    ("二表", _ext("relations"), "提交二表 你的内容（可以同时发图片）\n重复提交会覆盖上一次。二表只有本人和管理员能看。\n\n查看：查看二表"),
    ("心愿", _ext("wishes"), "挂心愿 时间 地点 内容 [| 昵称]\n例：挂心愿 1400-1500 花园 一起散步 | 神秘人A\n"
     "（昵称最多 10 个字；对外只显示性别和昵称，不显示真名；24 小时没人摘自动消失）\n\n看心愿／摘心愿 编号／撤心愿 [编号]"),
    ("发帖", _ext("forum"), "发帖 署名 内容（只写内容就用角色名署名）\n例：发帖 路人甲 今天天气真好！\n\n"
     "回复帖子 贴号 [引用N|署名] 内容\n例：回复帖子 001 你说得对！\n\n查看帖子／查看帖子 贴号"),
    ("点歌", _ext("song"), "点歌 歌名 [歌手] [给 某人] [寄语]\n例：点歌 晴天 周杰伦 给 张三 生日快乐\n\n"
     "也可以先把网易云 / QQ 音乐的歌分享到群里，回复那张卡片发：点歌 [给 某人] [寄语]"),
    ("踩点", _stakeout, "踩点 时间 地点（一个人的群，不带别人）\n例：踩点 1400-1500 大图书馆"),
    ("申请加入", _joinreq, "申请加入 对方名 [开始时间]\n例：申请加入 张三 14:00\n\n别人申请加入你的约会：发「加入请求」看，点按钮同意或拒绝。"),
    ("撤回", _always, forms.TEMPLATES["撤回"]),
]

ALIASES = {"私约": "约会", "送礼物": "送礼", "礼物": "送礼", "发送心动信": "心动信", "拉线": "关系线", "论坛": "发帖", "帖子": "发帖", "挂心愿": "心愿"}


# 点「格式」菜单里的按钮、或模板下面的「✏️ 填入输入框」，填进输入框的空白格式（不发出，玩家填好再发）。
# 能竖着写的（短信、送礼、心动信）填空白表单，冒号后面直接接着写；其余填指令开头。没有的（撤回）点了就看说明。
FILL = {"短信": "【短信】\n对象：\n内容：", "送礼": "【送礼】\n对象：\n礼物：\n留言：",
        "心动信": "发送心动信\n【发送对象】\n【内容】\n【署名】",
        "约会": "约会 ", "电话": "电话 ",
        "关系线": "拉线 ", "公知": "提交公知 ", "二表": "提交二表 ", "心愿": "挂心愿 ", "发帖": "发帖 ", "点歌": "点歌 ",
        "踩点": "踩点 ", "申请加入": "申请加入 "}


def fill(name: str) -> str:
    return FILL.get(ALIASES.get(name, name), "")


def available() -> list[str]:
    """现在开着的功能的模板名，按菜单顺序。"""
    return [name for name, on, _t in ENTRIES if on()]


def template(name: str) -> tuple[str, str]:
    """(状态, 文字)：状态 ok = 给出模板；off = 这个功能没开；unknown = 没有这个模板。"""
    name = ALIASES.get(name, name)
    for n, on, t in ENTRIES:
        if n == name:
            return ("ok", t) if on() else ("off", f"「{n}」现在没有开放。")
    return "unknown", ""


def menu_text(names: list[str]) -> str:
    return ("📋 格式指令\n点下面的按钮，格式会填进输入框，填好再发送\n"
            "想看带例子的完整写法：发「格式短信」「格式心动信」这样\n现在可用：" + "、".join(names))


# 「我的」：自己的东西集中在一处，一排按钮随时点。(按钮上的字, 点了发的指令, 开没开)。只列开着的功能。
MINE: list[tuple[str, str, Callable[[], bool]]] = [
    ("角色卡", "名片", _ext("rpg")),          # 用「名片」：「角色卡」在跑团模块里也是指令
    ("时间线", "时间线", _always),
    ("待回", "我的待回", _always),
    ("数量", "我的数量", _always),
    ("统计", "本场统计", _always),
    ("关系线", "查看关系线", _relations),
    ("心动信", "我的心动信", _lovemail),
    ("背包", "背包", _ext("rpg")),
    ("图鉴", "我的图鉴", _always),
    ("小手机", "手机码", _ext("phone")),
    ("我的群", "我的群", _always),
]


# 「我的」速查里每条的一句话说明（照老系统「我的」：说明 + 用法）
MINE_DESC = {
    "名片": "角色卡：档案、属性",
    "时间线": "自己今天和以后的约会安排",
    "我的待回": "开场了还没进的群、进行中的房间、要你同意的改时间和加入申请、轮到你回的关系线、今天的心动信",
    "我的数量": "今天发起的约会/电话/短信/礼物/心愿次数，以及全服今天的总数",
    "本场统计": "这一场 RP 你的互动数据",
    "查看关系线": "你和别人之间的关系线",
    "我的心动信": "收到和寄出的心动信",
    "背包": "你有的物品和货币",
    "我的图鉴": "逛礼品店收集到的礼物（可以无限送）",
    "手机码": "打开小手机的登录码",
    "我的群": "你绑了哪些群",
}


def mine_text() -> str:
    return "\n".join(f"· {cmd}：{MINE_DESC.get(cmd, label)}" for label, cmd in mine())


def mine() -> list[tuple[str, str]]:
    return [(label, cmd) for label, cmd, on in MINE if on()]


def qq_needed() -> bool:
    """用了 QQ 小号（房间群靠绑的 QQ 认人）才要求新角色绑 QQ。"""
    from .db import get_config
    return get_config("qqlink_on", "0") == "1"


def setup_step(role, head: str) -> str:
    """建角色 / NPC 认群之后的下一步：要绑 QQ 又还没绑 → 只提示第 2 步（绑好才发回执，免得玩家看完回执就漏了）；否则直接发完整回执。"""
    from . import roles
    if qq_needed() and not roles.qqs_of_role(role["id"]):
        return (f"{head}\n\n第 2 步：在这个群里发「绑定QQ 你的QQ号」（填你自己正在用的 QQ 号，房间群里说话要靠它认出你，不绑不算到场）。\n"
                "绑好以后发给你完整的角色回执。")
    return welcome(role, head)


def welcome(role, head: str = "") -> str:
    """建角色的最后一步：完整回执（个人群、QQ、初始档案 + 接下来能填什么）。只列开着的功能。
    名字写错了可以自己发「修改名字 新名字」改（HELP 里有）。"""
    from . import charprofile, extensions, roles
    from .db import get_config
    qqs = roles.qqs_of_role(role["id"])
    lines = [head or f"✅ 角色「{role['name']}」创建好了，这个群就是你的个人群。"] + ([f"🔗 绑定的 QQ：{'、'.join(qqs)}"] if qqs else []) + ["", "你的初始档案：", *charprofile.summary_lines(role),
             "", charprofile.HELP, "", "📎 其他可以填的："]
    lines.append("  提交公知 内容（可以带图，重复提交会覆盖）")
    lines.append("  提交二表 内容（同上）")
    if get_config("dice_enabled", "1") == "1":
        lines.append("  录属性 力量60 敏捷70（跑团检定用，可以一次写多项）")
    if extensions.is_enabled("phone"):
        lines.append("  手机码：打开小手机，在「我的」里改微信名、换头像")
    lines.append("  换绑码：以后要换个人群时用")
    lines += ["", "📋 发「我的」随时查看时间线、待回、数量这些自查。",
              "👥 发「玩家名单」看所有角色，「查看皮相墙」看大家的皮相。",
              "📝 发「格式」看短信、约会等指令怎么写。"]
    return "\n".join(lines)
