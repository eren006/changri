import os

from core.updater import startup_guard

_rolled_back = startup_guard()     # 刚更新完新版本又连续起不来：先回滚，再让服务管理器重新拉起
if _rolled_back:
    print(_rolled_back, flush=True)
    os._exit(3)

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter as QQAdapter

from core.botconf import qq_bots_config
from core.db import init_db

init_db()                                   # 先建库（会把旧 .env 里的机器人导入一次），再按库里的机器人启动
nonebot.init(qq_bots=qq_bots_config(), command_start={"/", ".", "。", ""})     # 斜杠、英文点、中文句号都能当指令前缀

driver = nonebot.get_driver()
driver.register_adapter(QQAdapter)

# 多机器人：每个机器人一套独立数据库。QQ 事件进来时，先把「当前机器人」设成收到消息的那个，后面所有指令里读写的都是它的数据。
from nonebot.adapters.qq import Bot as _QQBot  # noqa: E402

from core.db import use_bot  # noqa: E402

_orig_handle_event = _QQBot.handle_event


async def _handle_event_in_workspace(self, event):
    with use_bot(self.self_id):
        await _orig_handle_event(self, event)


_QQBot.handle_event = _handle_event_in_workspace

from core import extensions  # noqa: E402

extension_failures = extensions.load_all()     # 核心 + 开着的官方扩展 + 开着的用户扩展（extensions/ 文件夹）

# 设置平台（Flask）挂在同一个进程、同一个端口上：用户只需要启动这一个程序，在本机浏览器打开它的地址。
from a2wsgi import WSGIMiddleware  # noqa: E402

from admin import create_app  # noqa: E402

admin_app = create_app()
from admin.remote import AdminGate  # noqa: E402

nonebot.get_app().mount("/", WSGIMiddleware(AdminGate(admin_app)))      # 「远程访问」开了就只放名单里的 IP，和后台同一个端口

# 小手机（玩家用的网页）：另开一个公网端口，和设置平台分开监听；开关和端口在设置平台改完立刻生效（core/phoneserver.py）。
def _start_phone_server() -> None:
    from core import phoneserver
    phoneserver.start_all()


async def _remember_loop() -> None:
    import asyncio

    from core import loopbridge
    loopbridge.set_loop(asyncio.get_running_loop())     # 网页要让机器人主线程发消息时用


driver.on_startup(_remember_loop)
driver.on_startup(_start_phone_server)

if __name__ == "__main__":
    nonebot.run()
