"""混乱短信。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'chaos_letter',
 'name': '混乱短信',
 'module': 'changri.plugins.chaosletter',
 'settings_group': '混乱短信',
 'nav': [],
 'default_on': False,
 'description': '开了以后，玩家发的「短信」可能出岔子：错字、丢后半截、涂黑、句子乱序、落款写错、误投给别人、被撕成两半（后半截飘到第三个人手里）。每种概率和出岔子时要不要公开播报都在设置里调。每日次数、冷却照短信的规则。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 111}
