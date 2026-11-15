"""季度、出场与 NPC。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'season',
 'name': '季度与游戏天数',
 'settings_group': None,
 'links': [('season_page', '打开管理页')],
 'description': '预订季度、开始和结束、筹备期、游戏天数（自动天数）、收尾清空。短信礼物、心动信的每日次数都按它算「今天」。',
 'kind': 'core',
 'order': 1}
