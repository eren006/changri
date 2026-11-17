"""写信综（个性化模块）。"""

# 功能声明：设置平台「功能与设置」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'letters',
 'name': '写信综',
 'module': 'changri.plugins.letters',
 'settings_group': '写信综',
 'nav': [],
 'personal': True,
 'default_on': False,
 'description': '正式信件：玩家在个人群「发送信件」，写【收件人】【内容】，可选【日期】【附件】【署名】，收件人在个人群收到这封信（太长时画成信纸样式的图）。可以设每天封数、冷却、按字数给写信币赏金。道具「望远镜」抄录某人的下一封信，「羽毛笔」截下某人的下一封信改完再寄。写信币和两个道具第一次用到时自动建进物品库。收尾时信件清空。默认关闭。',
 'kind': 'official',
 'order': 191}
