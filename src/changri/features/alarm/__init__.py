"""加百列闹钟。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'alarm',
 'name': '加百列闹钟',
 'module': 'changri.plugins.alarm',
 'settings_group': None,
 'nav': ['alarm_page'],
 'default_on': False,
 'links': [('alarm_page', '群体闹钟（只能在这里设）')],
 'description': '提醒、再提醒我、我的提醒、删除提醒：到点在个人群 @ 本人；支持每天、每周重复。群体闹钟（到点给每个玩家的个人群发提醒）只能在后台页面里设。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 112,
 'url_prefixes': ['/alarm']}
