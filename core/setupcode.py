"""首次设置验证码：后台对公网开放后，新装时 /setup 页谁都能打开，抢先建主账号就等于拿走整个后台。
所以不是在服务器本机打开的话，要先输入一个只有装的人看得到的验证码（打在安装输出和日志里，也存在 data/.setup_code）。"""
import secrets

from .db import DB_PATH

LOOPBACK = {"127.0.0.1", "::1"}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _path():
    return DB_PATH.parent / ".setup_code"


def code() -> str:
    p = _path()
    if p.exists():
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    p.write_text(c)
    p.chmod(0o600)
    return c


def required_for(remote_addr: str | None) -> bool:
    return (remote_addr or "") not in LOOPBACK


def check(given: str) -> bool:
    return secrets.compare_digest((given or "").strip().upper(), code())


def clear() -> None:
    try:
        _path().unlink()
    except OSError:
        pass
