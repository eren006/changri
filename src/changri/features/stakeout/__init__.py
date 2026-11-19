"""踩点。"""

# 功能声明：设置平台「扩展」页的卡片、开关默认值、设置组。由 features/registry.py 收集。
# 指令「踩点」写在核心的 plugins/play/launch.py 里（和约会共用房间逻辑），所以 module 为空；开关关着时指令直接回「踩点没有开放」，建房间那一层也会拦。
FEATURE = {'id': 'stakeout',
 'name': '踩点',
 'module': '',
 'settings_group': '踩点',
 'nav': [],
 'default_on': False,
 'description': '玩家发「踩点 时间 地点」开一个只有自己的群：占时间线、要写地点，结束发「结束踩点」。关着时没办法发起踩点。每人每天上限在「踩点」设置里。属于「额外」，默认关闭；以前在「约会房间」设置里打开过踩点的，升级后保持开启。',
 'kind': 'official',
 'order': 105}
