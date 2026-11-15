"""公知、二表与关系线。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'relations',
 'name': '公知、二表与关系线',
 'module': 'changri.plugins.relations',
 'settings_group': '关系线',
 'nav': ['relations_page'],
 'links': [('relations_page', '打开管理页')],
 'description': '拉线、确认、撤回、查看、统计。',
 'kind': 'official',
 'order': 102,
 'url_prefixes': ['/relations', '/sheets']}
