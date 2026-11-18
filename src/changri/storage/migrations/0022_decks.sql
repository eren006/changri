-- 牌堆：一份「牌堆文件」是一个人上传的一份模板，里面可以有好几个牌堆（主牌堆 + 给别的牌堆用的辅助牌堆）。
-- raw 存整理成长日格式的原文（小手机里编辑看到的就是它）；decks 是解析好的，抽的时候只读这张表。
CREATE TABLE IF NOT EXISTS deck_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER,                         -- 上传的角色；NULL = 管理员在后台建的
    title      TEXT NOT NULL DEFAULT '',
    raw        TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id  INTEGER NOT NULL REFERENCES deck_files(id) ON DELETE CASCADE,
    name     TEXT NOT NULL COLLATE NOCASE UNIQUE,
    hidden   INTEGER NOT NULL DEFAULT 0,        -- 名字以 _ 开头：只给别的牌堆用，不能直接抽，不进列表
    cards    TEXT NOT NULL DEFAULT '[]'         -- JSON：[[权重, 内容], ...]
);
CREATE INDEX IF NOT EXISTS idx_decks_file ON decks(file_id);
