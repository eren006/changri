-- 0017：小手机网页推送（照老系统 Web Push）：一台设备（浏览器）一行；同一个角色最多留 5 台。
CREATE TABLE IF NOT EXISTS push_subs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);
