-- 0003：跑团角色卡的成长标记（COC：技能检定成功就记一笔，团后 /en 一次掷完）
CREATE TABLE IF NOT EXISTS trpg_growth_marks (
    card_id INTEGER NOT NULL REFERENCES trpg_cards(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    PRIMARY KEY (card_id, name)
);
