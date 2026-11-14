"""唯一的数据源：一个 SQLite 文件。bot 和设置平台都通过这里读写。"""
import os
import sqlite3
from pathlib import Path

import contextlib
import contextvars
import shutil
import threading

DB_PATH = Path(os.getenv("CHANGRI_DB_PATH") or Path(__file__).resolve().parent.parent / "data" / "changri.db")   # 全局库
BOTS_DIR = DB_PATH.parent / "bots"                       # 每个机器人一个工作区：bots/<AppID>/changri.db
LEGACY_SINGLE = bool(os.getenv("CHANGRI_DB_PATH"))        # 测试用：指定了单个库文件就不分工作区，全部落在这一个库里

_current: contextvars.ContextVar[str] = contextvars.ContextVar("changri_bot", default="")
_default_bot: str | None = None                          # 没指定机器人时用的那个（第一个启用的）；None = 还没查过
_inited: set[str] = set()                                # 已建好表的工作区
_lock = threading.Lock()

# 属于整个程序、不跟机器人走的设置项（按前缀）。其余设置都在各机器人自己的库里。
GLOBAL_CONFIG_PREFIXES = ("remote_", "update_", "ext:", "ext_", "heartbeat:")

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 角色：一个玩家一个。member_openid 是创建时看到的发言人标识
CREATE TABLE IF NOT EXISTS roles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    member_openid TEXT NOT NULL,
    created_ts    INTEGER NOT NULL
);

-- 角色的群：每个角色一个主群（个人群）+ 若干备用群。一个群只能属于一个角色。
-- 一个平台可以同时接多个机器人：group_openid / member_openid 都是「每个机器人各一套」，所以要记 bot_id，发消息时用对应的机器人。
-- group_openid 是机器人在群里见到的标识，发消息靠它；real_gid 是真实群号，官方机器人读不到，只当标签。
-- group_openid 为空 = 后台先登记了群号，还没在群里「认群」，这时不参与投递。
CREATE TABLE IF NOT EXISTS role_groups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    bot_id        TEXT NOT NULL DEFAULT '',
    group_openid  TEXT,
    real_gid      TEXT NOT NULL DEFAULT '',
    member_openid TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL CHECK (kind IN ('main', 'backup')),
    created_ts    INTEGER NOT NULL,
    UNIQUE (bot_id, group_openid)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_main_per_role ON role_groups(role_id) WHERE kind = 'main';

-- 认群码：在群里发「认群 码」把那个群的 openid 补到对应的群位上。
-- slot_id 不为空 = 后台登记了群号生成的码（认这一个群位）；为空 = 玩家自己生成的码（kind 决定加备用群还是换主群）
CREATE TABLE IF NOT EXISTS bind_codes (
    code       TEXT PRIMARY KEY,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('main', 'backup')),
    slot_id    INTEGER REFERENCES role_groups(id) ON DELETE CASCADE,
    expires_ts INTEGER NOT NULL
);

-- 短信 / 礼物
CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL CHECK (kind IN ('sms', 'gift')),
    from_role_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_role_id     INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    title          TEXT NOT NULL DEFAULT '',
    content        TEXT NOT NULL DEFAULT '',
    created_ts     INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    delivered_group TEXT,
    delivered_ts   INTEGER,
    error          TEXT NOT NULL DEFAULT '',
    day            TEXT NOT NULL DEFAULT '',
    sign           TEXT NOT NULL DEFAULT '',
    delivered_bot  TEXT NOT NULL DEFAULT '',
    delivered_msg_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(status, created_ts);
-- 拉黑：blocker 单方面屏蔽 blocked 对自己发起的短信/礼物。silent=1 时对方不知道自己被拉黑
CREATE TABLE IF NOT EXISTS blocks (
    blocker_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    silent     INTEGER NOT NULL DEFAULT 0,
    since_ts   INTEGER NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id)
);

