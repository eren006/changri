-- 通用物品库：跨季度、跨机器人留着的物品模板（只用全局库里的这张表）。导入到某个机器人时重新编号。
CREATE TABLE IF NOT EXISTS universal_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind       TEXT NOT NULL DEFAULT 'item',
    entry_json TEXT NOT NULL DEFAULT '{}',
    created_ts INTEGER NOT NULL
);
