"""「我的待回」的内容来源。核心只负责房间/时间线；关系线、心动信这些扩展自己注册一段进来，扩展关了就不会出现。"""
from typing import Callable

_PROVIDERS: list[tuple[str, Callable]] = []


def register(name: str, fn: Callable) -> None:
    """fn(role_row) -> list[str]（空列表表示没有）。同名重复注册会覆盖，方便热重载。"""
    global _PROVIDERS
    _PROVIDERS = [p for p in _PROVIDERS if p[0] != name] + [(name, fn)]


def collect_entries(role) -> list[tuple[str, str]]:
    """[(key, 文字)]。扩展可以返回字符串，也可以返回 (key, 文字)：key 给小手机「暂不提醒」认是不是同一次等待；
    只给字符串时按文字算 key（文字变了就当新的一条）。"""
    import hashlib
    out: list[tuple[str, str]] = []
    for name, fn in _PROVIDERS:
        try:
            for x in list(fn(role) or []):
                out.append(x if isinstance(x, tuple) else (f"p:{name}:" + hashlib.sha1(x.encode()).hexdigest()[:12], x))
        except Exception as e:  # noqa: BLE001 一个扩展出错不能拖垮整条指令
            out.append((f"err:{name}", f"（{name} 的待办读取失败：{str(e)[:40]}）"))
    return out


def collect(role) -> list[str]:
    return [t for _k, t in collect_entries(role)]


# ---------- 小手机「暂不提醒」 ----------

def dismissed(role_id: int) -> set[str]:
    from .db import get_conn
    with get_conn() as c:
        return {r["key"] for r in c.execute("SELECT key FROM pending_dismiss WHERE role_id=?", (role_id,))}


def set_dismissed(role_id: int, key: str, on: bool) -> None:
    import time
    from .db import get_conn
    with get_conn() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO pending_dismiss(role_id, key, created_ts) VALUES(?,?,?)", (role_id, key[:200], int(time.time())))
        else:
            c.execute("DELETE FROM pending_dismiss WHERE role_id=? AND key=?", (role_id, key))
