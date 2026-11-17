-- 0006：取消角色的「备用群」，每个角色只保留一个个人群（旧的备用群和对应的认群码一并清掉）
DELETE FROM bind_codes WHERE kind = 'backup';
DELETE FROM role_groups WHERE kind = 'backup';
