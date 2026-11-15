-- 0001 基线：架构 v2 之前的完整表结构（全部 IF NOT EXISTS，老库上跑也安全）。
-- 这个文件冻结了，不要再改。要加表、加列：新建 0002_xxx.sql（或 .py，里面写 def up(conn)），编号顺延。

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
    created_ts    INTEGER NOT NULL,
    is_npc        INTEGER NOT NULL DEFAULT 0
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

-- 关系线的闲谈和时间点（小手机里用）：kind=chat 是闲聊，time 是一个重要时间点（label 是自己写的时间）
CREATE TABLE IF NOT EXISTS rel_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_id       INTEGER NOT NULL REFERENCES relations(id) ON DELETE CASCADE,
    from_role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('chat', 'time')),
    label        TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    created_ts   INTEGER NOT NULL,
    edited_ts    INTEGER NOT NULL DEFAULT 0
);

-- 小手机小游戏：排行榜（每人每游戏取最高分）和每天的开局次数
CREATE TABLE IF NOT EXISTS game_scores (
    game       TEXT NOT NULL,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    score      INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (game, role_id)
);
CREATE TABLE IF NOT EXISTS game_attempts (
    game    TEXT NOT NULL,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    day     TEXT NOT NULL,
    n       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game, role_id, day)
);

