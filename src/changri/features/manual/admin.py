"""设置平台 · 使用手册：想做一件事去哪里设，每项设置是干嘛的。"""
from flask import render_template, request, url_for

from changri.core.db import get_config

TAB_NAMES = {"core": "核心功能", "aux": "辅助功能", "custom": "个性化模块"}

# 左侧菜单里的页面 → （菜单里的小标题，页面名）。跟 admin/templates/base.html 的菜单对应（test_manual 会查）
NAV = {
    "dashboard": ("", "总览"),
    "backup_pool_page": ("约会房间的群", "备用群池"), "qqlink_page": ("约会房间的群", "QQ 小号与群号组"),
    "noat_page": ("通用", "不用 @ 也能回"), "autoreply_page": ("通用", "自定义回复"), "dice_lines_page": ("通用", "个性化骰子"),
    "extensions_page": ("通用", "功能与设置"), "admins_page": ("通用", "QQ 管理员"),
    "god_page": ("恋综", "角色"), "season_page": ("恋综", "季度与出场"), "lovemail_page": ("恋综", "心动信"),
    "relations_page": ("恋综", "公知、二表与关系线"),
    "phone_page": ("额外", "小手机"), "giftshop_page": ("额外", "礼品店"), "rpg_places_page": ("额外", "地点"),
    "bots_page": ("全部机器人共用", "机器人接入"), "accounts_page": ("全部机器人共用", "账号"), "remote_page": ("全部机器人共用", "远程访问"),
    "https_page": ("全部机器人共用", "HTTPS"), "update_page": ("全部机器人共用", "更新"),
}

