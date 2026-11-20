-- 0.22.12：礼物和心动信可以附图。
-- 照片礼物：带了图的礼物不走礼品店（不认编号、不进热度），photo=1 标出来。不靠「有没有图」判断：后台按天数清图以后图没了，标记还在。
ALTER TABLE messages ADD COLUMN photo INTEGER NOT NULL DEFAULT 0;

-- 心动信带的图片：跟短信的 message_images 一样存在库里，信删了图跟着删
CREATE TABLE IF NOT EXISTS lovemail_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_id    INTEGER NOT NULL REFERENCES lovemails(id) ON DELETE CASCADE,
    mime       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    data       BLOB NOT NULL,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lovemail_images_mail ON lovemail_images(mail_id);
