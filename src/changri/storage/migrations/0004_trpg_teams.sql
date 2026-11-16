-- 0004：跑团队伍（/team）：每个群可以有几个队伍，成员是跑团玩家
CREATE TABLE IF NOT EXISTS trpg_teams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    name         TEXT NOT NULL,
    created_ts   INTEGER NOT NULL,
    UNIQUE (group_openid, name)
);
CREATE TABLE IF NOT EXISTS trpg_team_members (
    team_id   INTEGER NOT NULL REFERENCES trpg_teams(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES trpg_players(id) ON DELETE CASCADE,
    added_ts  INTEGER NOT NULL,
    PRIMARY KEY (team_id, player_id)
);
