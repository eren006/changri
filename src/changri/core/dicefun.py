"""跑团小工具：随机人名、随机排序、牌堆抽取、今日人品。不依赖 QQ，可单独测试。
名字表、遭遇表、塔罗关键词都是自己写的。"""
from __future__ import annotations

import hashlib
import random
import re
import time

MAX_NAMES = 10
MAX_WHO = 30
MAX_DRAW = 10

# ======================== 随机人名 ========================

CN_SURNAMES = ("王 李 张 刘 陈 杨 黄 赵 周 吴 徐 孙 马 朱 胡 林 郭 何 高 罗 郑 梁 谢 宋 唐 许 韩 冯 邓 曹 "
               "彭 曾 萧 田 董 潘 袁 蔡 蒋 余 于 杜 叶 程 魏 苏 吕 丁 沈 任 姚 卢 傅 钟 姜 崔 谭 陆 范 汪 "
               "欧阳 司马 上官 诸葛 慕容").split()
CN_GIVEN_M = "伟 强 磊 军 洋 勇 杰 涛 明 超 辉 刚 平 建 峰 鹏 宇 浩 文 博 志 斌 振 立 德 承 远 晨 默 川 岩 骁".split()
CN_GIVEN_F = "芳 娜 敏 静 丽 艳 娟 霞 婷 雪 慧 颖 琳 晴 岚 悦 宁 薇 月 瑶 洁 蓉 欣 怡 婉 清 柔 素 梅 菲 漪 筠".split()
CN_GIVEN_ANY = "安 然 思 若 子 一 之 言 景 书 云 秋 白 青 和 星 林 舟 知 闻 墨 溪 远 意".split()

EN_FIRST_M = ("James John Robert Michael William David Richard Joseph Thomas Charles Daniel Matthew Henry "
              "Arthur Edward Walter Harold Frank Samuel Lewis Albert George Oliver Jack").split()
EN_FIRST_F = ("Mary Patricia Jennifer Linda Elizabeth Barbara Susan Jessica Sarah Margaret Dorothy Helen "
              "Alice Emma Clara Ruth Evelyn Grace Florence Irene Edith Lucy Agnes Rose").split()
EN_SURNAMES = ("Smith Johnson Williams Brown Jones Miller Davis Wilson Anderson Taylor Thomas Moore Martin "
               "Jackson White Harris Clark Lewis Walker Hall Allen Young King Wright Hill Scott Green Baker "
               "Carter Mitchell Parker Collins Morgan Bell Murphy Cooper Ward Fletcher Whitmore Ashford").split()

JP_SURNAMES = ("佐藤 铃木 高桥 田中 伊藤 渡边 山本 中村 小林 加藤 吉田 山田 佐佐木 山口 松本 井上 木村 林 "
               "清水 山崎 森 池田 桥本 阿部 石川 前田 藤田 小川 冈田 长谷川 村上 近藤 远藤 青木 坂本").split()
JP_GIVEN_M = "翔太 大辅 健一 拓也 隆 诚 和也 浩二 直树 雄一 修 达也 优斗 莲 悠真 苍 阳翔 湊 春树 晴".split()
JP_GIVEN_F = "阳子 惠美 由美 直美 美穗 爱 舞 彩 千寻 葵 结衣 凛 美咲 樱 栞 七海 遥 琴音 纱希 明日香".split()

_KIND = {"cn": "cn", "中": "cn", "中文": "cn", "中国": "cn", "en": "en", "英": "en", "英文": "en", "英国": "en",
         "美国": "en", "jp": "jp", "日": "jp", "日文": "jp", "日本": "jp"}
_SEX = {"男": "m", "m": "m", "male": "m", "女": "f", "f": "f", "female": "f"}


def _one_name(kind: str, sex: str) -> str:
    s = sex or random.choice("mf")
    if kind == "en":
        return f"{random.choice(EN_FIRST_M if s == 'm' else EN_FIRST_F)} {random.choice(EN_SURNAMES)}"
    if kind == "jp":
        return random.choice(JP_SURNAMES) + random.choice(JP_GIVEN_M if s == "m" else JP_GIVEN_F)
    pool = (CN_GIVEN_M if s == "m" else CN_GIVEN_F) + CN_GIVEN_ANY
    given = random.choice(pool) + (random.choice(pool) if random.random() < 0.7 else "")
    return random.choice(CN_SURNAMES) + given


def names(raw: str) -> str:
    """/name [中|英|日] [数量] [男|女]，顺序随意。"""
    kind, sex, n = "cn", "", 5
    for t in raw.lower().split():
        if t in _KIND:
            kind = _KIND[t]
        elif t in _SEX:
            sex = _SEX[t]
        elif t.isdigit():
            n = int(t)
        else:
            return "格式：/name [中|英|日] [数量] [男|女]，例：/name 日 3 女"
    if not 1 <= n <= MAX_NAMES:
        return f"一次最多 {MAX_NAMES} 个。"
    seen: list[str] = []
    for _ in range(n * 5):
        name = _one_name(kind, sex)
        if name not in seen:
            seen.append(name)
        if len(seen) >= n:
            break
    return "随机名字：" + "、".join(seen)


# ======================== 随机排序 ========================

def who(raw: str) -> str:
    """/who 甲 乙 丙：把写的人随机排个顺序。"""
    items = [t for t in re.split(r"[\s,，、]+", raw.strip()) if t]
    if len(items) < 2:
        return "格式：/who 甲 乙 丙（至少两个，空格或逗号隔开）"
    if len(items) > MAX_WHO:
        return f"最多 {MAX_WHO} 个。"
    random.shuffle(items)
    return "随机顺序：" + " → ".join(f"{i}.{x}" for i, x in enumerate(items, 1))


