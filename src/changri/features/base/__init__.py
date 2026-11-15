"""基础。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'base',
 'name': '基础',
 'settings_group': '基础',
 'links': [('dashboard', '总览')],
 'description': '角色与群绑定、/ping、欢迎语、拉黑、长内容发图、系统名称、报错接收。',
 'kind': 'core',
 'order': 0}
