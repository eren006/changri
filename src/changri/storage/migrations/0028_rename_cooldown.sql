-- 0028：改名冷却。name_ts：上次自己改本名的时间（0 = 没改过；建角色后第一次改名不受冷却限制，方便改错字）。
ALTER TABLE role_profiles ADD COLUMN name_ts INTEGER NOT NULL DEFAULT 0;
