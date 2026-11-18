-- 0014：偷取礼物（照老系统小手机礼品店）：从别人图鉴里偷一件。每次尝试一行（成没成、带没带走、有没有被发现）。
-- 群里「偷礼物 对方名」和小手机礼品店共用这一份；「偷盗记录」从这里读。
CREATE TABLE IF NOT EXISTS gift_steals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thief_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    victim_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    item_id    INTEGER,
    item_name  TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 0,
    taken      INTEGER NOT NULL DEFAULT 0,
    found      INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS gift_steals_thief ON gift_steals(thief_id, created_ts);
