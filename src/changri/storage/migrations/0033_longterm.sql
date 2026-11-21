-- 长期群模式（恋综、跑团之外的第三种群模式）：后台先建长期群，QQ 群里「/模式 长期群」+「/切换长期群 名字」接上。
-- 每个长期群自己一套：注册的玩家、字数、货币、属性、背包。数据跟恋综的 roles、跑团的 trpg_* 都不相通。
CREATE TABLE IF NOT EXISTS lt_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    currency        TEXT NOT NULL DEFAULT '金币',     -- 货币名
    chars_per_step  INTEGER NOT NULL DEFAULT 1000,    -- 每多少字……
    coins_per_step  INTEGER NOT NULL DEFAULT 1,       -- ……换多少货币
    reg_fields      TEXT NOT NULL DEFAULT '',         -- 注册时要填的额外项，一行一个（名字之外）
    self_attrs      INTEGER NOT NULL DEFAULT 1,       -- 玩家能不能自己改属性
    count_admin_only INTEGER NOT NULL DEFAULT 0,      -- 字数统计只有管理员能交
    count_review    INTEGER NOT NULL DEFAULT 1,       -- 玩家交的字数要管理员在后台批准才入账
    checkin_on      INTEGER NOT NULL DEFAULT 0,       -- 每日签到
    checkin_rewards TEXT NOT NULL DEFAULT '',         -- 签到奖励，一行一项：「银两 10-30」「回血药水 1」「HP 1-3」
    kpi_period      TEXT NOT NULL DEFAULT 'week',     -- KPI 按 week 每周（周一起）/ month 每月 / all 累计 算
    kpi_chars       INTEGER NOT NULL DEFAULT 0,       -- KPI：字数至少多少，0 = 不考核
    kpi_paras       INTEGER NOT NULL DEFAULT 0,       -- KPI：段数至少多少（合并转发里一条消息算一段），0 = 不考核
    created_ts      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lt_members (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id       INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    member_openid  TEXT NOT NULL DEFAULT '',          -- 后台手动加的人可以没有，等本人在群里注册时认上
    name           TEXT NOT NULL,
    aliases        TEXT NOT NULL DEFAULT '',          -- 合并转发里可能出现的其他名字（QQ 昵称等），逗号分开
    info           TEXT NOT NULL DEFAULT '{}',        -- 注册填的额外项 JSON
    chars          INTEGER NOT NULL DEFAULT 0,        -- 累计字数
    chars_left     INTEGER NOT NULL DEFAULT 0,        -- 还没换成货币的零头
    paras          INTEGER NOT NULL DEFAULT 0,        -- 累计段数（合并转发里一条消息算一段）
    coins          INTEGER NOT NULL DEFAULT 0,        -- 货币余额
    created_ts     INTEGER NOT NULL,
    UNIQUE(group_id, name)
);
CREATE INDEX IF NOT EXISTS idx_lt_members_openid ON lt_members(group_id, member_openid);

CREATE TABLE IF NOT EXISTS lt_attr_defs (
    group_id       INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    default_value  INTEGER NOT NULL DEFAULT 0,
    min_value      INTEGER,
    max_value      INTEGER,
    PRIMARY KEY(group_id, name)
);

