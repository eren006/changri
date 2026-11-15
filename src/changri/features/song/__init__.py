"""点歌台。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组、左侧菜单、群指令模块。由 features/registry.py 收集。
FEATURE = {'id': 'song',
 'name': '点歌',
 'module': 'changri.plugins.song',
 'settings_group': '点歌',
 'nav': [],
 'default_on': False,
 'description': '回复一张网易云 / QQ 音乐的分享卡片发「点歌 [给 某人] [寄语]」，或「点歌 歌名 [歌手]」让机器人搜，B 站贴链接或 BV '
                '号；公告群收到一张点开就能听的图文卡片。不写点歌人就是匿名。每天次数在「点歌」设置里。要先设好公告群。属于「额外」，默认关闭。',
 'kind': 'official',
 'order': 114}
