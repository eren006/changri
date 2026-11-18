"""扩展：核心永远加载；官方扩展（随安装包带的可选功能）按开关加载；用户扩展（extensions/ 文件夹里的）按开关加载。

开关存在 config 表的 ext:<id>（1 开 / 0 关），改了要重启才生效（设置平台「扩展」页有重启按钮）。
用户扩展 = extensions/<id>/ 文件夹：manifest.json + __init__.py（普通 NoneBot 插件），只用 changri_api 里的东西，不碰内部的表。
在线更新只覆盖核心代码，不碰 extensions/，所以用户的自定义不会被更新冲掉。
"""
import ast
import io
import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

from ..features import registry
from .db import get_config, set_config

from ..paths import APP_ROOT as ROOT, DATA_DIR, EXT_DOCS_DIR, PKG_DIR
USER_DIR = Path(os.environ.get("CHANGRI_EXT_DIR") or DATA_DIR / "extensions")     # 用户扩展算数据：更新程序不碰
API_VERSION = 1

# 核心：别的功能的地基，不能关
CORE_MODULES = ["changri.plugins.ping", "changri.plugins.botsync", "changri.plugins.social", "changri.plugins.core_admin", "changri.plugins.season", "changri.plugins.play", "changri.plugins.autoreply", "changri.plugins.trpg", "changri.plugins.trpglog", "changri.plugins.prefix", "changri.plugins.qqwatch", "changri.plugins.msgmon"]  # 季度与游戏天数是地基：短信礼物、心动信的每日次数都按它算「今天」

# 官方扩展：随包带的可选功能。settings_group = 设置页里属于它的那一组；nav = 左侧菜单里属于它的页面
OFFICIAL = registry.official()        # 每个功能在 features/<功能>/__init__.py 里写 FEATURE 声明自己
# 核心功能：永远开着，没有开关；设置和管理页入口跟官方扩展一样放在自己的卡片里
CORE_CARDS = registry.core_cards()
for _e in CORE_CARDS:
    _e.update(kind="core", version="随主程序", author="长日将尽", homepage="", license="随主程序", enabled=True, error="", settings=[])
for _e in OFFICIAL:
    _e.setdefault("links", [])
    _e.update(kind="official", version="随主程序", author="长日将尽", homepage="", license="随主程序")
    if _e.get("personal"):           # 个性化模块：别人点名要的小功能，永远默认关
        _e["default_on"] = False

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
SETTING_TYPES = {"int", "string", "text", "bool", "choice"}


# 小手机自己的功能：开关和设置都在后台「小手机」页里，不算「额外扩展」（没有群里的指令，不用重启）。值 = 默认开不开。
PHONE_FEATURES = {"moments": False, "masquerade": False, "games": True, "chatroom": False, "groupchat": False, "dailyquote": False}


def is_enabled(ext_id: str) -> bool:
    """没手动开关过的，按官方扩展自己的默认（大多数默认开；心愿默认关）。"""
    if ext_id in PHONE_FEATURES:
        return get_config(f"ext:{ext_id}", "1" if PHONE_FEATURES[ext_id] else "0") != "0"
    default = "1" if next((e.get("default_on", True) for e in OFFICIAL if e["id"] == ext_id), True) else "0"
    if ext_id == "giftshop" and get_config("gift_catalog", "").strip():
        default = "1"            # 升级前已经上架了礼物的，礼品店保持开启
    if ext_id == "stakeout" and get_config("stakeout_enabled", "0") == "1":
        default = "1"            # 以前在「约会房间」设置里打开过踩点的，变成扩展卡片后保持开启
    if ext_id == "joinreq" and _joinreq_used():
        default = "1"            # 升级前已经有人申请加入过的，变成扩展卡片后保持开启
    if ext_id == "phone" and get_config("phone_enabled", "0") == "1":
        default = "1"            # 升级前已经在用小手机的，不能因为它变成可选项就被关掉
    return get_config(f"ext:{ext_id}", default) != "0"


def _joinreq_used() -> bool:
    from .db import get_conn
    try:
        with get_conn() as c:
            return c.execute("SELECT 1 FROM join_requests LIMIT 1").fetchone() is not None
    except Exception:  # noqa: BLE001  表还没建
        return False


