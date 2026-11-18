"""跑团骰子。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'dice',
 'name': '跑团骰子',
 'module': 'changri.plugins.dice',
 'settings_group': '跑团骰子',
 'nav': ['dice_lines_page'],
 'description': 'COC7 和 DND5e：检定、理智、成长、先攻、攻击，属性随角色保存。',
 'kind': 'official',
 'order': 103}
