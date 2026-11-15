"""小手机。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'phone',
 'name': '小手机',
 'module': 'changri.plugins.phone',
 'settings_group': None,
 'nav': ['phone_page'],
 'default_on': False,
 'links': [('phone_page', '打开小手机设置')],
 'description': '玩家用网页手机收发短信礼物、看公开播报、换外观；群里发「手机码」领激活码。要单独开一个端口给玩家访问。属于「额外」，不是必须打开的；之前已经开着小手机的，会自动保持开启。',
 'kind': 'official',
 'order': 108,
 'url_prefixes': ['/phone']}