def set_enabled(ext_id: str, on: bool) -> None:
    set_config(f"ext:{ext_id}", "1" if on else "0")
    set_config("ext_pending_restart", "1")


def personal_ids() -> list[str]:
    """个性化模块（FEATURE 里写 personal=True）的 id。"""
    return [e["id"] for e in OFFICIAL if e.get("personal")]


def reset_personal() -> int:
    """把个性化模块全部关上（「全部恢复默认设置」时调用）；返回原本开着、被关掉的个数。"""
    n = 0
    for ext_id in personal_ids():
        if is_enabled(ext_id):
            n += 1
        set_config(f"ext:{ext_id}", "0")
    if n:
        set_config("ext_pending_restart", "1")
    return n


def pending_restart() -> bool:
    return get_config("ext_pending_restart", "0") == "1"


def enabled_official() -> list[dict]:
    return [e for e in OFFICIAL if is_enabled(e["id"])]


def disabled_nav() -> set[str]:
    """左侧菜单里要隐藏的页面：这个页面归属的扩展全都关了才隐藏（季度与出场共用一个页面）。"""
    owners: dict[str, list[bool]] = {}
    for e in OFFICIAL:
        for n in e["nav"]:
            owners.setdefault(n, []).append(is_enabled(e["id"]))
    return {n for n, flags in owners.items() if not any(flags)}


def disabled_groups() -> set[str]:
    return {e["settings_group"] for e in OFFICIAL if e["settings_group"] and not is_enabled(e["id"])}


# ---------- 用户扩展 ----------

def validate_manifest(d: dict, folder: str | None = None) -> str:
    """合法返回空串，否则返回原因。"""
    if not isinstance(d, dict):
        return "manifest.json 要是一个对象"
    ext_id = d.get("id", "")
    if not ID_RE.match(str(ext_id)):
        return "id 只能用小写字母、数字、下划线，字母开头，2–31 个字符"
    if folder is not None and folder != ext_id:
        return f"文件夹名（{folder}）要和 id（{ext_id}）一致"
    if ext_id in {e["id"] for e in OFFICIAL} | {"season", "social", "ping", "botsync", "core_admin", "play"}:
        return f"id「{ext_id}」和官方扩展重名"
    if not str(d.get("name", "")).strip():
        return "缺少 name"
    if not str(d.get("version", "")).strip():
        return "缺少 version"
    if int(d.get("api", 1)) > API_VERSION:
        return f"这个扩展需要更新的 changri_api（版本 {d.get('api')}，当前 {API_VERSION}），请先更新主程序"
    for s in d.get("settings", []):
        if not isinstance(s, dict) or not s.get("key") or s.get("type", "string") not in SETTING_TYPES:
            return "settings 里每一项要有 key，type 只能是 " + "/".join(sorted(SETTING_TYPES))
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(s["key"])):
            return f"设置项 key「{s['key']}」只能用字母数字下划线"
    return ""


# ---------- 代码检查（安装时跑，列表里也会显示警告）----------

_RESERVED: set[str] | None = None


def reserved_commands() -> set[str]:
    """主程序（核心 + 官方扩展）已经占用的指令名和别名，用户扩展不许重名，否则会劫持主程序的指令。"""
    global _RESERVED
    if _RESERVED is None:
        names: set[str] = set()
        for f in (PKG_DIR / "plugins").glob("*.py"):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("_cmd", "on_command") and n.args:
                    for a in [n.args[0]] + n.args[1:2] + [k.value for k in n.keywords if k.arg == "aliases"]:
                        for c in ast.walk(a):
                            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                                names.add(c.value)
        _RESERVED = names
    return _RESERVED


_FORBID_CALLS = {"os._exit": "会让整个机器人直接退出", "sys.exit": "会让整个机器人直接退出", "exit": "会让整个机器人直接退出",
                 "quit": "会让整个机器人直接退出", "os.kill": "不许结束进程", "os.killpg": "不许结束进程"}
_WARN_CALLS = {"subprocess": "会运行系统命令", "os.system": "会运行系统命令", "os.popen": "会运行系统命令", "eval": "动态执行代码", "exec": "动态执行代码",
               "time.sleep": "time.sleep 会卡住整个机器人，请用 await asyncio.sleep", "shutil.rmtree": "会删除文件夹"}
_WARN_IMPORTS = {"requests": "requests 是同步的，会卡住机器人，请用 httpx 的异步写法", "socket": "直接操作网络连接", "ctypes": "调用底层代码"}


