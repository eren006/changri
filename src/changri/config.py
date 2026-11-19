"""程序级配置：data/config.toml（取代以前的 .env）。

只放「启动前就要知道」的几项：监听地址、设置平台端口、日志级别、时区。其余设置都在数据库里，在设置平台改。
读取顺序：内置默认 < config.toml < 环境变量（HOST / PORT / LOG_LEVEL / CHANGRI_TZ，方便测试和临时改）。
第一次启动时如果还没有 config.toml，会从老的 .env 里把这几项导入一次。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import dotenv_values

from .paths import DATA_DIR, LEGACY_ENV

DEFAULTS: dict[str, object] = {"host": "0.0.0.0", "port": 5091, "log_level": "INFO", "timezone": "Asia/Shanghai"}
_ENV = {"host": "HOST", "port": "PORT", "log_level": "LOG_LEVEL", "timezone": "CHANGRI_TZ"}
_COMMENTS = {
    "host": "监听地址。0.0.0.0 = 外网也能打开设置平台；改成 127.0.0.1 就只能在这台机器上打开",
    "port": "设置平台的端口（云服务器要在安全组里放行）",
    "log_level": "日志详细程度：DEBUG / INFO / WARNING",
    "timezone": "时区：每天 23:59 换天、心动信几点派送都按它算。默认北京时间；写成空字符串 \"\" 就跟着这台机器的系统时区",
}


def path() -> Path:
    return DATA_DIR / "config.toml"


def _coerce(key: str, value):
    return int(value) if isinstance(DEFAULTS[key], int) else str(value)


def _read_file() -> dict:
    p = path()
    if not p.exists():
        return {}
    with p.open("rb") as f:
        raw = tomllib.load(f)
    return {k: _coerce(k, v) for k, v in raw.items() if k in DEFAULTS}


def save(values: dict) -> None:
    lines = ["# 长日bot 程序配置。改完要重启才生效；其余设置都在设置平台里。", ""]
    for k in DEFAULTS:
        v = values.get(k, DEFAULTS[k])
        lines.append(f"# {_COMMENTS[k]}")
        lines.append(f"{k} = {v}" if isinstance(v, int) else f'{k} = "{v}"')
        lines.append("")
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, p)


def ensure() -> None:
    """没有 config.toml 就建一个：有老 .env 的从里面导入，没有就用默认值。老 .env 里的 FLASK_SECRET_KEY 搬进 data/.secret_key。"""
    if path().exists():
        return
    _move_legacy_extensions()
    values = dict(DEFAULTS)
    if LEGACY_ENV.exists():
        old = dotenv_values(LEGACY_ENV)
        for k, env in _ENV.items():
            if old.get(env):
                try:
                    values[k] = _coerce(k, old[env])
                except ValueError:
                    pass
        key_file = DATA_DIR / ".secret_key"
        if old.get("FLASK_SECRET_KEY") and not key_file.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            key_file.write_text(old["FLASK_SECRET_KEY"])
            try:
                key_file.chmod(0o600)
            except OSError:
                pass
    save(values)


def _move_legacy_extensions() -> None:
    """老版本把用户扩展放在程序目录的 extensions/；现在算数据，搬进 data/extensions/。"""
    import shutil

    from .paths import APP_ROOT
    old, new = APP_ROOT / "extensions", DATA_DIR / "extensions"
    if old.is_dir() and not new.exists() and old.resolve() != new.resolve():
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))


def load() -> dict:
    values = dict(DEFAULTS)
    values.update(_read_file())
    for k, env in _ENV.items():
        if os.environ.get(env):
            try:
                values[k] = _coerce(k, os.environ[env])
            except ValueError:
                pass
    return values


def get(key: str):
    return load()[key]


def admin_port() -> int:
    return int(get("port"))


# 没有时区数据库的机器上（精简过的 Linux），常用时区退回固定偏移的写法
_POSIX_FALLBACK = {"Asia/Shanghai": "CST-8", "Asia/Hong_Kong": "HKT-8", "Asia/Taipei": "CST-8", "Asia/Singapore": "SGT-8", "Asia/Tokyo": "JST-9"}


def apply_timezone(tz: str | None = None) -> str:
    """把整个进程的时区设成 config 里的 timezone（默认北京时间），time.localtime() 和定时任务都跟着走。
    以前直接用服务器的系统时区：UTC 的服务器上「23:59 换天」会变成北京时间早上 7:59。
    Windows 没有 time.tzset，跟着电脑自己的时区（装在自己电脑上的一般就是北京时间）。返回实际用的说明，给日志和自检看。"""
    import time
    tz = (get("timezone") if tz is None else tz).strip()
    if not tz:
        return "跟着系统时区"
    if not hasattr(time, "tzset"):
        return "跟着系统时区（Windows 不能单独设）"
    value = tz
    if "/" in tz and not any((Path(d) / tz).exists() for d in ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo")):
        value = _POSIX_FALLBACK.get(tz, "")
        if not value:
            return f"跟着系统时区（这台机器没有时区数据，认不出 {tz}）"
    os.environ["TZ"] = value
    time.tzset()
    return tz
