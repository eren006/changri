"""组装并启动：机器人（NoneBot）+ 设置平台 + 小手机。导入本模块就完成组装，run() 开始服务。"""
import os

import nonebot  # noqa: E402
from nonebot.adapters.qq import Adapter as QQAdapter

from changri.core import qq_compat  # noqa: E402

qq_compat.apply()                           # 适配器兼容：回复合并转发的消息不再被丢掉

from changri.core.botconf import qq_bots_config
from changri.core.db import init_db

init_db()                                   # 先建库（会把旧 .env 里的机器人导入一次），再按库里的机器人启动
from changri import config  # noqa: E402
from changri.paths import DATA_DIR  # noqa: E402

config.ensure()                             # 第一次启动：建 data/config.toml（有老 .env 就从里面导入）
_cfg = config.load()
os.environ.setdefault("DRIVER", "~fastapi+~httpx+~websockets")   # QQ 适配器要 HTTP 客户端 + WebSocket
nonebot.init(_env_file=str(DATA_DIR / "nonebot.env"),          # 不读当前目录的 .env；这个文件一般不存在，高级用户可以放 NoneBot 自己的配置
             host=_cfg["host"], port=_cfg["port"], log_level=_cfg["log_level"],
             qq_bots=qq_bots_config(), command_start={"/", ".", "。", ""})     # 斜杠、英文点、中文句号都能当指令前缀

driver = nonebot.get_driver()
driver.register_adapter(QQAdapter)

# 多机器人：每个机器人一套独立数据库。QQ 事件进来时，先把「当前机器人」设成收到消息的那个，后面所有指令里读写的都是它的数据。
from nonebot.adapters.qq import Bot as _QQBot  # noqa: E402

from changri.core.db import use_bot  # noqa: E402

_orig_handle_event = _QQBot.handle_event


async def _handle_event_in_workspace(self, event):
    with use_bot(self.self_id):
        await _orig_handle_event(self, event)


_QQBot.handle_event = _handle_event_in_workspace

from changri.core import extensions  # noqa: E402

extension_failures = extensions.load_all()     # 核心 + 开着的官方扩展 + 开着的用户扩展（extensions/ 文件夹）

# 设置平台（Flask）挂在同一个进程、同一个端口上：用户只需要启动这一个程序，在本机浏览器打开它的地址。
from a2wsgi import WSGIMiddleware  # noqa: E402

from changri.admin import create_app  # noqa: E402

admin_app = create_app()
from changri.admin.remote import AdminGate, LocalProxyFix  # noqa: E402

# 「远程访问」开了就只放名单里的 IP，和后台同一个端口；LocalProxyFix 让经 Caddy（HTTPS）转来的请求认出真实来源 IP
nonebot.get_app().mount("/", WSGIMiddleware(LocalProxyFix(AdminGate(admin_app))))

# 小手机（玩家用的网页）：另开一个公网端口，和设置平台分开监听；开关和端口在设置平台改完立刻生效（core/phoneserver.py）。
def _start_phone_server() -> None:
    from changri.core import phoneserver
    phoneserver.start_all()


async def _remember_loop() -> None:
    import asyncio

    from changri.core import loopbridge
    loopbridge.set_loop(asyncio.get_running_loop())     # 网页要让机器人主线程发消息时用


async def _start_qq_links() -> None:
    from changri.core import qqmanager
    await qqmanager.start_all()


def _start_errlog() -> None:
    from changri.core import errlog
    errlog.install()                                     # 记报错、查上次是不是崩溃退出；有接收地址就发出去


driver.on_startup(_start_errlog)


def _mark_clean_exit() -> None:
    # systemctl restart 发的是 SIGTERM，uvicorn 收尾后会用同一个信号再杀自己，atexit 来不及跑，
    # 结果每次重启都被记成「崩溃」。所以在 NoneBot 收尾的这一步就把「正常退出」标记写好。
    from changri.core import errlog
    errlog.mark_clean_exit()


driver.on_shutdown(_mark_clean_exit)
driver.on_startup(_remember_loop)
driver.on_startup(_start_qq_links)
driver.on_startup(_start_phone_server)

def run() -> None:
    nonebot.run()
