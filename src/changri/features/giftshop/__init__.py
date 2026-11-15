"""礼品店。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'giftshop',
 'name': '礼品店',
 'module': 'changri.plugins.giftshop',
 'settings_group': '礼品店',
 'nav': ['giftshop_page'],
 'default_on': False,
 'links': [('giftshop_page', '打开管理页')],
 'description': '管理员在后台上架礼物（有编号），玩家发「礼品店」看；送礼可以直接写编号，也可以开「只能送礼品店里的东西」（开了就只能写编号，不能自定义）。后台能导出、导入。属于「额外」，默认关闭；之前已经上架过礼物的会自动保持开启。',
 'kind': 'official',
 'order': 109,
 'url_prefixes': ['/giftshop']}
