"""心动信。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'lovemail',
 'name': '心动信',
 'module': 'changri.plugins.lovemail',
 'settings_group': '心动信',
 'nav': ['lovemail_page'],
 'links': [('lovemail_page', '打开管理页')],
 'description': '匿名投递、定时统一派送、撤回、曝光、信箱统计。',
 'kind': 'official',
 'order': 101,
 'url_prefixes': ['/lovemail']}
