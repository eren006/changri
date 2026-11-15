"""晚餐。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'dinner',
 'name': '晚餐',
 'module': 'changri.plugins.dinner',
 'settings_group': '晚餐',
 'nav': [],
 'default_on': False,
 'description': '管理员「开始晚餐 座位数 [风格]」开一桌，玩家「入座 座位号」，相邻座位（首尾也算）可以「戳你一下 角色名」，戳了对方个人群会收到提醒。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 113}