# ======================== 牌堆 ========================

TAROT = [  # (牌名, 正位关键词, 逆位关键词)
    ("愚者", "出发、冒险、无所顾忌", "鲁莽、迷失方向"), ("魔术师", "行动力、手段、开始", "欺骗、空谈"),
    ("女祭司", "直觉、秘密、静观", "隐瞒、判断失灵"), ("皇后", "丰饶、照料、感受", "依赖、停滞"),
    ("皇帝", "秩序、权威、掌控", "专横、失控"), ("教皇", "传统、指引、承诺", "教条、离经叛道"),
    ("恋人", "选择、结合、心意相通", "犹豫、关系失衡"), ("战车", "前进、意志、胜利", "冲动、受阻"),
    ("力量", "勇气、耐心、驯服", "软弱、自我怀疑"), ("隐士", "独处、探寻、内省", "孤立、逃避"),
    ("命运之轮", "转机、循环、运气", "厄运、错失时机"), ("正义", "公平、因果、裁决", "偏颇、不公"),
    ("倒吊人", "等待、换个角度、牺牲", "白费、拖延"), ("死神", "结束、告别、转变", "抗拒改变、拖着不放"),
    ("节制", "调和、平衡、节奏", "过度、失调"), ("恶魔", "欲望、束缚、诱惑", "挣脱、清醒"),
    ("塔", "突变、崩塌、真相揭开", "灾祸将至、勉强维持"), ("星星", "希望、疗愈、方向", "失望、迷茫"),
    ("月亮", "不安、幻象、潜意识", "迷雾散去、恐惧减轻"), ("太阳", "明朗、成功、活力", "短暂的低落"),
    ("审判", "觉醒、召唤、清算", "悔恨、拒绝面对"), ("世界", "完成、圆满、归来", "未竟、差一步"),
]
_SUITS = "♠♥♣♦"
_RANKS = "A 2 3 4 5 6 7 8 9 10 J Q K".split()
POKER = [s + r for s in _SUITS for r in _RANKS] + ["小王", "大王"]
ENCOUNTERS = [
    "路边停着一辆熄火的车，车门半开，没有人。", "有人从背后叫了你的名字，回头却只有风。",
    "一个迷路的小孩拉住你的衣角，问你家在哪。", "报摊上的报纸头条，写的是明天的日期。",
    "窗户里有人在看你，你一抬头，窗帘拉上了。", "一只黑猫横穿马路，停下来回头看了你一眼。",
    "地上有一串湿脚印，一直延伸到墙根就断了。", "收音机里断断续续地播着一段没人听过的旋律。",
    "一个醉汉拦住你，说他见过你，在很多年前。", "街角的电话亭响了，周围只有你一个人。",
    "门缝里塞着一张字条，字迹潦草：别信他。", "一阵浓雾毫无预兆地漫上来，三步外看不清人。",
    "有人遗落了一本笔记，最后一页写着你的住址。", "远处传来教堂钟声，可这附近并没有教堂。",
    "一位老妇人送你一枚旧硬币，说迟早用得上。", "巡警盘问你去哪，看了你的证件后脸色变了。",
    "某户人家的灯一直在有规律地闪，像在打信号。", "你闻到一股海腥味，而这里离海很远。",
    "一辆车从你身边驶过，车里的人和你长得一模一样。", "一切如常，平静得让人不安。",
]
DECKS = {"塔罗": "tarot", "tarot": "tarot", "扑克": "poker", "poker": "poker", "遭遇": "enc", "随机遭遇": "enc"}
DECK_TITLES = {"tarot": "塔罗（大阿卡纳 22 张，含正逆位）", "poker": "扑克（54 张）", "enc": "随机遭遇（20 条）"}


def draw(raw: str) -> str:
    """/draw 牌堆名 [张数]：一次抽的几张不重复。"""
    parts = raw.split()
    if not parts:
        return "可以抽的牌堆：\n" + "\n".join(f"· {t}" for t in DECK_TITLES.values()) + "\n格式：/draw 塔罗 3"
    deck = DECKS.get(parts[0].lower())
    if not deck:
        return f"没有「{parts[0]}」这个牌堆。发 /draw 看有哪些。"
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    if not 1 <= n <= MAX_DRAW:
        return f"一次最多抽 {MAX_DRAW} 张。"
    if deck == "tarot":
        picks = random.sample(TAROT, n)
        lines = []
        for name, up, down in picks:
            rev = random.random() < 0.5
            lines.append(f"{name}（{'逆位' if rev else '正位'}）：{down if rev else up}")
        return "抽到：\n" + "\n".join(lines)
    if deck == "poker":
        return "抽到：" + " ".join(random.sample(POKER, n))
    return "随机遭遇：\n" + "\n".join(random.sample(ENCOUNTERS, n))


# ======================== 今日人品 ========================

JRRP_REMARKS = [(1, "……今天还是待在家里吧。"), (10, "不太顺，做事多留个心眼。"), (30, "平平淡淡。"),
                (60, "还不错。"), (85, "手气很好，适合掷骰。"), (99, "今天就是你的日子。"), (100, "满分！")]


def jrrp_value(key: str, day: str | None = None) -> int:
    """同一个人同一天固定的 1~100。key 一般是 机器人|成员。"""
    day = day or time.strftime("%Y-%m-%d")
    return int(hashlib.sha256(f"jrrp|{key}|{day}".encode()).hexdigest(), 16) % 100 + 1


def jrrp(key: str, who: str = "你", day: str | None = None) -> str:
    v = jrrp_value(key, day)
    remark = next(r for top, r in JRRP_REMARKS if v <= top)
    return f"{who}今天的人品值是 {v}。{remark}"
