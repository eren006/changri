"""QQ 号连接层：连到 NapCat / LLBot（OneBot 11 正向 WebSocket），只做两件事——听群消息、改群名。

这一层不碰数据库、不发消息，只负责：保持连接（断了自动重连）、把群消息整理成 GroupMsg 交给回调、
提供 set_group_name / group_list / login_info。群号池、进 log 等逻辑在上层。设计见 docs/QQ号监听与改群名设计.md。
"""
import asyncio
import inspect
import itertools
import json
import logging
import time
from dataclasses import dataclass

log = logging.getLogger("qqlink")

ALLOWED_ACTIONS = {"set_group_name", "get_group_list", "get_login_info", "get_group_member_list", "send_group_msg"}     # 只放行这几个：不踢人、不禁言；send_group_msg 只给房间群「人到齐了」的提示用


class QQLinkError(Exception):
    pass


@dataclass
class GroupMsg:
    self_id: str
    group_id: str
    user_id: str
    nickname: str
    text: str
    time: int
    message_id: str


def segments_to_text(message) -> str:
    """OneBot 消息（字符串或消息段数组）转成纯文字；图片、表情等写成 [图片] 这样的占位。"""
    if isinstance(message, str):
        return message
    out = []
    for seg in message or []:
        kind, data = seg.get("type"), seg.get("data") or {}
        if kind == "text":
            out.append(str(data.get("text", "")))
        elif kind == "at":
            out.append("@" + str(data.get("name") or data.get("qq") or ""))
        elif kind == "image":
            out.append("[图片]")
        elif kind == "face":
            out.append("[表情]")
        elif kind in ("reply", "json", "xml"):
            continue
        else:
            out.append(f"[{kind}]")
    return "".join(out).strip()


def parse_group_message(ev: dict) -> GroupMsg | None:
    """不是群消息返回 None。"""
    if ev.get("post_type") != "message" or ev.get("message_type") != "group":
        return None
    sender = ev.get("sender") or {}
    nick = sender.get("card") or sender.get("nickname") or str(ev.get("user_id", ""))
    text = segments_to_text(ev.get("message")) or str(ev.get("raw_message", "")).strip()
    return GroupMsg(self_id=str(ev.get("self_id", "")), group_id=str(ev.get("group_id", "")),
                    user_id=str(ev.get("user_id", "")), nickname=nick, text=text,
                    time=int(ev.get("time") or time.time()), message_id=str(ev.get("message_id", "")))


class QQLink:
    def __init__(self, url: str, token: str = "", on_group_message=None, on_group_join=None):
        self.url, self.token = url, token
        self.on_group_message = on_group_message
        self.on_group_join = on_group_join          # fn(group_id, user_id)：有人进群（OneBot 的 group_increase 通知）
        self.self_id = ""                  # 连上后从事件里得到
        self.connected = False
        self.last_event = 0.0              # 最近一次收到任何事件的时间
        self.last_error = ""
        self.online: bool | None = None    # 心跳里带的「QQ 号是否在线」；没带就是 None（当作正常）
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seq = itertools.count(1)
        self._stop = False

    # ---------- 连接 ----------

    async def run(self) -> None:
        import websockets
        delay = 1.0
        while not self._stop:
            try:
                headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
                async with websockets.connect(self.url, additional_headers=headers, open_timeout=10, max_size=8 * 1024 * 1024) as ws:
                    self._ws, self.connected, self.last_error, delay = ws, True, "", 1.0
                    async for raw in ws:
                        await self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:                      # 断线、连不上、口令错：记下原因，退避后重连
                self.last_error = f"{type(e).__name__}: {e}"[:200]
            finally:
                self.connected, self._ws = False, None
                self._fail_pending()
            if self._stop:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def stop(self) -> None:
        self._stop = True
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())

    def _fail_pending(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(QQLinkError("连接断开了"))
        self._pending.clear()

    async def _handle(self, raw) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        echo = data.get("echo")
        if echo is not None and str(echo) in self._pending:          # API 的返回
            fut = self._pending.pop(str(echo))
            if not fut.done():
                fut.set_result(data)
            return
        self.last_event = time.time()
        if data.get("post_type") == "meta_event":
            status = data.get("status") or {}
            if "online" in status:
                self.online = bool(status["online"])
        if data.get("self_id"):
            self.self_id = str(data["self_id"])
        if data.get("post_type") == "notice" and data.get("notice_type") == "group_increase" and data.get("group_id") and self.on_group_join:
            try:
                res = self.on_group_join(str(data["group_id"]), str(data.get("user_id") or ""))
                if inspect.isawaitable(res):
                    await res
            except Exception:
                log.exception("处理进群通知出错")
            return
        msg = parse_group_message(data)
        if msg and self.on_group_message:
            try:
                res = self.on_group_message(msg)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                log.exception("处理群消息出错")

    # ---------- 接口 ----------

    async def call(self, action: str, params: dict | None = None, timeout: float = 10.0):
        if action not in ALLOWED_ACTIONS:
            raise QQLinkError(f"不允许的操作：{action}")
        if not self.connected or self._ws is None:
            raise QQLinkError("QQ 号没有连上")
        echo = str(next(self._seq))
        fut = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        await self._ws.send(json.dumps({"action": action, "params": params or {}, "echo": echo}))
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)
            raise QQLinkError("QQ 号没有回应") from None
        if resp.get("status") != "ok" and resp.get("retcode", 0) != 0:
            raise QQLinkError(str(resp.get("wording") or resp.get("message") or f"失败（retcode {resp.get('retcode')}）"))
        return resp.get("data")

    async def set_group_name(self, group_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise QQLinkError("群名不能为空")
        await self.call("set_group_name", {"group_id": int(group_id), "group_name": name})

    async def send_group_text(self, group_id: str, text: str, at_qqs: list[str] | None = None) -> None:
        """在群里发一条文字，开头 @ 指定的人（QQ 号）。"""
        segs = []
        for q in at_qqs or []:
            segs += [{"type": "at", "data": {"qq": str(q)}}, {"type": "text", "data": {"text": " "}}]
        segs.append({"type": "text", "data": {"text": text}})
        await self.call("send_group_msg", {"group_id": int(group_id), "message": segs})

    async def group_list(self) -> list[dict]:
        return await self.call("get_group_list") or []

    async def member_list(self, group_id: str) -> list[str]:
        """群里所有成员的 QQ 号（要这个号自己在群里）。要强制刷新：不带 no_cache，SnowLuma 会给缓存的旧名单，刚进群的人看不到。"""
        rows = await self.call("get_group_member_list", {"group_id": int(group_id), "no_cache": True}) or []
        return [str(r.get("user_id")) for r in rows if r.get("user_id")]

    async def login_info(self) -> dict:
        return await self.call("get_login_info") or {}
