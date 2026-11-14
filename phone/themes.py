"""小手机外观（visual：颜色/圆角/字体）和用语（copy：「信息」「公开播报」这类词）。后台配用语，玩家只能换外观。"""
import json

PHONE_THEMES = {
    'modern': {'name':'现代', 'accent':'#6854bc', 'light':['#ffffff','#f0eff2','#111114','#707079'], 'dark':['#101014','#25252b','#f2f2f5','#a0a0aa'], 'radius':'18px', 'font':'sans-serif'},
    'classic': {'name':'中国古风', 'accent':'#934432', 'light':['#faf5e9','#eee4d2','#332b24','#796857'], 'dark':['#1d1915','#302820','#f0e5d0','#c0aa8e'], 'radius':'10px', 'font':'serif'},
    'scifi': {'name':'科幻', 'accent':'#196a88', 'light':['#edf6fa','#dcebf2','#163340','#516d7c'], 'dark':['#09161f','#142c3a','#dceff8','#90b4c6'], 'radius':'8px', 'font':'sans-serif'},
    'medieval': {'name':'欧洲中世纪', 'accent':'#793746', 'light':['#f7efdd','#eaddc2','#3c2d25','#7c6652'], 'dark':['#21191c','#38272c','#f1e2cb','#c2a58b'], 'radius':'12px', 'font':'serif'},
    'cloudpost': {'name':'云絮邮局', 'accent':'#c4566e', 'light':['#f8eed7','#efdcc9','#2b1f1d','#86695f'], 'dark':['#231a1c','#382a2d','#f6e9d6','#c3a79c'], 'radius':'6px', 'font':'serif'},
    'medici': {'name':'美第奇', 'accent':'#8c1d2b', 'light':['#efe2c9','#f8f0df','#373c4c','#6e604e'], 'dark':['#222731','#303746','#f4e8cf','#c5b89e'], 'radius':'8px', 'font':'serif'},
    'blush': {'name':'粉紫雾面', 'accent':'#c0548a', 'light':['#f6eef4','#f3e8f1','#4b4a5e','#8c84a0'], 'dark':['#1d1621','#2f2336','#f3e8f3','#b8a8bf'], 'radius':'20px', 'font':'sans-serif'},
    'republic': {'name':'民国', 'accent':'#315d50', 'light':['#f5f1e7','#e4e6dc','#24382f','#657266'], 'dark':['#141e1a','#26372e','#e7eee4','#a4b6a6'], 'radius':'6px', 'font':'serif'},
}
PHONE_COPY = {
    'modern': {'messages':'信息','public':'公开播报','moments':'朋友圈','new':'新信息','brand':'长日小手机系统','empty':'这里还很安静，新的消息会留在这里。'},
    'classic': {'messages':'尺素','public':'风闻','moments':'雅集','new':'寄尺素','brand':'长日尺素局','empty':'此间尚无来书，静候故人落笔。'},
    'scifi': {'messages':'通讯','public':'公共频道','moments':'动态频道','new':'建立通讯','brand':'长日通讯终端','empty':'暂无通讯记录，等待新的信号接入。'},
    'medieval': {'messages':'书信','public':'王国公告','moments':'旅人见闻','new':'寄出书信','brand':'长日信使公会','empty':'信匣尚空，信使仍在路上。'},
    'cloudpost': {'messages':'来信','public':'橱窗告示','moments':'窗外见闻','new':'落笔','brand':'云絮邮局','empty':'信箱里空空的，下一封还在路上。'},
    'medici': {'messages':'密函','public':'城邦告示','moments':'市集见闻','new':'寄出密函','brand':'美第奇信使行','empty':'暂无来信，信使尚在途中。'},
    'blush': {'messages':'信息','public':'公开播报','moments':'朋友圈','new':'新信息','brand':'长日小手机系统','empty':'这里还很安静，新的消息会留在这里。'},
    'republic': {'messages':'信件','public':'今日公报','moments':'街巷见闻','new':'写信','brand':'长日书信局','empty':'今日尚无来信，且候邮差叩门。'},
}


def phone_theme(config_json: str = "", picked: str = "") -> dict:
    """config_json 是管理员存的 {"visual","copy","words"}；picked 是玩家在本机选的外观（只换样子，不换用语）。"""
    try:
        config = json.loads(config_json or "{}")
    except (ValueError, TypeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    visual = config.get("visual", "modern")
    copy = config.get("copy", "modern")
    if visual not in PHONE_THEMES:
        visual = "modern"
    if copy not in PHONE_COPY:
        copy = "modern"
    if picked in PHONE_THEMES:
        visual = picked
    else:
        picked = ""
    words = dict(PHONE_COPY[copy])
    custom = config.get("words", {})
    if isinstance(custom, dict):
        for key in words:
            if isinstance(custom.get(key), str) and custom[key].strip():
                words[key] = custom[key].strip()[:100]
    return {"visual": visual, "copy": copy, "style": PHONE_THEMES[visual], "words": words, "custom": custom if isinstance(custom, dict) else {}, "picked": picked}
