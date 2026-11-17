"""约会。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'play',
 'name': '约会',
 'settings_group': '约会房间',
 'links': [('backup_pool_page', '官方机器人备用群池（高级）'), ('reviews_page', '复盘')],
 'description': '约会、电话、微信、官约、备用群池分配房间、时间线、我的待回。心愿是单独的可开关扩展。',
 'kind': 'core',
 'order': 5}
