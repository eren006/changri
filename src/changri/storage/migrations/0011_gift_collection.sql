-- 0011：礼品店图鉴（找回老系统的玩法）：发「礼品店」逛一逛，随机拿到一件还没有的礼物收进图鉴，有冷却；只能送图鉴里有的礼品店礼物
CREATE TABLE IF NOT EXISTS gift_unlocks (
    role_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    item_id  INTEGER NOT NULL REFERENCES gift_shop(id) ON DELETE CASCADE,
    ts       INTEGER NOT NULL,
    PRIMARY KEY (role_id, item_id)
);

-- 每人货架上现在摆的是哪件、什么时候换的（换的间隔 = 冷却）
CREATE TABLE IF NOT EXISTS gift_shop_visits (
    role_id      INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    item_id      INTEGER,
    refreshed_ts INTEGER NOT NULL
);
