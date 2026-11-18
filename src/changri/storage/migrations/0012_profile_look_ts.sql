-- 0012：记下上次改皮相的时间（皮相 2 小时只能改一次，照老系统手册）
ALTER TABLE role_profiles ADD COLUMN look_ts INTEGER NOT NULL DEFAULT 0;
