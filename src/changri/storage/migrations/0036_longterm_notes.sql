-- 长期群玩家自己的「条目」：像备忘录，玩家随时加一栏（比如「魔杖」），下面写几个字段（木材、杖芯……），都不是必填。
-- JSON：{"魔杖": {"木材": "冬青", "杖芯": "凤凰羽毛"}, "宠物": {}}，按加的先后排。
ALTER TABLE lt_members ADD COLUMN notes TEXT NOT NULL DEFAULT '{}';
