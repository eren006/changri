"""论坛。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'forum',
 'name': '论坛',
 'module': 'changri.plugins.forum',
 'settings_group': None,
 'nav': ['forum_page'],
 'links': [('forum_page', '打开管理页')],
 'description': '群里发帖、回复、点赞点踩、查看帖子，管理员删帖删楼层；开放时段在「约会房间」设置里。属于「额外」，关掉后指令不再响应、侧边栏自动折叠。',
 'kind': 'official',
 'order': 104,
 'url_prefixes': ['/forum']}
