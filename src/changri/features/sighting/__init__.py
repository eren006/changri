"""目击。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'sighting',
 'name': '目击',
 'module': 'changri.plugins.sighting',
 'settings_group': '目击',
 'nav': ['sighting_page'],
 'default_on': False,
 'links': [('sighting_page', '打开记录页')],
 'description': '建好约会/官约/心愿后，同一天同一地点、时间重叠够多的其他约会，双方成员各按概率「看见」对方：看见的人收到点名提示，被看见的人收到匿名或点名提示（可设）。独立于小手机，群里自动提示，没有指令；后台能看全部记录。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 115,
 'url_prefixes': ['/sightings']}
