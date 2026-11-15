"""混乱寄信。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'chaos_letter',
 'name': '混乱寄信',
 'module': 'changri.plugins.chaosletter',
 'settings_group': '混乱寄信',
 'nav': [],
 'default_on': False,
 'description': '在「信件」的基础上，让寄出的信可能错字、丢后半截、涂黑、句子乱序、落款写错、误投给别人、被撕成两半（后半页飘到第三个人手里）。每种概率在设置里调。每日次数、冷却、公开播报在「信件」里。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 111}
