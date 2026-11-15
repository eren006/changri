"""前置电话。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'precall',
 'name': '前置电话',
 'module': 'changri.plugins.precall',
 'settings_group': '前置电话',
 'nav': ['precall_admin'],
 'default_on': False,
 'links': [('precall_admin', '打开管理页')],
 'description': '独立于小手机。管理员打开后，玩家「提交前置电话 内容」，每条有全局唯一编号；刚交的都是未分组，管理员在后台手动归成组，再设「可选 A组」，那组的前置电话才会出现在「查看前置电话」里，玩家「拨打前置电话 '
                '12」自动开一个电话房间。可以设每人主动拨出上限和总通话上限（0 = 不限）。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 116,
 'url_prefixes': ['/precall']}
