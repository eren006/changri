"""漂流瓶。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'bottle',
 'name': '漂流瓶',
 'module': 'changri.plugins.bottle',
 'settings_group': '漂流瓶',
 'nav': ['bottles_page'],
 'links': [('bottles_page', '看漂流瓶记录')],
 'url_prefixes': ['/bottles'],
 'default_on': False,
 'description': '「漂流瓶 内容」把匿名的话随机送到某个玩家手里，对方用编号回信，一直能来回。NPC 不参与，会避开拉黑了你的人。后台「漂流瓶记录」能看到每个瓶子真正是谁扔的、谁捡到的。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 110}
