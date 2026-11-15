"""nonebot-adapter-qq 1.7.1 的兼容补丁（启动时调用 apply()）。

回复一条「合并转发的聊天记录」时，QQ 推过来的消息里 msg_elements 只有 content，没有 message_type 和 msg_idx；
适配器把这两个字段定成必填，整条消息解析失败直接丢掉（日志：2 validation errors for GroupMessageCreateEvent）。
这里把它们改成可以没有，再让所有用到这个模型的事件重新生成校验规则。
"""
from __future__ import annotations

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return
    from pydantic import BaseModel

    from nonebot.adapters.qq import event as qq_event
    from nonebot.adapters.qq.models import qq as qq_models

    reply = qq_models.QQReplyMessage
    changed = False
    for name in ("message_type", "msg_idx"):
        f = reply.model_fields.get(name)
        if f is not None and f.is_required():
            f.default = None
            changed = True
    if changed:
        reply.model_rebuild(force=True)
        for mod in (qq_models, qq_event):                  # 用到它的模型（消息、各种事件）都要重建，不然还按旧规则校验
            for obj in vars(mod).values():
                if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not reply:
                    try:
                        obj.model_rebuild(force=True)
                    except Exception:  # noqa: BLE001  个别模型重建不了不影响别的
                        pass
    _applied = True
