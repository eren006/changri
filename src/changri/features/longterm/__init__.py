"""长期群（第三种群模式，个性化模块）：后台页面在 admin.py，群指令在 plugins/longterm.py。"""

# 功能声明：由 features/registry.py 收集。放在「功能与设置 → 个性化模块」，默认关；
# 关着时：左侧菜单不显示「长期群」一节、/longterm 页面 404、群指令不加载、/模式 长期群 切不过去（core/trpg.get_mode 退回恋综）。
FEATURE = {'id': 'longterm',
 'name': '长期群',
 'module': 'changri.plugins.longterm',
 'settings_group': '',
 'nav': ['longterm_page'],
 'personal': True,
 'default_on': False,
 'links': [('longterm_page', '打开长期群')],
 'description': '恋综、跑团之外的第三种群模式，没有季度和时间线：后台建长期群，QQ 群里 /模式 长期群 接上。'
                '玩家注册、回复合并转发交字数换货币（可以要管理员批准）、属性面板（/ra /rt 读它）、背包和赠送、签到、'
                '商城（价格、库存、限购）、合成兑换；管理员群里 /调整 /发放，后台看动态和每一笔流水。'
                '不读写恋综和跑团的数据。默认关闭，关掉后数据保留。',
 'kind': 'official',
 'order': 196,
 'url_prefixes': ['/longterm']}
