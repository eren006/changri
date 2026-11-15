"""地点。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'places',
 'name': '地点',
 'module': 'changri.plugins.places',
 'settings_group': '地点',
 'nav': ['rpg_places_page'],
 'links': [('rpg_places_page', '打开管理页')],
 'description': '地点列表、带钥匙的地点、「地点」指令；可以再开「只能使用预设地点约会」。属于「额外」，平时可以不开。',
 'kind': 'official',
 'order': 118,
 'url_prefixes': ['/places']}
