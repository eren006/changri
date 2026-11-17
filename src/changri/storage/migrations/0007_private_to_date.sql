-- 0007：「私约」改名「约会」（0.21）。房间类型、每日上限设置、上帝模式的功能开关和次数调整、结戏奖励规则里的房间类型一起改。
-- 群里照旧发「私约 …」「结束私约」「强结私约」也认（指令别名），只是记下来都叫约会。
UPDATE rooms SET kind='约会' WHERE kind='私约';
INSERT OR IGNORE INTO config(key, value) SELECT 'limit_约会', value FROM config WHERE key='limit_私约';
DELETE FROM config WHERE key='limit_私约';
INSERT OR IGNORE INTO role_perms(role_id, feature, ts) SELECT role_id, '约会', ts FROM role_perms WHERE feature='私约';
DELETE FROM role_perms WHERE feature='私约';
INSERT OR IGNORE INTO count_resets(role_id, kind, dkey, n) SELECT role_id, '约会', dkey, n FROM count_resets WHERE kind='私约';
DELETE FROM count_resets WHERE kind='私约';
UPDATE reward_rules SET kinds=REPLACE(kinds, '私约', '约会') WHERE kinds LIKE '%私约%';
