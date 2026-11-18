"""网页（另一个线程里的 Flask）要让机器人主线程的事件循环去发消息时用这里：
启动时记下主循环，网页里 run(协程) 就会在主循环里跑，并等结果。"""
import asyncio
import concurrent.futures

_loop: asyncio.AbstractEventLoop | None = None

STILL_RUNNING_TEXT = "机器人还在处理，可能已经发出去了。先别重复操作，过一两分钟刷新看看结果。"


class StillRunning(TimeoutError):
    """等超时了，但事情没取消、还在主循环里接着做（发了一半的消息不能半路掐掉）。
    界面上不能说成「失败」：玩家以为没发出去会再点一次，结果发两遍。"""

    def __init__(self, text: str = STILL_RUNNING_TEXT, future=None):
        super().__init__(text)
        self.future = future            # 还在跑的那件事（concurrent.futures.Future）：想等它的结果可以留着以后看


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def run(coro, timeout: float = 25):
    """在主循环里跑协程并返回结果；还没记下主循环（比如测试里）就在当前线程新开一个循环跑。
    超过 timeout 秒抛 StillRunning（协程继续跑完，不取消）。"""
    if _loop is not None and _loop.is_running():
        from .db import current_bot, use_bot
        app_id = current_bot()          # 跨线程不会带上「当前机器人」，这里手动带过去

        async def _in_bot():
            with use_bot(app_id):
                return await coro
        fut = asyncio.run_coroutine_threadsafe(_in_bot(), _loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            raise StillRunning(future=fut) from None
    return asyncio.run(coro)


def friendly_error(e: BaseException, prefix: str) -> str:
    """网页上给人看的出错文字：超时（还在做）单独说，别的是「prefix：原因」。"""
    if isinstance(e, StillRunning):
        return str(e)
    return f"{prefix}：{str(e)[:60]}"


def install_flask_handler(app, home: str = "/") -> None:
    """没被页面自己接住的 StillRunning：提示一句、回到上一页（拿不到上一页就回 home），不出 500。
    小手机设了 Referrer-Policy: no-referrer，浏览器一般不带上一页，所以给它传 home="/p"。"""
    from flask import flash, redirect, request

    @app.errorhandler(StillRunning)
    def _still_running(e):
        flash(str(e))
        return redirect(request.referrer or home)
