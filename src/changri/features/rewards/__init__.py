"""结戏奖励。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'rewards',
 'name': '结戏奖励',
 'module': 'changri.plugins.rewards',
 'settings_group': None,
 'nav': ['rewards_page'],
 'default_on': False,
 'links': [('rewards_page', '打开管理页')],
 'description': '房间正常结束时，按每人这一场的段数、字数、平均字数、时长发奖励（固定物品/货币/属性，或按权重随机抽），自动发到个人群；后台可以补发、看每场状态、调整某人的场次数据。依赖「物品与商城」里的物品。属于「额外」，默认关闭，只在后台配置。',
 'kind': 'official',
 'order': 107,
 'url_prefixes': ['/rewards']}
