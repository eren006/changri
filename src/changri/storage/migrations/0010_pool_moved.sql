-- 0010：一个群同一时间只归一方——QQ 小号的群号组（group_openid = qq:群号 那一行），或官方机器人的备用群（官方群编号那一行）。
-- 两行都留着，moved = 1 的那一行表示「这个群现在归另一边」：不分给房间、小号不听、只记着，方便以后一键转回来。
ALTER TABLE pool_groups ADD COLUMN moved INTEGER NOT NULL DEFAULT 0;
