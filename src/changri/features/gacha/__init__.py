"""抽取池。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'gacha',
 'name': '抽取池',
 'module': 'changri.plugins.gacha',
 'settings_group': None,
 'nav': ['gacha_page'],
 'default_on': False,
 'links': [('gacha_page', '打开管理页')],
 'description': '后台建池子、放奖品（物品库里的物品，带权重，可标稀有）、设保底、发抽取次数；玩家用「抽取」「我的抽取次数」「我的保底」「查看池子」，管理员可以在群里「发放抽取」。抽取次数会显示在角色卡上。依赖「物品与商城」里的物品。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 117,
 'url_prefixes': ['/gacha']}