# 「我想……」：(问题, 去哪里, 补充说明)。去哪里：("set", 设置项) / ("page", 页面, {参数}) / ("qq", [群里发的指令])
TASKS = [
    ("开季前准备", "", [
        ("让机器人连上 QQ / 机器人一直不在线", ("page", "bots_page", {}),
         "填好 AppID 和 AppSecret 后点「检查连接」，它会告诉你要把哪个 IP 加进 QQ 开放平台的 IP 白名单。"),
        ("让别人也能在群里用管理员指令", ("page", "admins_page", {}), ""),
        ("给别人开一个后台账号", ("page", "accounts_page", {}), "只有主账号能开。"),
        ("设公告群、后台群、水群", ("qq", ["设为公告群", "设为后台群", "设为水群"]),
         "在要用的那个群里发（机器人要在群里）。后台群收系统提醒和复盘网页链接；没设后台群，这些提醒就收不到。"),
        ("设戏群", ("qq", ["设为戏群"]), "在要用的那个群里发。挂心愿时会发到戏群。"),
        ("准备开约会房间用的群", ("page", "backup_pool_page", {}),
         "在每个空群里发「设为备用群池」把它放进池子。池子里的群用完时，玩家会被告知这次没开成，不会排队。"),
        ("建角色、看谁绑了哪个个人群", ("page", "god_page", {}), "玩家在自己的个人群里发「创建新角色 名字」，就会出现在这里。"),
        ("预订季度、开始和结束、推进游戏天数", ("page", "season_page", {}), ""),
        ("给玩家开小手机", ("page", "phone_page", {}),
         "还要在云服务商的安全组放行小手机端口（默认 5092），然后点页面上的「检查玩家能不能打开」。"),
        ("打开或关掉某个玩法（心愿、礼品店、目击……）", ("page", "extensions_page", {"tab": "aux"}),
         "每张卡片左边是开关。关着的功能，指令不响应、后台页面也不显示。开关以后点右上角「重启并生效」。"),
    ]),
    ("调节奏和上限", "", [
        ("短信冷却、每天能发几条、最多几个字", ("set", "sms_cooldown_sec"), "同一个设置框里还有每天条数、字数上限、能不能带图。"),
        ("规定几点到几点能玩", ("set", "window_sms"),
         "开放时段分散在各组：短信礼物在「短信」，约会电话微信和论坛在「约会」，心愿在「心愿」，心动信在「心动信」。总览「快捷开关 → 开放时段」能一次看全。"),
        ("约会最短多长、每人每天能约几次", ("set", "min_minutes_约会"), "电话、微信、每天次数也在同一个设置框里。"),
        ("不让一个人同时开太多房间", ("set", "date_open_cap"), "电话单独算，是同一个设置框里的「每人最多同时有几个没结束的电话」。"),
        ("房间开太久自动结束", ("set", "room_expire_enabled"), "打开后再填结戏限时和提前提醒几小时。"),
        ("轮到某人回、太久没回时提醒 TA", ("set", "overdue_phone_hours"), "电话和其他房间分开设，夜里可以设免打扰时段。"),
        ("送礼冷却、每天能送几次", ("set", "gift_cooldown_sec"), ""),
        ("只能送礼品店里的礼物", ("set", "gift_only_preset"), "上架礼物在左侧「礼品店」页。"),
        ("限制关系线写多长", ("set", "max_detail_chars"), ""),
        ("每人最多挂几条心愿", ("set", "wish_max_concurrent"), ""),
        ("哪些房间可以申请加入", ("set", "join_kinds"), ""),
        ("约会只能选建好的地点", ("set", "place_system_enabled"), "地点在左侧「地点」页建，可以设钥匙。"),
        ("前置电话每人每轮选几个人", ("set", "precall_max_picks"), ""),
    ]),
    ("心动信", "", [
        ("打开或关上信箱（玩家现在能不能投）", ("page", "lovemail_page", {}), "总览「快捷开关」里也能开关。"),
        ("每天几点派送", ("set", "lovemail_delivery_time"), ""),
        ("每人每天能投几封、某一天单独设", ("set", "lovemail_default_limit"), "按天单独设的写法是 D0:3 D1:5。"),
        ("心动信可能被曝光到公告群", ("set", "lovemail_expose"), ""),
        ("心动信可以附图", ("set", "lovemail_image_enabled"), ""),
    ]),
    ("群里自动发的消息", "总览「快捷开关 → 群里会自动发什么」能一次看全、直接开关。", [
        ("「第 N 次××记录」发太多 / 不想发", ("set", "announce_frequency"), "填 0 就不发；每一类单独设在「高级」里。"),
        ("礼物公开播报", ("set", "gift_public_send"), ""),
        ("挂心愿时发到公告群 / 戏群", ("set", "wish_public_send"), ""),
        ("短信出岔子（错字、误投、撕成两半）", ("set", "chaos_typo"), "每种岔子各有一个概率。"),
        ("房间结束时自动把戏文发给参与者", ("set", "room_auto_review"), ""),
        ("季末报告和收尾前存档提醒", ("set", "season_report"), ""),
        ("心动信派送后发一条指令提示", ("set", "daily_tip"), ""),
    ]),
    ("玩法里的概率", "", [
        ("约会被别人撞见（目击）的概率", ("set", "sighting_chance"), ""),
        ("追踪器这类特殊道具的成功率", ("set", "item_tracker_success_rate"), ""),
        ("允许偷礼物", ("set", "gift_steal_enabled"), "失手、带走、被发现的概率都在同一个设置框里。"),
    ]),
    ("机器人怎么回话", "", [
        ("指令必须带 / 才响应", ("set", "require_prefix"), ""),
        ("群里不用 @ 机器人也能回", ("page", "noat_page", {}), ""),
        ("关键词自动回复", ("page", "autoreply_page", {}), ""),
        ("长消息发成图片", ("set", "long_as_image"), ""),
        ("列表下面挂按钮", ("set", "qq_buttons"), "机器人没有 Markdown 和按钮权限时会自动改发文字。"),
        ("/ping 回什么", ("set", "hello_text"), ""),
        ("长图左上角写的名字", ("set", "brand_name"), ""),
        ("跑团默认用哪套规则", ("set", "dice_default_rule"), "群里也能用「切换规则」单独改。"),
        ("骰子回复的文案", ("page", "dice_lines_page", {}), ""),
    ]),
    ("系统和安全", "", [
        ("升级长日bot", ("page", "update_page", {}), "升级前会先备份所有数据；新版本起不来会自动退回。"),
        ("后台用 HTTPS 打开", ("page", "https_page", {}), ""),
        ("只让自己的电脑打开后台", ("page", "remote_page", {}), ""),
        ("留一个 QQ，出错时作者能联系你", ("set", "contact_qq"), ""),
        ("关掉匿名使用情况上报", ("set", "usage_report"), ""),
        ("把设置恢复成默认", ("page", "extensions_page", {}),
         "页面上方的「全部恢复默认设置」会覆盖你改过的所有设置，不能撤销；「补齐没设过的」只填空着的。"),
    ]),
]


