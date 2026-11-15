"""网页（另一个线程里的 Flask）要让机器人主线程的事件循环去发消息时用这里：
启动时记下主循环，网页里 run(协程) 就会在主循环里跑，并等结果。"""
import asyncio

_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def run(coro, timeout: float = 25):
    """在主循环里跑协程并返回结果；还没记下主循环（比如测试里）就在当前线程新开一个循环跑。"""
    if _loop is not None and _loop.is_running():
        from .db import current_bot, use_bot
        app_id = current_bot()          # 跨线程不会带上「当前机器人」，这里手动带过去

        async def _in_bot():
            with use_bot(app_id):
                return await coro
        return asyncio.run_coroutine_threadsafe(_in_bot(), _loop).result(timeout)
    return asyncio.run(coro)