def _dotted(n) -> str:
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr); n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
    return ".".join(reversed(parts))


def check_code(files: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """检查扩展里的 .py。返回 (错误, 警告)：有错误就不让装；警告只是提醒，扩展本来就有完整权限。"""
    errors: list[str] = []
    warns: list[str] = []
    reserved = reserved_commands()
    for path, data in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(data.decode("utf-8"), filename=path)
        except SyntaxError as e:
            errors.append(f"{path} 第 {e.lineno} 行语法错误：{e.msg}")
            continue
        except UnicodeDecodeError:
            errors.append(f"{path} 不是 UTF-8 编码")
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                name = _dotted(n.func)
                if name in _FORBID_CALLS:
                    errors.append(f"{path} 第 {n.lineno} 行用了 {name}()：{_FORBID_CALLS[name]}")
                for k, why in _WARN_CALLS.items():
                    if name == k or name.startswith(k + "."):
                        warns.append(f"{path} 第 {n.lineno} 行用了 {name}：{why}")
                if name in ("on_command", "on_message", "on_type", "on_keyword", "on_regex"):
                    pri = next((k.value.value for k in n.keywords if k.arg == "priority" and isinstance(k.value, ast.Constant)), None)
                    block = next((k.value.value for k in n.keywords if k.arg == "block" and isinstance(k.value, ast.Constant)), None)
                    if name == "on_command" and n.args:
                        used = {c.value for a in [n.args[0]] + n.args[1:2] + [k.value for k in n.keywords if k.arg == "aliases"]
                                for c in ast.walk(a) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
                        for u in sorted(used & reserved):
                            errors.append(f"{path} 第 {n.lineno} 行：指令「{u}」和主程序重名，换一个名字")
                    if name != "on_command" and block is True:
                        warns.append(f"{path} 第 {n.lineno} 行：{name} 设了 block=True，会拦住后面所有人的消息，通常要设成 False")
                    if isinstance(pri, int) and pri < 5:
                        warns.append(f"{path} 第 {n.lineno} 行：priority={pri} 太靠前，可能抢在主程序前面处理消息，建议 10 以上")
            elif isinstance(n, ast.Import):
                for a in n.names:
                    root = a.name.split(".")[0]
                    if root in _WARN_IMPORTS:
                        warns.append(f"{path} 第 {n.lineno} 行 import {a.name}：{_WARN_IMPORTS[root]}")
                    if root == "core":
                        warns.append(f"{path} 第 {n.lineno} 行 import {a.name}：只从 changri_api 取东西，core 里的名字以后可能改")
            elif isinstance(n, ast.ImportFrom):
                root = (n.module or "").split(".")[0]
                if root in _WARN_IMPORTS:
                    warns.append(f"{path} 第 {n.lineno} 行 from {n.module} import：{_WARN_IMPORTS[root]}")
                if root in ("core", "plugins", "admin"):
                    warns.append(f"{path} 第 {n.lineno} 行 from {n.module} import：只从 changri_api 取东西，{root} 里的名字以后可能改")
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and (".env" == n.value.strip() or "changri.db" in n.value):
                warns.append(f"{path} 第 {getattr(n, 'lineno', 0)} 行碰了 {n.value}：机器人密钥和数据库不要直接读写")
    return errors[:20], list(dict.fromkeys(warns))[:20]


def _folder_files(d: Path) -> dict[str, bytes]:
    out = {}
    for f in d.rglob("*.py"):
        if f.stat().st_size < 1_000_000:
            out[str(f.relative_to(d))] = f.read_bytes()
    return out


def scan_user() -> list[dict]:
    out = []
    if not USER_DIR.is_dir():
        return out
    for d in sorted(USER_DIR.iterdir()):
        mf = d / "manifest.json"
        if not (d.is_dir() and mf.is_file()) or d.name.startswith((".", "_")):
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            err = validate_manifest(m, d.name)
        except Exception as e:  # noqa: BLE001
            m, err = {"id": d.name, "name": d.name, "version": "?"}, f"manifest.json 读不出来：{e}"
        m = dict(m)
        st = mf.stat()
        m.update(kind="user", path=str(d), error=err, installed_ts=int(d.stat().st_ctime), updated_ts=int(st.st_mtime))
        m["code_errors"], m["warnings"] = check_code(_folder_files(d))
        if m["code_errors"] and not err:
            err = "代码检查没通过：" + m["code_errors"][0]
            m["error"] = err
        m.setdefault("author", ""); m.setdefault("description", ""); m.setdefault("homepage", ""); m.setdefault("license", "")
        out.append(m)
    return out


def user_is_enabled(ext_id: str) -> bool:
    # 新放进去的用户扩展默认关：要用户在「扩展」页自己点开（代码是别人写的，装上不代表想跑）
    return get_config(f"ext:{ext_id}", "0") == "1"


def setting_key(ext_id: str, key: str) -> str:
    return f"ext.{ext_id}.{key}"


def get_setting(ext_id: str, key: str, default=None):
    """读用户扩展的设置项，按 manifest 里声明的类型转换；没填用 manifest 里的默认值。"""
    spec = next((s for m in scan_user() if m["id"] == ext_id for s in m.get("settings", []) if s["key"] == key), None)
    raw = get_config(setting_key(ext_id, key), "")
    if spec is None:
        return raw if raw != "" else default
    t = spec.get("type", "string")
    if raw == "":
        raw = str(spec.get("default", "")) if default is None else str(default)
    if t == "int":
        try:
            return int(raw)
        except ValueError:
            return int(spec.get("default", 0) or 0)
    if t == "bool":
        return raw in ("1", "true", "True", "是", "开")
    return raw


def save_settings(ext_id: str, form: dict) -> int:
    n = 0
    for m in scan_user():
        if m["id"] != ext_id:
            continue
        for s in m.get("settings", []):
            k = s["key"]
            if s.get("type") == "bool":
                set_config(setting_key(ext_id, k), "1" if form.get(k) in ("1", "on", "true") else "0")
                n += 1
            elif k in form:
                v = str(form[k]).strip() if s.get("type") != "text" else str(form[k])
                if s.get("type") == "int" and v != "" and not re.fullmatch(r"-?\d+", v):
                    continue
                if s.get("type") == "choice" and v not in [str(c) for c in s.get("choices", [])]:
                    continue
                set_config(setting_key(ext_id, k), v)
                n += 1
    return n


MAX_ZIP = 5 * 1024 * 1024


def install_zip(data: bytes, replace: bool = False) -> tuple[bool, str]:
    """安装用户扩展压缩包：里面要有 <id>/manifest.json（也认 manifest.json 直接在根上，id 当文件夹名）。路径不安全、太大、清单不合法都拒绝。"""
    if len(data) > MAX_ZIP:
        return False, "压缩包太大（超过 5MB），不像是一个扩展。"
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return False, "不是有效的 zip 文件。"
    names = [n for n in z.namelist() if not n.endswith("/")]
    for n in names:
        p = n.replace("\\", "/")
        if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
            return False, f"压缩包里有不安全的路径：{n}"
    if sum(z.getinfo(n).file_size for n in names) > 20 * 1024 * 1024:
        return False, "解压后太大（超过 20MB）。"
    roots = {n.replace("\\", "/").split("/")[0] for n in names if "/" in n}
    flat = "manifest.json" in [n.replace("\\", "/") for n in names]
    prefix = ""
    if not flat:
        if len(roots) != 1 or f"{next(iter(roots))}/manifest.json" not in [n.replace("\\", "/") for n in names]:
            return False, "压缩包里要有一个文件夹，里面有 manifest.json 和 __init__.py。"
        prefix = next(iter(roots)) + "/"
    try:
        m = json.loads(z.read(prefix + "manifest.json").decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"manifest.json 读不出来：{e}"
    err = validate_manifest(m)
    if err:
        return False, err
    if (prefix + "__init__.py") not in [n.replace("\\", "/") for n in names]:
        return False, "缺少 __init__.py。"
    files = {n.replace("\\", "/")[len(prefix):]: z.read(n) for n in names if n.replace("\\", "/").startswith(prefix) and n.endswith(".py")}
    errs, warns = check_code(files)
    if errs:
        return False, "代码检查没通过，没有安装：\n" + "\n".join(errs)
    ext_id = m["id"]
    target = USER_DIR / ext_id
    if target.exists() and not replace:
        return False, f"已经装了「{ext_id}」。要覆盖请勾选「覆盖已有的同名扩展」。"
    USER_DIR.mkdir(exist_ok=True)
    tmp = USER_DIR / f".installing_{ext_id}"
    shutil.rmtree(tmp, ignore_errors=True)
    for n in names:
        p = n.replace("\\", "/")
        if prefix and not p.startswith(prefix):
            continue
        dest = tmp / p[len(prefix):]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(n))
    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    set_config("ext_pending_restart", "1")
    note = ("\n⚠️ 代码里有 %d 处要留意：\n" % len(warns) + "\n".join(warns[:5])) if warns else ""
    return True, f"已安装「{m['name']}」{m['version']}。默认是关闭的，打开开关并重启后生效。{note}"


def delete_user(ext_id: str) -> bool:
    if not ID_RE.match(ext_id) or not (USER_DIR / ext_id).is_dir():
        return False
    shutil.rmtree(USER_DIR / ext_id)
    set_config(f"ext:{ext_id}", "0")
    set_config("ext_pending_restart", "1")
    return True


# ---------- 列表（设置平台用） ----------

def list_all() -> list[dict]:
    items = [dict(c) for c in CORE_CARDS]
    for e in OFFICIAL:
        items.append({**e, "enabled": is_enabled(e["id"]), "error": "", "settings": []})
    for m in scan_user():
        items.append({**m, "enabled": user_is_enabled(m["id"]), "settings": m.get("settings", [])})
    return items


# ---------- 加载（bot.py 启动时调用一次） ----------

def load_all() -> dict[str, str]:
    """加载核心、开着的官方扩展、开着的用户扩展。返回 {扩展id: 加载失败的原因}（成功的不在里面）。"""
    import nonebot

    set_config("ext_pending_restart", "0")
    for mod in CORE_MODULES:
        nonebot.load_plugin(mod)
    failed: dict[str, str] = {}
    for e in enabled_official():
        try:
            if e["module"] and nonebot.load_plugin(e["module"]) is None:       # module 为空 = 只有网页/小手机的功能，没有群里的指令
                failed[e["id"]] = "加载失败（详见日志）"
        except Exception as ex:  # noqa: BLE001
            failed[e["id"]] = str(ex)[:200]
    # 崩溃保护：上次启动加载的用户扩展没来得及「确认平安」进程就没了（崩溃、被杀、死循环），这次全部先停用，免得开机就崩、网页都进不去
    suspects = [x for x in get_config("ext_boot_guard", "").split(",") if x]
    for ext_id in suspects:
        set_config(f"ext:{ext_id}", "0")
        failed[ext_id] = "上次启动没有成功（可能就是它导致的），已自动停用。改好后可以重新打开。"
    set_config("ext_boot_guard", "")
    safe_mode = os.environ.get("CHANGRI_SAFE_MODE") == "1"      # 手动安全模式：只跑核心和官方扩展
    loaded_user: list[str] = []
    for m in scan_user():
        if m["error"] or not user_is_enabled(m["id"]) or safe_mode:
            if m["error"]:
                failed[m["id"]] = m["error"]
            continue
        loaded_user.append(m["id"])
        set_config("ext_boot_guard", ",".join(loaded_user))
        try:
            if nonebot.load_plugin(Path(m["path"])) is None:
                failed[m["id"]] = "加载失败（详见日志）"
        except Exception as ex:  # noqa: BLE001
            failed[m["id"]] = str(ex)[:200]
    for k in [k for k in list_keys("ext_error:")]:
        set_config(k, "")
    for ext_id, why in failed.items():
        set_config(f"ext_error:{ext_id}", why)
    return failed


def confirm_boot() -> None:
    """启动后过了一阵机器人还活着，就确认这批用户扩展没问题，撤掉崩溃保护的标记。"""
    set_config("ext_boot_guard", "")


def list_keys(prefix: str) -> list[str]:
    """设置里以 prefix 开头、值不为空的键。要和 get_config / set_config 查同一个库：
    ext_ 开头的存在全局库里（db.GLOBAL_CONFIG_PREFIXES），以前查的是当前机器人的库，扩展的旧报错一直清不掉。"""
    from .db import GLOBAL_CONFIG_PREFIXES, get_conn, get_global_conn

    with (get_global_conn() if prefix.startswith(GLOBAL_CONFIG_PREFIXES) else get_conn()) as conn:
        return [r[0] for r in conn.execute("SELECT key FROM config WHERE key LIKE ? AND value!=''", (prefix + "%",))]


def load_error(ext_id: str) -> str:
    return get_config(f"ext_error:{ext_id}", "")


def restart_soon(delay: float = 1.0) -> None:
    """退出码 3：systemd（Restart=on-failure）、start.sh、start.bat 都会把进程重新拉起。先让网页把响应发出去。"""
    confirm_boot()      # 人为重启不是崩溃，别让崩溃保护把刚开的扩展又关掉
    threading.Timer(delay, _exit_for_restart).start()


# ---------- 给 AI 的模板 ----------

DOCS = EXT_DOCS_DIR


def ai_prompt() -> str:
    """把规范 + 完整示例 + 一块「我的需求」拼成一段话，用户整段复制给 AI 就行。读的是随包附带的 docs/。"""
    def rd(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return "（文件缺失）"
    demo = DOCS / "示例扩展" / "hello_demo"
    return (
        "你是「长日」QQ 机器人的扩展作者。请按下面的规范，为我写一个扩展，严格照示例的格式，不要用规范以外的写法。\n\n"
        "========== 规范 ==========\n" + rd(DOCS / "写扩展.md") + "\n\n"
        "========== 示例：manifest.json ==========\n" + rd(demo / "manifest.json") + "\n\n"
        "========== 示例：__init__.py ==========\n" + rd(demo / "__init__.py") + "\n\n"
        "========== 我想要的扩展 ==========\n"
        "（把下面几项改成你的，写得越具体越好）\n"
        "- 扩展叫什么、一句话介绍：\n"
        "- 玩家发什么指令触发（写出完整的例子）：\n"
        "- 发了之后机器人怎么回（成功、失败、参数不对时各回什么）：\n"
        "- 需要在设置平台里调的选项（每项写：人话名字、类型 string/text/int/bool/choice、默认值、一句说明；数字可以写单位）：\n"
        "- 需要用到的角色信息、属性、天数、定时任务等（只能用 changri_api 里有的：玩家清单 all_roles、属性 get_attr/set_attr、天数等；物品栏/商城/地点目前没有接口，不要自己读数据库）：\n\n"
        "========== 输出要求 ==========\n"
        "1. 只输出两个代码块，分别标明文件名 manifest.json 和 __init__.py，不要多余的解释（第 8 条的提醒除外）。\n"
        "2. manifest.json 的 id 用小写字母、数字、下划线，字母开头。\n"
        "3. 只从 changri_api 导入，不要 import core / plugins / admin。\n"
        "4. 不能用 sys.exit / os._exit / time.sleep / requests；等待用 await asyncio.sleep，联网用 httpx 异步。\n"
        "5. 指令名不要和主程序已有的重名（约会、私约、电话、时间线、心愿、发帖、短信、送礼 等）。\n"
        "6. 每条指令都要有回复，出错时回一句人话。\n"
        "7. 凡是玩家/管理员可能想调的数字、开关、文字（次数上限、冷却、开关、回复内容……），不要写死在代码里：写进 manifest.json 的 settings，"
        "每项带 label（人话）、desc（一句说明，数字注明 0 代表什么）、default，数字可加 unit；代码里用 setting(EXT_ID, key) 读。\n"
        "8. 扩展没有沙盒，写错会改坏全体玩家数据或让机器人卡死：改数据要限定范围、检查 set_attr 的返回值，不要写一条指令改全员的东西；不要读 .env、不要碰数据库文件；回答最后用一两句话提醒我「装之前先备份、先在测试群试」。\n"
    )


def skeleton_zip() -> bytes:
    """示例扩展打成 zip，当骨架下载。"""
    buf = io.BytesIO()
    demo = DOCS / "示例扩展" / "hello_demo"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(demo.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f"my_extension/{f.relative_to(demo)}")
    return buf.getvalue()


def _exit_for_restart() -> None:
    """人为重启（点了「重启」、更新完成）：先把「正常退出」标记写好，再用退出码 3 让服务管理器拉起；
    否则 os._exit 跳过收尾，下次启动会把这次重启记成崩溃。"""
    try:
        from . import errlog
        errlog.mark_clean_exit()
    except Exception:  # noqa: BLE001
        pass
    os._exit(3)