def _cards():
    """所有带设置的功能卡片，按「功能与设置」里的顺序：[(卡片, 标签名, 开没开, [设置项…])]。"""
    from changri.admin.common import HIDDEN_FIELDS, SECTIONS, card_groups, field_view
    from changri.core import extensions
    sections = dict(SECTIONS)
    out = []
    cards = extensions.CORE_CARDS + [e for e in extensions.OFFICIAL if not e.get("personal")] + [e for e in extensions.OFFICIAL if e.get("personal")]
    for c in cards:
        groups = card_groups(c)
        if not groups:
            continue
        tab = "core" if c.get("kind") == "core" else ("custom" if c.get("personal") else "aux")
        fields = []
        for i, g in enumerate(groups):
            for k, (label, default) in sections[g].items():
                if k in HIDDEN_FIELDS:
                    continue
                f = field_view(k, label, default, default)
                now = field_view(k, label, default, get_config(k, default))
                f["default_text"] = _show(f)
                f["now_text"] = _show(now) if get_config(k, default) != default else ""
                f["sub"] = g if i and k == next(iter(sections[g])) else ""
                f["url"] = _set_url(k)
                fields.append(f)
        on = c.get("kind") == "core" or extensions.is_enabled(c["id"])
        out.append({"id": c["id"], "name": c["name"], "tab": tab, "tab_name": TAB_NAMES[tab], "on": on,
                    "description": c.get("description", ""), "fields": fields})
    return out


def _show(f: dict) -> str:
    v = str(f["value"]).strip()
    if f["kind"] == "bool":
        return "开" if f.get("checked") else "关"
    if f["kind"] == "choice":
        return dict(f["options"]).get(f["value"], v)
    if not v:
        return "空着"
    if f["kind"] == "lines":
        return v.replace("\n", " / ")
    unit = f.get("unit") or ("分钟" if f["kind"] == "number" and f.get("hint", "").startswith("分钟") else "")
    return v + (" " + unit if unit in ("分钟", "秒", "小时") else unit)


def _set_url(key: str) -> str:
    from changri.admin.quick import _page
    ep, kw = _page(key)
    return url_for(ep, **kw)


def _page_url(ep: str, kw: dict) -> str:
    from changri.admin.common import GLOBAL_ENDPOINTS
    u = url_for(ep, **kw)
    return u[len(request.script_root):] if ep in GLOBAL_ENDPOINTS else u     # 全部机器人共用的页面不带 /b/<机器人>/


def _tasks(cards):
    from changri.core import extensions
    where = {f["key"]: (c, f) for c in cards for f in c["fields"]}
    hidden = extensions.disabled_nav()
    out = []
    for title, lede, items in TASKS:
        rows = []
        for q, target, note in items:
            row = {"q": q, "note": note, "cmds": [], "url": "", "path": [], "warn": ""}
            if target[0] == "set":
                c, f = where[target[1]]
                row.update(url=f["url"], path=["功能与设置", c["tab_name"], c["name"], "进入设置", f["label"]], card=c["id"])
                if not c["on"]:
                    row["warn"] = f"「{c['name']}」现在关着：先在「功能与设置 → {c['tab_name']}」打开它，再点「重启并生效」。"
            elif target[0] == "page":
                _, ep, kw = target
                grp, label = NAV[ep]
                row.update(url=_page_url(ep, kw), path=[p for p in (grp, label) if p])
                if kw.get("tab"):
                    row["path"].append(TAB_NAMES[kw["tab"]])
                if ep in hidden:
                    row["warn"] = f"这个页面所属的功能现在关着：先在「功能与设置 → 辅助功能」打开它。"
            else:
                row.update(cmds=target[1], path=["QQ 群里"])
            rows.append(row)
        out.append({"title": title, "lede": lede, "rows": rows})
    return out


def register(app, login_required):
    @app.route("/manual")
    @login_required
    def manual_page():
        cards = _cards()
        return render_template("manual.html", tasks=_tasks(cards), cards=cards)
