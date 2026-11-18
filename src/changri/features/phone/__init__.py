"""小手机。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'phone',
 'name': '小手机',
 'module': 'changri.plugins.phone',
 'settings_group': None,
 'nav': ['phone_page'],
 'default_on': True,
 'links': [('phone_page', '打开小手机设置')],
 'description': '玩家用网页手机收发短信礼物、看公开播报、换外观；群里发「手机码」领激活码。要单独开一个端口（默认 5092）给玩家访问，记得在云服务商的安全组放行。0.22.4 起默认打开；这里和「小手机」页的开关是同一个，关掉后玩家就打不开了。',
 'kind': 'official',
 'order': 108,
 'url_prefixes': ['/phone']}
