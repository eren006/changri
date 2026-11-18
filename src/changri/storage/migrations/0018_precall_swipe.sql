-- 0018：前置电话换成老系统小手机的「滑卡」玩法（作者定的，替换原来的编号盲选）：
-- 玩家在小手机填一份公开资料（照片 / 介绍 / 性别）→ 翻别人的资料卡、选人 → 配对成功在小手机里开一个电话聊天页。纯网页，不建 QQ 群。
-- 原来的 precall_entries / precall_groups / precall_picks / precall_calls 留着不删（旧数据），新代码不再用。
CREATE TABLE IF NOT EXISTS precall_profiles (
    role_id    INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    pid        TEXT NOT NULL UNIQUE,              -- 对外的随机编号：网址、页面数据里不出现角色
    no         INTEGER NOT NULL,                  -- 匿名时显示的「匿名 #编号」
    gender     TEXT NOT NULL DEFAULT 'x',         -- m / f / x（不限）
    bio        TEXT NOT NULL DEFAULT '',
    photo      BLOB,                              -- 3:4 竖图 JPEG（600×800），没有就是 NULL
    hidden     INTEGER NOT NULL DEFAULT 0,        -- 管理员隐藏：别人翻不到
    updated_ts INTEGER NOT NULL DEFAULT 0
);
-- 选人：只记「选」，翻过去不记录。一轮里同一个人只能选一次。
CREATE TABLE IF NOT EXISTS precall_likes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    round   INTEGER NOT NULL DEFAULT 1,
    from_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    ts      INTEGER NOT NULL,
    UNIQUE (round, from_id, to_id)
);
-- 电话：一对人只开一次（role_lo < role_hi）。read_lo / read_hi = 读到哪条消息。
CREATE TABLE IF NOT EXISTS precall_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_lo    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    role_hi    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    round      INTEGER NOT NULL DEFAULT 1,
    ended      INTEGER NOT NULL DEFAULT 0,
    read_lo    INTEGER NOT NULL DEFAULT 0,
    read_hi    INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL,
    UNIQUE (role_lo, role_hi)
);
CREATE TABLE IF NOT EXISTS precall_msgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES precall_sessions(id) ON DELETE CASCADE,
    from_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS precall_msgs_session ON precall_msgs(session_id, id);
