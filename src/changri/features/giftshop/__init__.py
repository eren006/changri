"""礼品店。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'giftshop',
 'name': '礼品店',
 'module': 'changri.plugins.giftshop',
 'settings_group': '礼品店',
 'nav': ['giftshop_page'],
 'default_on': False,
 'links': [('giftshop_page', '打开管理页')],
 'description': '管理员在后台上架礼物（有编号）；玩家发「礼品店」逛一逛，随机拿到一件还没有的收进图鉴（有冷却，可能顺手多捡一件），「图鉴」看收集进度；图鉴里的礼物送礼写编号（#003）可以无限送，也可以开「只能送礼品店里的东西」（开了就不能自定义）。后台能导出、导入。属于「额外」，默认关闭；之前已经上架过礼物的会自动保持开启。',
 'kind': 'official',
 'order': 109,
 'url_prefixes': ['/giftshop']}
