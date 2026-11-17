-- 0008：写信综（个性化模块）：正式信件、望远镜 / 羽毛笔道具效果。收尾时清空。
CREATE TABLE IF NOT EXISTS letters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    to_id      INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    date_tag   TEXT NOT NULL DEFAULT '',
    attachment TEXT NOT NULL DEFAULT '',
    signature  TEXT NOT NULL DEFAULT '',
    day        TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent', 'held', 'failed')),   -- held = 被羽毛笔截住，等改完再发
    held_by    INTEGER,
    edited     INTEGER NOT NULL DEFAULT 0,
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS letters_from ON letters(from_id, day);
CREATE TABLE IF NOT EXISTS letter_effects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('telescope', 'quill')),
    target_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    applier_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    ts         INTEGER NOT NULL
);

-- 特殊道具（系统自带）：窃听器 / 截信器 / 回音壁 装在某人身上，剩余条数用完失效；抄到的内容先进 tap_outbox，由定时任务发给放道具的人
CREATE TABLE IF NOT EXISTS item_taps (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL CHECK (kind IN ('phone', 'sms_out', 'sms_in')),
    target_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    owner_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    remain    INTEGER NOT NULL,
    blur      INTEGER NOT NULL DEFAULT 0,
    ts        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tap_outbox (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    text     TEXT NOT NULL,
    ts       INTEGER NOT NULL
);