-- 心动信：先进信池，到派送时间统一发。day 是自然日（YYYY-MM-DD），用来计每天的投稿次数；派送后保留行（status='sent'）
CREATE TABLE IF NOT EXISTS lovemails (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_role_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    signature    TEXT NOT NULL DEFAULT '匿名',
    content      TEXT NOT NULL,
    day          TEXT NOT NULL,
    created_ts   INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pool',
    sent_ts      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_lovemails_pool ON lovemails(status, to_role_id);
CREATE INDEX IF NOT EXISTS idx_lovemails_day ON lovemails(from_role_id, day);

-- 关系线：一对角色一条（role_lo < role_hi），细节按先后追加
CREATE TABLE IF NOT EXISTS relations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    role_lo      INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    role_hi      INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    initiator_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    confirmed    INTEGER NOT NULL DEFAULT 0,
    created_ts   INTEGER NOT NULL,
    UNIQUE (role_lo, role_hi)
);
CREATE TABLE IF NOT EXISTS rel_details (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_id       INTEGER NOT NULL REFERENCES relations(id) ON DELETE CASCADE,
    from_role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    created_ts   INTEGER NOT NULL
);

-- 管理员：不需要有角色。后台生成管理员码，被指定的人在任意群里发「认管理员 码」，机器人记下他的 member_openid
CREATE TABLE IF NOT EXISTS admins (
    bot_id        TEXT NOT NULL DEFAULT '',
    member_openid TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    created_ts    INTEGER NOT NULL,
    PRIMARY KEY (bot_id, member_openid)
);

