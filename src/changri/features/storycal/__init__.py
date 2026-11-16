"""故事历（个性化模块）。"""

# 功能声明：设置平台「功能与设置」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'storycal',
 'name': '故事历',
 'module': '',
 'settings_group': '',
 'nav': [],
 'personal': True,
 'default_on': False,
 'links': [('storycal_page', '故事历设置')],
 'description': '把一个 D（现实里的一天）平分成若干故事日，比如 D0 = 康熙十四年 3月1日–3月31日。只用来显示和写时间：换天公告和季度状态会带上故事日期，开房间时间只能写故事里的某一天，比如「3月5日」，不能再写钟点。心动信、每日上限、自动天数照旧按 D 和现实时间走。默认关闭，每次「全部恢复默认设置」也会关回去。',
 'kind': 'official',
 'order': 190,
 'url_prefixes': ['/storycal']}
