"""出场。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'entrance',
 'name': '出场',
 'module': 'changri.plugins.entrance',
 'settings_group': None,
 'nav': [],
 'links': [('season_page', '出场管理（在季度页里）')],
 'description': '抽出场顺序，在戏群里依次出场，支持提前预存和带图片。',
 'kind': 'official',
 'order': 100}