-- 设置平台的登录账号（和上面的 QQ 管理员是两回事）。owner = 主账号（只有一个，不能删、不能停用，独享机器人密钥管理）；admin = 主账号发出去的管理员账号
CREATE TABLE IF NOT EXISTS web_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('owner', 'admin')),
    disabled      INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    created_ts    INTEGER NOT NULL,
    last_login_ts INTEGER
);
CREATE TABLE IF NOT EXISTS bot_accounts (
    app_id     TEXT PRIMARY KEY,
    secret     TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS role_attrs (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    value   INTEGER NOT NULL,
    PRIMARY KEY (role_id, name)
);
-- RPG：部分规则改编自「长日将尽」CC BY-NC-SA 协议作品（联系 3052553938）
CREATE TABLE IF NOT EXISTS rpg_attr_defs (
    name          TEXT PRIMARY KEY,
    min_value     INTEGER,
    max_value     INTEGER,
    default_value INTEGER NOT NULL DEFAULT 0,
    description   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rpg_items (
    code         TEXT PRIMARY KEY COLLATE NOCASE,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind         TEXT NOT NULL DEFAULT 'item' CHECK (kind IN ('item','currency')),
    description  TEXT NOT NULL DEFAULT '',
    attr_effects TEXT NOT NULL DEFAULT '{}',
    max_uses     INTEGER NOT NULL DEFAULT -1,
    can_give     INTEGER NOT NULL DEFAULT 1,
    created_ts   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rpg_inventory (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id        INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    item_code      TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE RESTRICT,
    count          INTEGER NOT NULL CHECK (count > 0),
    remaining_uses INTEGER NOT NULL DEFAULT -1,
    expires_ts     INTEGER,
    UNIQUE(role_id, item_code, remaining_uses, expires_ts)
);
CREATE TABLE IF NOT EXISTS rpg_shop (
    item_code     TEXT PRIMARY KEY REFERENCES rpg_items(code) ON DELETE CASCADE,
    price         INTEGER NOT NULL CHECK (price > 0),
    currency_code TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS rpg_places (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    locked      INTEGER NOT NULL DEFAULT 0,
    created_ts  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rpg_place_keys (
    role_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    place_id INTEGER NOT NULL REFERENCES rpg_places(id) ON DELETE CASCADE,
    PRIMARY KEY(role_id, place_id)
);
CREATE TABLE IF NOT EXISTS rpg_item_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id    INTEGER REFERENCES roles(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    item_code  TEXT NOT NULL,
    count      INTEGER NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL
);
-- 跑团模块：玩家、角色卡、属性变动历史（跟恋综的 roles / role_attrs 完全分开，见 docs/跑团模块设计.md）
CREATE TABLE IF NOT EXISTS trpg_players (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trpg_identities (
    member_openid TEXT PRIMARY KEY,
    player_id     INTEGER NOT NULL REFERENCES trpg_players(id) ON DELETE CASCADE,
    group_openid  TEXT NOT NULL DEFAULT '',
    created_ts    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trpg_cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id  INTEGER NOT NULL REFERENCES trpg_players(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    rule       TEXT NOT NULL DEFAULT 'coc',
    note       TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL,
    UNIQUE(player_id, name)
);
CREATE TABLE IF NOT EXISTS trpg_card_attrs (
    card_id INTEGER NOT NULL REFERENCES trpg_cards(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    value   INTEGER NOT NULL,
    PRIMARY KEY (card_id, name)
);
CREATE TABLE IF NOT EXISTS trpg_active (
    group_openid TEXT NOT NULL,
    player_id    INTEGER NOT NULL REFERENCES trpg_players(id) ON DELETE CASCADE,
    card_id      INTEGER NOT NULL REFERENCES trpg_cards(id) ON DELETE CASCADE,
    PRIMARY KEY (group_openid, player_id)
);
CREATE TABLE IF NOT EXISTS trpg_attr_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id   INTEGER NOT NULL REFERENCES trpg_cards(id) ON DELETE CASCADE,
    log_id    INTEGER,
    ts        INTEGER NOT NULL,
    name      TEXT NOT NULL,
    old_value INTEGER,
    new_value INTEGER,
    source    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS trpg_history_card ON trpg_attr_history(card_id, id);
CREATE TABLE IF NOT EXISTS trpg_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'on' CHECK (status IN ('on','off','ended','halted')),
    token      TEXT NOT NULL,
    started_by TEXT NOT NULL DEFAULT '',
    started_ts INTEGER NOT NULL,
    ended_ts   INTEGER,
    palette    TEXT NOT NULL DEFAULT '{}',
    shared     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(scope, name)
);
CREATE TABLE IF NOT EXISTS trpg_log_lines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id       INTEGER NOT NULL REFERENCES trpg_logs(id) ON DELETE CASCADE,
    ts           INTEGER NOT NULL,
    speaker_key  TEXT NOT NULL DEFAULT '',
    speaker_name TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'say' CHECK (kind IN ('say','dice','bot')),
    text         TEXT NOT NULL,
    ref          INTEGER
);
CREATE INDEX IF NOT EXISTS trpg_log_lines_log ON trpg_log_lines(log_id, id);
CREATE TABLE IF NOT EXISTS trpg_log_members (
    log_id    INTEGER NOT NULL REFERENCES trpg_logs(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES trpg_players(id) ON DELETE CASCADE,
    card_id   INTEGER REFERENCES trpg_cards(id) ON DELETE SET NULL,
    PRIMARY KEY (log_id, player_id)
);
CREATE TABLE IF NOT EXISTS trpg_observers (
    group_openid TEXT NOT NULL,
    player_id    INTEGER NOT NULL REFERENCES trpg_players(id) ON DELETE CASCADE,
    PRIMARY KEY (group_openid, player_id)
);
-- QQ 号 ↔ 人（用 roles.member_openid 认人）：绑一次，这个人之后建的新角色也自动算他的，永久保存，后台可看可删
CREATE TABLE IF NOT EXISTS person_qq (
    qq            TEXT PRIMARY KEY,
    member_openid TEXT NOT NULL,
    created_ts    INTEGER NOT NULL
);
-- 一个官方机器人可以连多个 QQ 号（每个号一条连接），它们的群放在同一个群号池里
CREATE TABLE IF NOT EXISTS qq_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL,
    token      TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS qq_groups (
    gid          TEXT PRIMARY KEY,
    link_id      INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'listen' CHECK (kind IN ('listen', 'bot', 'room')),
    group_openid TEXT NOT NULL DEFAULT '',
    created_ts   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS seasons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    supp_end   TEXT NOT NULL DEFAULT '',
    npcs       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'running', 'ended')),
    started_ts INTEGER,
    ended_ts   INTEGER
);
CREATE TABLE IF NOT EXISTS entrance (
    role_id INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    pos     INTEGER NOT NULL,
    text    TEXT NOT NULL DEFAULT '',
    ts      INTEGER,
    done    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sheets (
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (role_id, kind)
);
CREATE TABLE IF NOT EXISTS sheet_images (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    filename TEXT NOT NULL,
    ord      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entrance_images (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    data    BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_codes (
    code       TEXT PRIMARY KEY,
    note       TEXT NOT NULL DEFAULT '',
    expires_ts INTEGER NOT NULL
);

-- 群池：机器人被拉进的群。管理员在设置平台填群号、分组、放进池；约会房间从池里领空群。
-- state: free 空闲 / occupied 占用中 / cleaning 约会结束，等管理员确认群里的人清空了再放回池
CREATE TABLE IF NOT EXISTS pool_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id       TEXT NOT NULL,
    group_openid TEXT NOT NULL,
    real_gid     TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT '',
    set_name     TEXT NOT NULL DEFAULT '',
    in_pool      INTEGER NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'free' CHECK (state IN ('free', 'occupied', 'cleaning')),
    room_id      INTEGER,
    added_ts     INTEGER NOT NULL,
    UNIQUE (bot_id, group_openid)
);

-- 约会房间：私约/电话/官约/微信/心愿。pool_id 为空 = 没领到群（群池用完），走网页聊天室
CREATE TABLE IF NOT EXISTS rooms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    creator_id     INTEGER,
    day            INTEGER,
    start_hm       TEXT NOT NULL DEFAULT '',
    end_hm         TEXT NOT NULL DEFAULT '',
    place          TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'ended', 'cancelled')),
    pool_id        INTEGER,
    web_code       TEXT NOT NULL DEFAULT '',
    dkey           TEXT NOT NULL DEFAULT '',
    pending_change TEXT NOT NULL DEFAULT '',
    created_ts     INTEGER NOT NULL,
    ended_ts       INTEGER
);
-- 房间群里监听到的发言（复盘用）：只记正在进行的房间群，房间删了（收尾）这些也一起清掉
CREATE TABLE IF NOT EXISTS room_lines (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    ts        INTEGER NOT NULL,
    qq        TEXT NOT NULL DEFAULT '',
    role_id   INTEGER,
    name      TEXT NOT NULL DEFAULT '',
    text      TEXT NOT NULL,
    is_rp     INTEGER NOT NULL DEFAULT 0,
    chars     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS room_lines_room ON room_lines(room_id, id);
CREATE TABLE IF NOT EXISTS room_members (
    room_id    INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    entered_ts INTEGER,
    agree      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (room_id, role_id)
);

-- 小手机：每个角色一个激活码；phone_seen 记玩家最近什么时候来过（后台看谁还没用过手机）
CREATE TABLE IF NOT EXISTS phone_codes (
    role_id    INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    code       TEXT NOT NULL UNIQUE,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS phone_seen (
    role_id    INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS join_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wishes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    poster_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    day        INTEGER,
    start_hm   TEXT NOT NULL DEFAULT '',
    end_hm     TEXT NOT NULL DEFAULT '',
    place      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    reward     TEXT NOT NULL DEFAULT '',
    sign       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'taken', 'withdrawn')),
    taker_id   INTEGER,
    room_id    INTEGER,
    dkey       TEXT NOT NULL DEFAULT '',
    taken_dkey TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    sign       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS replies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    floor      INTEGER NOT NULL,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    sign       TEXT NOT NULL,
    quote      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    UNIQUE (post_id, floor)
);
-- ref：帖子写 p<帖子id>，楼层写 r<回复id>
-- 自定义回复：词汇触发。words 一行一个触发词（任一命中即可），replies 是 JSON 数组（命中后随机发一条）
CREATE TABLE IF NOT EXISTS autoreplies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    mode       TEXT NOT NULL DEFAULT 'contains' CHECK (mode IN ('exact', 'contains', 'prefix', 'suffix', 'regex')),
    words      TEXT NOT NULL DEFAULT '',
    replies    TEXT NOT NULL DEFAULT '[]',
    who        TEXT NOT NULL DEFAULT 'all' CHECK (who IN ('all', 'role', 'admin')),
    place      TEXT NOT NULL DEFAULT 'all' CHECK (place IN ('all', 'personal', 'public')),
    cooldown   INTEGER NOT NULL DEFAULT 0,
    send_mode  TEXT NOT NULL DEFAULT 'random',
    sort       INTEGER NOT NULL DEFAULT 0,
    hits       INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    ref     TEXT NOT NULL,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    val     INTEGER NOT NULL,
    PRIMARY KEY (ref, role_id)
);
"""


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
        if path.exists():
            _migrate(path)
        conn = _connect(path)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
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
    _migrate()
    with get_global_conn() as conn:
        conn.executescript(SCHEMA)
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
