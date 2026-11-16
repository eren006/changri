"""收集 features/<功能>/ 里的东西，别的地方不用再手写清单。

- 功能声明：features/<功能>/__init__.py 里的 FEATURE = {...}
    kind      "core"（核心，不能关）/ "official"（随包带的可选扩展）/ "phone"（小手机里的功能，开关在「小手机」页）
    order     在「扩展」页里的顺序（小的在前）
    url_prefixes  设置平台里属于它的网址前缀：功能关着时这些页面 404
    personal  True = 放进「功能与设置 → 个性化模块」页：别人点名要的小定制，永远默认关，「全部恢复默认设置」时也会关回去
    其余字段（name、module、settings_group、nav、links、description、default_on）和以前 extensions.OFFICIAL 里的一样
- 设置平台页面：有 admin.py 的功能，里面写 register(app, login_required)
- 小手机页面：有 phone.py 的功能，里面写 register(app, ctx)

新加一个功能 = 新建 features/<功能>/ 文件夹，写 __init__.py（要出现在「扩展」页才需要 FEATURE），按需加 admin.py / phone.py；
群里的指令仍然放在 plugins/<功能>.py（NoneBot 用模块名最后一段当插件 ID，不能都叫同一个名字），在 FEATURE["module"] 里写上。
"""
from __future__ import annotations

import copy
import importlib
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent


def feature_ids() -> list[str]:
    return sorted(p.name for p in HERE.iterdir() if p.is_dir() and (p / "__init__.py").exists() and not p.name.startswith("_"))


@lru_cache(maxsize=None)
def _declared() -> tuple[dict, ...]:
    out = []
    for fid in feature_ids():
        mod = importlib.import_module(f"{__package__}.{fid}")
        f = getattr(mod, "FEATURE", None)
        if f:
            if f.get("id") != fid:
                raise ValueError(f"features/{fid}/__init__.py 里 FEATURE['id'] 应该是 {fid!r}，现在是 {f.get('id')!r}")
            out.append(f)
    return tuple(out)


def _of_kind(kind: str) -> list[dict]:
    return [copy.deepcopy(f) for f in sorted(_declared(), key=lambda f: f.get("order", 999)) if f.get("kind") == kind]


def official() -> list[dict]:
    return _of_kind("official")


def core_cards() -> list[dict]:
    return _of_kind("core")


def url_guards() -> list[tuple[str, str]]:
    """[(网址前缀, 功能id)]：功能关着时，设置平台里这些前缀的页面 404。"""
    return [(p, f["id"]) for f in _declared() for p in f.get("url_prefixes", [])]


def _pages(filename: str) -> list[str]:
    return [f"{__package__}.{fid}.{filename[:-3]}" for fid in feature_ids() if (HERE / fid / filename).exists()]


def admin_pages() -> list[str]:
    return _pages("admin.py")


def phone_pages() -> list[str]:
    return _pages("phone.py")