CREATE TABLE IF NOT EXISTS lt_attrs (
    member_id  INTEGER NOT NULL REFERENCES lt_members(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    value      INTEGER NOT NULL,
    PRIMARY KEY(member_id, name)
);

CREATE TABLE IF NOT EXISTS lt_items (
    member_id  INTEGER NOT NULL REFERENCES lt_members(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    qty        INTEGER NOT NULL,
    PRIMARY KEY(member_id, name)
);

-- 道具（后台注册）：用掉时按 effects 改属性；上架就能在商城买
CREATE TABLE IF NOT EXISTS lt_item_defs (
    group_id     INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    effects      TEXT NOT NULL DEFAULT '',          -- 「HP+10 理智-5 力量=60」，空 = 不能使用（收藏品、任务物品）
    on_sale      INTEGER NOT NULL DEFAULT 0,        -- 上架
    price        INTEGER NOT NULL DEFAULT 0,
    stock        INTEGER NOT NULL DEFAULT -1,       -- 库存，-1 = 不限
    limit_total  INTEGER NOT NULL DEFAULT 0,        -- 每人一共最多买几个，0 = 不限
    limit_daily  INTEGER NOT NULL DEFAULT 0,        -- 每人每天最多买几个，0 = 不限
    sort         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(group_id, name)
);

-- 合并转发里出现过、还没对上注册玩家的名字：字数先记在名字下面，认领后转给玩家
CREATE TABLE IF NOT EXISTS lt_unclaimed (
    group_id   INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    chars      INTEGER NOT NULL DEFAULT 0,
    paras      INTEGER NOT NULL DEFAULT 0,
    last_ts    INTEGER NOT NULL,
    PRIMARY KEY(group_id, name)
);

-- 统计过的发言（名字 + 内容的哈希）：同一段转发重复交不会重复算
CREATE TABLE IF NOT EXISTS lt_counted (
    group_id   INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    hash       TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    PRIMARY KEY(group_id, hash)
);

-- 每次交的合并转发（字数统计）原样留底，后台能追溯：谁交的、哪天、每条记给了谁、算没算
CREATE TABLE IF NOT EXISTS lt_submissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    submitter_id    INTEGER REFERENCES lt_members(id) ON DELETE SET NULL,
    submitter_name  TEXT NOT NULL DEFAULT '',
    qq_group        TEXT NOT NULL DEFAULT '',          -- 在哪个 QQ 群交的（group_openid）
    n_lines         INTEGER NOT NULL DEFAULT 0,
    n_counted       INTEGER NOT NULL DEFAULT 0,
    total_chars     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'approved', -- pending 等批准 / approved 已入账 / rejected 驳回
    reviewed_ts     INTEGER,
    ts              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lt_submissions_group ON lt_submissions(group_id, id);

CREATE TABLE IF NOT EXISTS lt_sub_lines (
    submission_id  INTEGER NOT NULL REFERENCES lt_submissions(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    sender         TEXT NOT NULL,                     -- 转发里写的发送者
    member_id      INTEGER REFERENCES lt_members(id) ON DELETE SET NULL,   -- 记给了谁（没对上就空）
    text           TEXT NOT NULL,
    chars          INTEGER NOT NULL DEFAULT 0,
    counted        INTEGER NOT NULL DEFAULT 1,        -- 0 = 以前统计过，这次跳过
    hash           TEXT NOT NULL DEFAULT '',          -- 去重用；驳回时从 lt_counted 里拿掉，可以重新交
    PRIMARY KEY(submission_id, seq)
);

-- 流水：字数入账、货币变动、物品进出，后台看
CREATE TABLE IF NOT EXISTS lt_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    member_id  INTEGER REFERENCES lt_members(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                       -- 字数 / 货币 / 物品
    chars      INTEGER NOT NULL DEFAULT 0,
    paras      INTEGER NOT NULL DEFAULT 0,                -- 字数入账时的段数（KPI 按入账时间算）
    coins      INTEGER NOT NULL DEFAULT 0,
    item       TEXT NOT NULL DEFAULT '',
    qty        INTEGER NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',
    submission_id INTEGER,                          -- 字数统计来的：哪一次交的
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lt_ledger_member ON lt_ledger(member_id, id);

CREATE TABLE IF NOT EXISTS lt_checkins (
    member_id  INTEGER NOT NULL REFERENCES lt_members(id) ON DELETE CASCADE,
    day        TEXT NOT NULL,                       -- YYYY-MM-DD（机器人的时区）
    rewards    TEXT NOT NULL DEFAULT '',
    ts         INTEGER NOT NULL,
    PRIMARY KEY(member_id, day)
);

-- 商城购买记录：限购按它算
CREATE TABLE IF NOT EXISTS lt_purchases (
    member_id  INTEGER NOT NULL REFERENCES lt_members(id) ON DELETE CASCADE,
    item       TEXT NOT NULL,
    qty        INTEGER NOT NULL,
    day        TEXT NOT NULL,
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lt_purchases ON lt_purchases(member_id, item, day);

-- 合成 / 兑换配方：用掉 inputs，得到 outputs；两边都写「碎片×3 银两×10」，货币名就是货币
CREATE TABLE IF NOT EXISTS lt_recipes (
    group_id   INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    inputs     TEXT NOT NULL,
    outputs    TEXT NOT NULL,
    PRIMARY KEY(group_id, name)
);

-- 动态：后台「总览」的流水，一件事一行（交字数、批准、驳回、买、用、送、签到、合成、调整、发放、注册……）
CREATE TABLE IF NOT EXISTS lt_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   INTEGER NOT NULL REFERENCES lt_groups(id) ON DELETE CASCADE,
    member_id  INTEGER REFERENCES lt_members(id) ON DELETE SET NULL,
    kind       TEXT NOT NULL,                       -- 字数 / 商城 / 道具 / 赠送 / 签到 / 合成 / 管理 / 注册
    text       TEXT NOT NULL,
    ref_id     INTEGER,                             -- 字数统计的留底编号
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lt_events_group ON lt_events(group_id, id);
CREATE INDEX IF NOT EXISTS idx_lt_events_member ON lt_events(member_id, id);
