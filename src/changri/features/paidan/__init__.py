"""排单（个性化模块）。"""

# 功能声明：设置平台「功能与设置」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
# 后台页面在左侧「额外 → 附属功能」（base.html），只有开着才显示；群指令和定时任务在 plugins/paidan.py。
FEATURE = {'id': 'paidan',
 'name': '排单',
 'module': 'changri.plugins.paidan',
 'settings_group': '',
 'nav': ['paidan_page'],
 'personal': True,
 'default_on': False,
 'links': [('paidan_page', '打开排单')],
 'description': '给接稿用的排单：业务和工作群、客户额度和单口、散单兑换码；客户在群里按格式下单、补图，'
                'owner 在工作群里发送交付，到网页上扣张数；每天提醒额度到期和工期，可以按天暂停并自动顺延。'
                '不读写恋综的数据。默认关闭，关掉后数据保留；每次「全部恢复默认设置」也会关回去。',
 'kind': 'official',
 'order': 195,
 'url_prefixes': ['/paidan']}