-- 匿名舞会（额外）：面具+昵称的特别群聊。时间戳都是毫秒。真名只在后台出现。
CREATE TABLE IF NOT EXISTS masq_members (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id   INTEGER NOT NULL UNIQUE REFERENCES roles(id) ON DELETE CASCADE,
    offered   TEXT NOT NULL DEFAULT '[]',
    mask      TEXT NOT NULL DEFAULT '',
    nick      TEXT NOT NULL DEFAULT '',
    seen_id   INTEGER NOT NULL DEFAULT 0,
    joined_ts INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_masq_mask ON masq_members(mask) WHERE mask!='';
CREATE TABLE IF NOT EXISTS masq_msgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id    INTEGER NOT NULL DEFAULT 0,
    content    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'msg',
    mention_id INTEGER NOT NULL DEFAULT 0,
    deleted    INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS masq_invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id     INTEGER NOT NULL,
    to_id       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    cnt         INTEGER NOT NULL DEFAULT 0,
    created_ts  INTEGER NOT NULL,
    answered_ts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS masq_dance_msgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invite_id  INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS masq_reacts (
    msg_id     INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    emoji      TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (msg_id, role_id)
);
CREATE TABLE IF NOT EXISTS masq_sched (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    at   INTEGER NOT NULL,
    text TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS masq_archives (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    no         INTEGER NOT NULL,
    started_ts INTEGER NOT NULL DEFAULT 0,
    ended_ts   INTEGER NOT NULL,
    guests     INTEGER NOT NULL DEFAULT 0,
    msgs       INTEGER NOT NULL DEFAULT 0,
    transcript TEXT NOT NULL
);

-- 朋友圈（额外）：帖子、图片（大图+缩略图直接存库）、赞、评论；删除都是软删（图片删了会清空二进制腾空间，行留着）
CREATE TABLE IF NOT EXISTS phone_favs (
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (role_id, message_id)
);
CREATE TABLE IF NOT EXISTS precall_entries (
    no      INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER NOT NULL UNIQUE REFERENCES roles(id) ON DELETE CASCADE,
    text    TEXT NOT NULL DEFAULT '',
    grp     TEXT NOT NULL DEFAULT '',
    hidden  INTEGER NOT NULL DEFAULT 0,
    ts      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS precall_groups (
    name TEXT PRIMARY KEY,
    open INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS precall_picks (
    from_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    entry_no INTEGER NOT NULL DEFAULT 0,
    ts       INTEGER NOT NULL,
    PRIMARY KEY (from_id, to_id)
);
CREATE TABLE IF NOT EXISTS precall_calls (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    entry_no INTEGER NOT NULL DEFAULT 0,
    room_id  INTEGER,
    ts       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS moments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    day_label  TEXT NOT NULL DEFAULT '',
    deleted    INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moments_role ON moments(role_id, created_ts);
CREATE TABLE IF NOT EXISTS moment_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    moment_id  INTEGER NOT NULL REFERENCES moments(id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    data       BLOB NOT NULL,
    thumb      BLOB NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    width      INTEGER NOT NULL DEFAULT 0,
    height     INTEGER NOT NULL DEFAULT 0,
    seq        INTEGER NOT NULL DEFAULT 0,
    deleted    INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS moment_likes (
    moment_id  INTEGER NOT NULL REFERENCES moments(id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (moment_id, role_id)
);
CREATE TABLE IF NOT EXISTS moment_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    moment_id   INTEGER NOT NULL REFERENCES moments(id) ON DELETE CASCADE,
    role_id     INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    reply_to_id INTEGER REFERENCES roles(id) ON DELETE SET NULL,
    content     TEXT NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    created_ts  INTEGER NOT NULL
);

-- 公开播报：机器人往公告群公开发的内容留底，小手机「公开播报」里翻
CREATE TABLE IF NOT EXISTS public_feed (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'other',
    text TEXT NOT NULL
);

-- 小手机：微信名、给对方的备注、头像（256×256 JPEG 直接存库）
CREATE TABLE IF NOT EXISTS role_nicks (
    role_id    INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    nick       TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS role_remarks (
    owner_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    target_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    remark     TEXT NOT NULL,
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (owner_id, target_id)
);
CREATE TABLE IF NOT EXISTS role_avatars (
    role_id    INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    data       BLOB NOT NULL,
    updated_ts INTEGER NOT NULL
);

-- 关系线已读位置：每人每条线，闲谈和细节各记看到哪一条（比这个新、且不是自己写的，就算未读）
CREATE TABLE IF NOT EXISTS rel_seen (
    rel_id      INTEGER NOT NULL REFERENCES relations(id) ON DELETE CASCADE,
    role_id     INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    chat_seen   INTEGER NOT NULL DEFAULT 0,
    detail_seen INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (rel_id, role_id)
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
-- 兑换：N 个 A 换 M 个 B（物品、货币都行），玩家用「兑换」指令自己换
CREATE TABLE IF NOT EXISTS rpg_exchanges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    from_code TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE CASCADE,
    from_n    INTEGER NOT NULL CHECK (from_n > 0),
    to_code   TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE CASCADE,
    to_n      INTEGER NOT NULL CHECK (to_n > 0),
    enabled   INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
-- 合成：几种材料（每种 n 个）合成一种物品（out_n 个）
CREATE TABLE IF NOT EXISTS rpg_recipes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    out_code   TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE CASCADE,
    out_n      INTEGER NOT NULL CHECK (out_n > 0),
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rpg_recipe_items (
    recipe_id INTEGER NOT NULL REFERENCES rpg_recipes(id) ON DELETE CASCADE,
    item_code TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE CASCADE,
    n         INTEGER NOT NULL CHECK (n > 0),
    PRIMARY KEY (recipe_id, item_code)
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
    chars     INTEGER NOT NULL DEFAULT 0,
    msg_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS room_lines_room ON room_lines(room_id, id);
CREATE TABLE IF NOT EXISTS room_members (
    room_id    INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    entered_ts INTEGER,
    agree      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (room_id, role_id)
);

-- 目击系统（额外）：既是记录也是发件箱。role_id = 收到提示的人；kind: saw 看见别人 / seen 被别人看见
CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('saw', 'seen')),
    room_id       INTEGER,
    other_room_id INTEGER,
    place         TEXT NOT NULL DEFAULT '',
    day           INTEGER,
    hm            TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL,
    anon          INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    error         TEXT NOT NULL DEFAULT '',
    created_ts    INTEGER NOT NULL,
    sent_ts       INTEGER
);
CREATE INDEX IF NOT EXISTS sightings_role ON sightings(role_id, id);

-- 礼品店（额外）：编号就是 id；玩家送礼可以直接写编号
CREATE TABLE IF NOT EXISTS gift_shop (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    note       TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL
);

-- 抽取池（额外）：奖品是物品库里的物品；次数和保底进度按 玩家×池子 记
CREATE TABLE IF NOT EXISTS gacha_pools (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    note       TEXT NOT NULL DEFAULT '',
    pity_n     INTEGER NOT NULL DEFAULT 0,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gacha_prizes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id   INTEGER NOT NULL REFERENCES gacha_pools(id) ON DELETE CASCADE,
    item_code TEXT NOT NULL REFERENCES rpg_items(code) ON DELETE CASCADE,
    n         INTEGER NOT NULL DEFAULT 1,
    weight    INTEGER NOT NULL DEFAULT 1,
    rare      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(pool_id, item_code)
);
CREATE TABLE IF NOT EXISTS gacha_state (
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    pool_id    INTEGER NOT NULL REFERENCES gacha_pools(id) ON DELETE CASCADE,
    tickets    INTEGER NOT NULL DEFAULT 0,
    since_rare INTEGER NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role_id, pool_id)
);
CREATE TABLE IF NOT EXISTS gacha_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    pool_id   INTEGER NOT NULL REFERENCES gacha_pools(id) ON DELETE CASCADE,
    item_code TEXT NOT NULL,
    n         INTEGER NOT NULL,
    pity      INTEGER NOT NULL DEFAULT 0,
    ts        INTEGER NOT NULL
);

-- 定时收集：repeat_days 天，每天同一时段一轮（round 从 0 数）；每人每轮一份，窗口内重交会覆盖
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    start_ts    INTEGER NOT NULL,
    end_ts      INTEGER NOT NULL,
    repeat_days INTEGER NOT NULL DEFAULT 1,
    closed      INTEGER NOT NULL DEFAULT 0,
    created_ts  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS collect_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    round         INTEGER NOT NULL DEFAULT 0,
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    ts            INTEGER NOT NULL,
    UNIQUE(collection_id, round, role_id)
);

-- 点歌（额外）：回复音乐卡片或按歌名搜，发到公告群；day 用来数每天的次数，announced=1 表示公告群已发出
CREATE TABLE IF NOT EXISTS song_requests (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    signer    TEXT NOT NULL DEFAULT '',
    to_name   TEXT NOT NULL DEFAULT '',
    platform  TEXT NOT NULL DEFAULT '',
    song_id   TEXT NOT NULL DEFAULT '',
    title     TEXT NOT NULL,
    artist    TEXT NOT NULL DEFAULT '',
    url       TEXT NOT NULL DEFAULT '',
    cover     TEXT NOT NULL DEFAULT '',
    message   TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT '',
    announced INTEGER NOT NULL DEFAULT 0,
    day       TEXT NOT NULL DEFAULT '',
    ts        INTEGER NOT NULL
);

-- 漂流瓶（额外）：抛瓶人和捡瓶人是一对，双方用同一个编号来回通信
CREATE TABLE IF NOT EXISTS drift_bottles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thrower_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    catcher_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_ts INTEGER NOT NULL
);
-- 每次扔瓶/回信一行，用来数每天的次数
CREATE TABLE IF NOT EXISTS drift_msgs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bottle_id INTEGER NOT NULL REFERENCES drift_bottles(id) ON DELETE CASCADE,
    from_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    content   TEXT NOT NULL,
    day       TEXT NOT NULL DEFAULT '',
    ts        INTEGER NOT NULL
);

-- 混乱寄信（额外）：每封信一行；true_to_id 是真正收到的人（误投时和 to_id 不同）
CREATE TABLE IF NOT EXISTS chaos_letters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_id      INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    true_to_id INTEGER NOT NULL,
    content    TEXT NOT NULL,
    shown      TEXT NOT NULL DEFAULT '',
    effects    TEXT NOT NULL DEFAULT '',
    day        TEXT NOT NULL DEFAULT '',
    ts         INTEGER NOT NULL
);

-- 闹钟（加百列）：个人提醒（repeat: none / daily / weekly:N）和管理员的群体闹钟
CREATE TABLE IF NOT EXISTS alarms (
    id         TEXT PRIMARY KEY,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    trigger_ts INTEGER NOT NULL,
    content    TEXT NOT NULL,
    repeat     TEXT NOT NULL DEFAULT 'none',
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alarms_trigger ON alarms(trigger_ts);
CREATE TABLE IF NOT EXISTS group_alarms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_ts INTEGER NOT NULL,
    content    TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);

-- 短信/礼物带的图片：存在库里（和公知二表的图一样），消息删了图跟着删
CREATE TABLE IF NOT EXISTS message_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id     INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    mime       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    data       BLOB NOT NULL,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_images_msg ON message_images(msg_id);

-- 上帝模式：功能权限（有记录 = 这个角色被关掉了这项功能）、时间锁定、手动重置当天次数（记「当天少算几次」）
CREATE TABLE IF NOT EXISTS role_perms (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    feature TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    PRIMARY KEY (role_id, feature)
);
CREATE TABLE IF NOT EXISTS time_locks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    day      INTEGER,
    start_hm TEXT NOT NULL,
    end_hm   TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    ts       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS count_resets (
    role_id INTEGER NOT NULL,
    kind    TEXT NOT NULL,
    dkey    TEXT NOT NULL,
    n       INTEGER NOT NULL,
    PRIMARY KEY (role_id, kind, dkey)
);

-- 结戏奖励（额外扩展）：规则、发放记录（每场每人每条规则只发一次）、管理员调整过的场次数据
CREATE TABLE IF NOT EXISTS reward_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    kinds      TEXT NOT NULL DEFAULT '',
    conds      TEXT NOT NULL DEFAULT '{}',
    fixed      TEXT NOT NULL DEFAULT '[]',
    pool       TEXT NOT NULL DEFAULT '[]',
    pool_draws INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reward_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id   INTEGER NOT NULL,
    role_id   INTEGER NOT NULL,
    rule_id   INTEGER NOT NULL,
    rule_name TEXT NOT NULL DEFAULT '',
    ok        INTEGER NOT NULL DEFAULT 1,
    detail    TEXT NOT NULL DEFAULT '',
    ts        INTEGER NOT NULL,
    UNIQUE (room_id, role_id, rule_id)
);
CREATE TABLE IF NOT EXISTS reward_adjust (
    room_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    segs    INTEGER NOT NULL DEFAULT 0,
    chars   INTEGER NOT NULL DEFAULT 0,
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
