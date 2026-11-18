"""心愿。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'wishes',
 'name': '心愿',
 'module': 'changri.plugins.wishes',
 'settings_group': '心愿',
 'nav': [],
 'default_on': False,
 'description': '挂心愿、看心愿、摘心愿、撤心愿；对外只显示性别和昵称，24 小时没人摘自动消失；被摘走后自动开一个心愿约会房间。开放时段、每人每天上限、公开播报在「心愿」设置里。属于「额外」，默认关闭，要用再打开。',
 'kind': 'official',
 'order': 106}
