"""物品、属性、地点与商城。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'rpg',
 'name': '物品与商城',
 'module': 'changri.plugins.rpg',
 'settings_group': None,
 'nav': ['rpg_page'],
 'links': [('rpg_page', '打开管理页')],
 'description': '物品库、角色背包、货币商城、属性效果；背包、商城、购买、使用、赠送道具、兑换、合成指令。属于「额外」，平时可以不开。',
 'kind': 'official',
 'order': 105,
 'url_prefixes': ['/rpg']}
