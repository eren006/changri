"""短信。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'social',
 'name': '短信',
 'settings_group': '短信',
 'links': [('reviews_page', '短信复盘')],
 'description': '短信、署名、竖着写、撤回、拉群引导。冷却、每日次数、字数上限、开放时段都在这里设置。',
 'kind': 'core',
 'order': 2}
