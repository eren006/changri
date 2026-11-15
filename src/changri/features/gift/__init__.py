"""礼物。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'gift',
 'name': '礼物',
 'settings_group': '礼物',
 'links': [('reviews_page', '礼物复盘')],
 'description': '送礼、礼品店与图鉴、礼物公开播报。送礼冷却、每日次数在这里设置。',
 'kind': 'core',
 'order': 3}
