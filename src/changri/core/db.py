"""唯一的数据源：一个 SQLite 文件。bot 和设置平台都通过这里读写。"""
import os
import sqlite3
from pathlib import Path

import contextlib
import contextvars
import shutil
import threading

from ..paths import DATA_DIR

DB_PATH = Path(os.getenv("CHANGRI_DB_PATH") or DATA_DIR / "changri.db")   # 全局库
BOTS_DIR = DB_PATH.parent / "bots"                       # 每个机器人一个工作区：bots/<AppID>/changri.db
LEGACY_SINGLE = bool(os.getenv("CHANGRI_DB_PATH"))        # 测试用：指定了单个库文件就不分工作区，全部落在这一个库里

_current: contextvars.ContextVar[str] = contextvars.ContextVar("changri_bot", default="")
_default_bot: str | None = None                          # 没指定机器人时用的那个（第一个启用的）；None = 还没查过
_inited: set[str] = set()                                # 已建好表的工作区
_lock = threading.Lock()

# 属于整个程序、不跟机器人走的设置项（按前缀）。其余设置都在各机器人自己的库里。
GLOBAL_CONFIG_PREFIXES = ("remote_", "update_", "ext:", "ext_", "heartbeat:", "https_", "phonesrv_")

from ..storage.migrate import baseline_sql as _baseline_sql
from ..storage.migrate import migrate

SCHEMA = _baseline_sql()          # 只读：表结构以 storage/migrations/ 为准


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------- 多机器人：谁是「当前机器人」 ----------

def workspace_path(app_id: str) -> Path:
    return BOTS_DIR / app_id / "changri.db"


def current_bot() -> str:
    """现在这段代码在替哪个机器人干活。没设置就用第一个启用的机器人；一个机器人都没有就是空串（用全局库）。"""
    global _default_bot
    cur = _current.get()
    if cur:
        return cur
    if LEGACY_SINGLE:
        return ""
    if _default_bot is None:
        with _connect(DB_PATH) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_accounts'").fetchone():
                row = conn.execute("SELECT app_id FROM bot_accounts ORDER BY enabled DESC, created_ts, app_id LIMIT 1").fetchone()
                _default_bot = row["app_id"] if row else ""
            else:
                return ""
    return _default_bot


def forget_bots() -> None:
    """机器人清单变了（添加/删除）：下次重新选默认机器人。"""
    global _default_bot
    _default_bot = None


@contextlib.contextmanager
def use_bot(app_id: str):
    """with use_bot("1906…"): 这段代码里的 get_conn/get_config 都落在这个机器人的工作区。"""
    token = _current.set(str(app_id or ""))
    try:
        yield
    finally:
        _current.reset(token)


def enter_bot(app_id: str):
    """不用 with 的写法（Flask 请求开始/结束分开两处）：返回 token，结束时交给 leave_bot。"""
    return _current.set(str(app_id or ""))


def leave_bot(token) -> None:
    _current.reset(token)


def bot_scope(app_id_of):
    """装饰器：app_id_of(*args) 返回 AppID（给 NoneBot handler 用）。"""
    def deco(fn):
        import functools

        @functools.wraps(fn)
        async def wrap(*a, **kw):
            with use_bot(app_id_of(*a, **kw)):
                return await fn(*a, **kw)
        return wrap
    return deco


def list_bot_ids(only_enabled: bool = True) -> list[str]:
    if LEGACY_SINGLE:
        return [""]
    with _connect(DB_PATH) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_accounts'").fetchone():
            return []
        q = "SELECT app_id FROM bot_accounts" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY created_ts, app_id"
        return [r["app_id"] for r in conn.execute(q)]


def for_each_bot():
    """依次进入每个启用的机器人的工作区：for app_id in for_each_bot(): ...（定时任务用）。没有机器人时什么也不做。"""
    for app_id in list_bot_ids():
        with use_bot(app_id):
            yield app_id


def per_bot(fn):
    """定时任务专用：对每个启用的机器人各跑一遍（各自的工作区）；一个出错不影响其他的。"""
    import functools
    import logging

    @functools.wraps(fn)
    async def wrap(*a, **kw):
        for app_id in list_bot_ids():
            try:
                with use_bot(app_id):
                    await fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                logging.getLogger("changri.db").warning(f"[{app_id}] 定时任务 {fn.__name__} 出错：{e}")
    return wrap


def erase_workspace(app_id: str) -> bool:
    """彻底删除这个机器人的整个工作区文件夹（不可恢复）。只用在后台「彻底删除」。"""
    import re
    if not re.fullmatch(r"\d{4,24}", app_id or ""):
        return False
    folder = workspace_path(app_id).parent
    _inited.discard(app_id)
    if not folder.exists():
        return False
    shutil.rmtree(folder)
    return True


def get_global_conn() -> sqlite3.Connection:
    """全局库：后台账号、机器人清单、更新、远程访问。"""
    return _connect(DB_PATH)


def get_conn() -> sqlite3.Connection:
    """当前机器人的库；没有机器人（刚装好还没接入）时用全局库。"""
    app_id = current_bot()
    if not app_id or LEGACY_SINGLE:
        return _connect(DB_PATH)
    path = workspace_path(app_id)
    if app_id not in _inited:
        _init_workspace(app_id, path)
    return _connect(path)


def _init_workspace(app_id: str, path: Path) -> None:
    with _lock:
        if app_id in _inited:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        migrate(path, legacy=_migrate)
        _inited.add(app_id)


