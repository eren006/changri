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
from dataclasses import dataclass, field

log = logging.getLogger("qqlink")

ALLOWED_ACTIONS = {"set_group_name", "get_group_list", "get_login_info", "get_group_member_list", "send_group_msg", "get_msg", "get_forward_msg"}     # 只放行这几个：不踢人、不禁言；send_group_msg 只给房间群提示用；get_msg / get_forward_msg 只读（补录合并转发）


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
    reply_id: str = ""                                      # 回复了哪条消息（补录：回复合并转发发「补录」）
    forward_ids: list = field(default_factory=list)         # 这条消息本身是合并转发：转发记录的 id


def segments_to_text(message) -> str:
    """OneBot 消息（字符串或消息段数组）转成纯文字；图片、表情等写成 [图片] 这样的占位。"""
    if isinstance(message, str):
        return message
    out = []
    if not isinstance(message, list):
        return ""
    for seg in message:
        if not isinstance(seg, dict):
            continue
        kind, data = seg.get("type"), seg.get("data") if isinstance(seg.get("data"), dict) else {}
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
    # 各家实现偶尔给出奇怪的字段（sender 不是对象、消息段里混着字符串、time 不是数字）：能认的认，认不出的当没有，不抛错
    sender = ev.get("sender") if isinstance(ev.get("sender"), dict) else {}
    nick = str(sender.get("card") or sender.get("nickname") or ev.get("user_id", ""))
    message = ev.get("message")
    segs = [s for s in message if isinstance(s, dict)] if isinstance(message, list) else []
    text = segments_to_text(message if isinstance(message, str) else segs) or str(ev.get("raw_message", "")).strip()

    def seg_data(s) -> dict:
        return s.get("data") if isinstance(s.get("data"), dict) else {}
    reply = next((str(seg_data(s).get("id", "")) for s in segs if s.get("type") == "reply"), "")
    fwd = [str(seg_data(s)["id"]) for s in segs if s.get("type") == "forward" and seg_data(s).get("id")]
    try:
        ts = int(float(ev.get("time") or 0)) or int(time.time())
    except (TypeError, ValueError):
        ts = int(time.time())
    return GroupMsg(self_id=str(ev.get("self_id", "")), group_id=str(ev.get("group_id", "")),
                    user_id=str(ev.get("user_id", "")), nickname=nick, text=text,
                    time=ts, message_id=str(ev.get("message_id", "")),
                    reply_id=reply, forward_ids=fwd)


def forward_nodes(data) -> list[dict]:
    """get_forward_msg 的结果 → [{ts, qq, nickname, text}]。兼容 NapCat / LLBot / SnowLuma 的几种写法（messages / message，content / message）。
    转发里再套的转发不展开，写成 [合并转发]。"""
    if isinstance(data, dict):
        data = data.get("messages") or data.get("message") or []
    out = []
    for n in data or []:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "node":                         # 有的实现每条外面再包一层 {type: node, data: {...}}
            n = n.get("data") or {}
        sender = n.get("sender") or {}
        body = n.get("message", n.get("content"))
        if isinstance(body, list) and any(s.get("type") == "forward" for s in body if isinstance(s, dict)):
            text = "[合并转发]"
        else:
            text = segments_to_text(body) if body is not None else ""
        out.append({"ts": int(n.get("time") or 0), "qq": str(sender.get("user_id") or n.get("user_id") or n.get("uin") or ""),
                    "nickname": str(sender.get("card") or sender.get("nickname") or n.get("nickname") or n.get("name") or ""),
                    "text": text})
    return out


