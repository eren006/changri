-- 0013：小手机待回页的「暂不提醒」。key 认的是「这一次」等待（场次按上一句的时间、关系线按段数），对方再回一轮就是新的提醒。
-- 只影响小手机上的提醒（和新消息角标），群里「我的待回」照旧列出（照老系统）。
CREATE TABLE IF NOT EXISTS pending_dismiss (
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (role_id, key)
);
