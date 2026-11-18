-- 0015：小手机匿名对话（照老系统）：谁(owner)对谁(target)用的什么化名。对同一个人最多 3 个化名；同一收件人下化名不重名。
-- blocked_by：'owner' / 'target' / ''（只拉黑这个化名，不牵涉真实身份）；拉黑满 2 小时才能解除。
CREATE TABLE IF NOT EXISTS phone_aliases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    target_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    blocked_by TEXT NOT NULL DEFAULT '',
    blocked_ts INTEGER NOT NULL DEFAULT 0,
    UNIQUE (target_id, alias)
);
-- 匿名对话里的短信：alias_id 不为空。普通会话列表、对话页不显示它们，只在匿名对话里显示。
ALTER TABLE messages ADD COLUMN alias_id INTEGER;