def migrate_to_workspaces() -> str:
    """升级到多机器人：旧的单库整个复制成第一个机器人的工作区（旧库先留一份 .premulti 备份，不删）。
    已有工作区就什么也不做；失败也不会影响旧库继续用。返回做了什么（空串 = 没动）。"""
    if LEGACY_SINGLE or not DB_PATH.exists():
        return ""
    ids = list_bot_ids(only_enabled=False)
    if not ids:
        return ""
    first = ids[0]
    dst = workspace_path(first)
    if dst.exists():
        return ""
    bak = DB_PATH.with_name(DB_PATH.name + ".premulti")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        for target in (bak, dst):
            if target.exists():
                continue
            src = sqlite3.connect(DB_PATH, timeout=10)
            out = sqlite3.connect(target)
            try:
                src.backup(out)          # 在线一致拷贝（库开着、有 WAL 也安全）
            finally:
                out.close()
                src.close()
    except Exception:
        dst.unlink(missing_ok=True)      # 半成品不留，下次启动重来
        raise
    return f"已把现有数据复制为机器人 {first} 的工作区（旧库备份：{bak.name}）"


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(path: Path | None = None) -> None:
    """旧库升级：role_groups / admins 加 bot_id（已有数据归到 .env 里配置的第一个机器人）。
    重建表时必须关外键，否则 DROP TABLE 会把别的表里引用它的行级联删掉。"""
    path = path or DB_PATH
    if not path.exists():
        return
    from .botconf import configured_bot_ids
    default_bot = (configured_bot_ids() or [""])[0]
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        rg = _cols(conn, "role_groups")
        if rg and "bot_id" not in rg:
            conn.execute("""CREATE TABLE role_groups_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                bot_id TEXT NOT NULL DEFAULT '', group_openid TEXT, real_gid TEXT NOT NULL DEFAULT '',
                member_openid TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL CHECK (kind IN ('main','backup')),
                created_ts INTEGER NOT NULL, UNIQUE (bot_id, group_openid))""")
            conn.execute("""INSERT INTO role_groups_new(id, role_id, bot_id, group_openid, real_gid, member_openid, kind, created_ts)
                SELECT id, role_id, CASE WHEN group_openid IS NULL THEN '' ELSE ? END, group_openid, real_gid, member_openid, kind, created_ts
                FROM role_groups""", (default_bot,))
            conn.execute("DROP TABLE role_groups")
            conn.execute("ALTER TABLE role_groups_new RENAME TO role_groups")
        ms = _cols(conn, "messages")
        for col in ("day", "sign", "delivered_bot", "delivered_msg_id"):
            if ms and col not in ms:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        ad = _cols(conn, "admins")
        if ad and "bot_id" not in ad:
            conn.execute("""CREATE TABLE admins_new (bot_id TEXT NOT NULL DEFAULT '', member_openid TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '', created_ts INTEGER NOT NULL, PRIMARY KEY (bot_id, member_openid))""")
            conn.execute("INSERT INTO admins_new(bot_id, member_openid, note, created_ts) SELECT ?, member_openid, note, created_ts FROM admins",
                         (default_bot,))
            conn.execute("DROP TABLE admins")
            conn.execute("ALTER TABLE admins_new RENAME TO admins")
        ar = _cols(conn, "autoreplies")
        tl = _cols(conn, "trpg_logs")
        qg = _cols(conn, "qq_groups")
        if qg and "link_id" not in qg:
            conn.execute("ALTER TABLE qq_groups ADD COLUMN link_id INTEGER NOT NULL DEFAULT 0")
        if tl and "shared" not in tl:
            conn.execute("ALTER TABLE trpg_logs ADD COLUMN shared INTEGER NOT NULL DEFAULT 0")
        if ar and "send_mode" not in ar:
            conn.execute("ALTER TABLE autoreplies ADD COLUMN send_mode TEXT NOT NULL DEFAULT 'random'")
        rlines = _cols(conn, "room_lines")
        if rlines and "msg_id" not in rlines:
            conn.execute("ALTER TABLE room_lines ADD COLUMN msg_id TEXT NOT NULL DEFAULT ''")
        rl = _cols(conn, "roles")
        if rl and "is_npc" not in rl:
            conn.execute("ALTER TABLE roles ADD COLUMN is_npc INTEGER NOT NULL DEFAULT 0")
        wu = _cols(conn, "web_users")
        if wu and "role" not in wu:      # 旧库：第一个账号升为主账号
            conn.execute("ALTER TABLE web_users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
            conn.execute("ALTER TABLE web_users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE web_users ADD COLUMN note TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE web_users SET role='owner' WHERE id=(SELECT MIN(id) FROM web_users)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    migrate(DB_PATH, legacy=_migrate)
    from .botconf import import_env_bots
    import_env_bots()
    forget_bots()
    migrate_to_workspaces()
    for app_id in list_bot_ids(only_enabled=False):     # 每个机器人的工作区建表/升级
        if app_id:
            _init_workspace(app_id, workspace_path(app_id))


def _cfg_conn(key: str) -> sqlite3.Connection:
    return get_global_conn() if key.startswith(GLOBAL_CONFIG_PREFIXES) else get_conn()


def get_config(key: str, default: str = "") -> str:
    with _cfg_conn(key) as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with _cfg_conn(key) as conn:
        conn.execute(
            "INSERT INTO config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (key, value),
        )


def get_int(key: str, default: int) -> int:
    try:
        return int(get_config(key, str(default)))
    except ValueError:
        return default