class QQLink:
    def __init__(self, url: str, token: str = "", on_group_message=None, on_group_join=None, on_group_recall=None):
        self.url, self.token = url, token
        self.on_group_message = on_group_message
        self.on_group_recall = on_group_recall      # fn(group_id, message_id)：有人撤回了群消息（OneBot 的 group_recall 通知）
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
        """收消息的循环只做两件事：API 的返回当场交给等它的 call()，事件排进队列交给 _work 按顺序处理。
        不能在这里直接 await 回调：回调里常常要再调这个号的接口（房间群里回话、改群名、查群成员），
        那个返回也要靠这个循环来收，循环被回调占着就收不到，每次都白等到超时（「QQ 号没有回应」）。"""
        import websockets
        events: asyncio.Queue = asyncio.Queue()
        worker = asyncio.get_running_loop().create_task(self._work(events))
        delay = 1.0
        try:
            while not self._stop:
                try:
                    headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
                    async with websockets.connect(self.url, additional_headers=headers, open_timeout=10, max_size=8 * 1024 * 1024) as ws:
                        self._ws, self.connected, self.last_error, delay = ws, True, "", 1.0
                        async for raw in ws:
                            data = self._parse(raw)
                            if data is not None and not self._take_reply(data):
                                events.put_nowait(data)
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
        finally:
            worker.cancel()

    async def _work(self, events: asyncio.Queue) -> None:
        """按收到的顺序一条一条处理事件（同一个群里发言、到场的先后不会乱）。一条出错（包括格式不对）不影响后面的，也不会断线。"""
        while True:
            data = await events.get()
            try:
                await self._dispatch(data)
            except Exception:
                log.exception("处理 QQ 号事件出错")

    def stop(self) -> None:
        self._stop = True
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())

    def _fail_pending(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(QQLinkError("连接断开了"))
        self._pending.clear()

    @staticmethod
    def _parse(raw) -> dict | None:
        """不是 JSON 对象（坏数据、数组、数字）返回 None，直接丢掉。"""
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _take_reply(self, data: dict) -> bool:
        """是 API 的返回：交给等它的 call()，返回 True。"""
        echo = data.get("echo")
        if echo is None or str(echo) not in self._pending:
            return False
        fut = self._pending.pop(str(echo))
        if not fut.done():
            fut.set_result(data)
        return True

    async def _handle(self, raw) -> None:
        """一条消息当场处理完（测试直接调）。run() 里事件走队列，原因见 run 的说明。"""
        data = self._parse(raw)
        if data is not None and not self._take_reply(data):
            await self._dispatch(data)

    async def _dispatch(self, data: dict) -> None:
        self.last_event = time.time()
        if data.get("post_type") == "meta_event":
            status = data.get("status") if isinstance(data.get("status"), dict) else {}
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
        if data.get("post_type") == "notice" and data.get("notice_type") == "group_recall" and data.get("message_id") and self.on_group_recall:
            try:
                res = self.on_group_recall(str(data.get("group_id") or ""), str(data["message_id"]))
                if inspect.isawaitable(res):
                    await res
            except Exception:
                log.exception("处理撤回通知出错")
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
        if resp.get("status") == "failed" or resp.get("retcode", 0) not in (0, 1):     # OneBot：ok=0、async=1，其余都是失败
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

    async def forward_of(self, reply_id: str = "", forward_id: str = "") -> list[dict]:
        """读一条合并转发里的每条消息。给 forward_id 直接读；给 reply_id（被回复的那条）先取那条消息、找出里面的转发。"""
        if not forward_id and reply_id:
            msg = await self.call("get_msg", {"message_id": int(reply_id) if reply_id.isdigit() else reply_id})
            segs = (msg or {}).get("message") or []
            forward_id = next((str((s.get("data") or {}).get("id", "")) for s in segs if isinstance(s, dict) and s.get("type") == "forward"), "")
            inline = next(((s.get("data") or {}).get("content") for s in segs if isinstance(s, dict) and s.get("type") == "forward"), None)
            if inline:                                       # 有的实现把转发内容直接放在消息段里
                return forward_nodes(inline)
        if not forward_id:
            raise QQLinkError("回复的那条不是合并转发")
        data = await self.call("get_forward_msg", {"id": forward_id, "message_id": forward_id}, timeout=20.0)
        return forward_nodes(data)

    async def group_list(self) -> list[dict]:
        return await self.call("get_group_list") or []

    async def member_rows(self, group_id: str) -> list[dict]:
        """群里所有成员：[{"qq": QQ 号, "role": owner / admin / member}]（要这个号自己在群里）。
        要强制刷新：不带 no_cache，SnowLuma 会给缓存的旧名单，刚进群的人看不到。"""
        rows = await self.call("get_group_member_list", {"group_id": int(group_id), "no_cache": True})
        if rows is None:
            raise QQLinkError("没拿到群成员名单")
        return [{"qq": str(r.get("user_id")), "role": str(r.get("role") or "member")} for r in rows if r.get("user_id")]

    async def member_list(self, group_id: str) -> list[str]:
        """群里所有成员的 QQ 号。"""
        return [r["qq"] for r in await self.member_rows(group_id)]

    async def login_info(self) -> dict:
        return await self.call("get_login_info") or {}
