-- 0005：历史季度档案。结束/收尾季度时把本季的统计和房间复盘存一份，收尾清空数据后还能在「季度」页回看。
CREATE TABLE IF NOT EXISTS season_archives (
    season_id  INTEGER PRIMARY KEY REFERENCES seasons(id) ON DELETE CASCADE,
    created_ts INTEGER NOT NULL,
    summary    TEXT NOT NULL DEFAULT '{}',   -- JSON：角色、各项数量、每个玩家的数据
    rooms      BLOB                          -- gzip 的 JSON：每场房间的参与者和全部发言
);
