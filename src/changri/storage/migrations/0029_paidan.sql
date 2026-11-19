-- 0029：排单（个性化模块，默认关）。自己的表，前缀 pd_，不碰恋综的数据。设计见 docs/排单设计.md。
-- 日期一律存 'YYYY-MM-DD'（本地日期）；到期日 / DDL 当天还有效，第二天 0 点起算过期。

-- 业务：A / B / C。letter 是单号前缀，seq 是这个业务已经编到几号。
CREATE TABLE IF NOT EXISTS pd_businesses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    letter        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    days          INTEGER NOT NULL DEFAULT 7,        -- 工期天数
    default_slots INTEGER NOT NULL DEFAULT 1,        -- 默认单口
    template      TEXT NOT NULL DEFAULT '',          -- 下单格式（不含第一行标记）
    seq           INTEGER NOT NULL DEFAULT 0,
    group_openid  TEXT NOT NULL DEFAULT '',          -- 工作群
    created_ts    INTEGER NOT NULL
);

-- 客户：固定一个人，有自己的客户群。
CREATE TABLE IF NOT EXISTS pd_clients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    group_openid  TEXT NOT NULL DEFAULT '',
    member_openid TEXT NOT NULL DEFAULT '',
    qq            TEXT NOT NULL DEFAULT '',          -- 选填：owner 在网页上填；下单格式里填了以格式为准
    real_gid      TEXT NOT NULL DEFAULT '',          -- 客户群的真实群号（官方机器人看不到，建客户时 owner 填），私聊播报里带上方便找群
    note          TEXT NOT NULL DEFAULT '',
    archived      INTEGER NOT NULL DEFAULT 0,        -- 1 = 已完结：不能再下单、不提醒、列表里收起来；数据保留，可以恢复
    created_ts    INTEGER NOT NULL
);

-- 客户每个业务的单口（同时最多几单没完成）。custom=1 表示单独改过，业务默认值变了不跟着变。
CREATE TABLE IF NOT EXISTS pd_slots (
    client_id   INTEGER NOT NULL REFERENCES pd_clients(id) ON DELETE CASCADE,
    business_id INTEGER NOT NULL REFERENCES pd_businesses(id) ON DELETE CASCADE,
    slots       INTEGER NOT NULL,
    custom      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (client_id, business_id)
);

-- 额度：一笔一笔录。
CREATE TABLE IF NOT EXISTS pd_quotas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES pd_clients(id) ON DELETE CASCADE,
    business_id INTEGER NOT NULL REFERENCES pd_businesses(id) ON DELETE CASCADE,
    total       INTEGER NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0,
    expires     TEXT NOT NULL,
    created_ts  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS pd_quotas_cb ON pd_quotas(client_id, business_id);

-- 延期记录（暂停顺延也记，by='pause'）。
CREATE TABLE IF NOT EXISTS pd_quota_extends (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    quota_id INTEGER NOT NULL REFERENCES pd_quotas(id) ON DELETE CASCADE,
    old_date TEXT NOT NULL,
    new_date TEXT NOT NULL,
    by       TEXT NOT NULL DEFAULT 'owner',
    ts       INTEGER NOT NULL
);

-- 订单。client_id 为空 = 散单。status：open 在排 / done 已完成 / cancelled 已取消。
CREATE TABLE IF NOT EXISTS pd_orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    no             TEXT NOT NULL UNIQUE,              -- A-0001
    business_id    INTEGER NOT NULL REFERENCES pd_businesses(id),
    client_id      INTEGER REFERENCES pd_clients(id) ON DELETE SET NULL,
    orderer_openid TEXT NOT NULL DEFAULT '',          -- 散单的下单人（交付时 @ 他）
    orderer_name   TEXT NOT NULL DEFAULT '',
    qq             TEXT NOT NULL DEFAULT '',          -- 下单格式里的 QQ号（没填用客户档案里的），私聊 owner 时显示
    redeem_code    TEXT NOT NULL DEFAULT '',
    content        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
    locked         INTEGER NOT NULL DEFAULT 0,        -- 已上传所有图
    images_in      INTEGER NOT NULL DEFAULT 0,        -- 补过几张图
    images_out     INTEGER NOT NULL DEFAULT 0,        -- 发送过几张图（>0 = 已发图，待扣张数）
    deadline       TEXT NOT NULL,
    charged        INTEGER NOT NULL DEFAULT 0,        -- 扣了几张（散单只记录）
    shortfall      INTEGER NOT NULL DEFAULT 0,        -- 额度不够，差几张
    created_ts     INTEGER NOT NULL,
    done_ts        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS pd_orders_status ON pd_orders(status, business_id);

-- 一单从哪几笔额度各扣了几张（撤销时原样退回）。
CREATE TABLE IF NOT EXISTS pd_charges (
    order_id INTEGER NOT NULL REFERENCES pd_orders(id) ON DELETE CASCADE,
    quota_id INTEGER NOT NULL REFERENCES pd_quotas(id) ON DELETE CASCADE,
    n        INTEGER NOT NULL,
    PRIMARY KEY (order_id, quota_id)
);

-- 散单兑换码：一个码一单。
CREATE TABLE IF NOT EXISTS pd_codes (
    code        TEXT PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES pd_businesses(id) ON DELETE CASCADE,
    expires     TEXT NOT NULL,
    order_id    INTEGER REFERENCES pd_orders(id) ON DELETE SET NULL,
    used_by     TEXT NOT NULL DEFAULT '',
    used_ts     INTEGER NOT NULL DEFAULT 0,
    voided      INTEGER NOT NULL DEFAULT 0,
    created_ts  INTEGER NOT NULL
);

-- 暂停的日子。applied=1 表示这天的顺延已经做过（防止重复加一天）。
CREATE TABLE IF NOT EXISTS pd_pauses (
    day        TEXT PRIMARY KEY,
    applied    INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);

-- 绑定码：kind = work（业务工作群）/ client（客户群 + 本人）/ sandan（散单群）/ owner。用一次就删。
CREATE TABLE IF NOT EXISTS pd_bind_codes (
    code       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('work','client','sandan','owner')),
    target_id  INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
