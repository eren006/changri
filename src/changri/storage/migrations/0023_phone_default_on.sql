-- 0.22.4 起小手机默认打开（作者定的：新装的和升级的老机器都打开）。
-- 只在升级到这一版时执行一次：之后管理员在后台关掉，就一直是关的。
-- 两个开关（「扩展」页的卡片 ext:phone、「小手机」页的 phone_enabled）一起打开；以后后台改其中一个，另一个跟着变。
INSERT INTO config(key, value) VALUES('ext:phone', '1') ON CONFLICT(key) DO UPDATE SET value='1';
INSERT INTO config(key, value) VALUES('phone_enabled', '1') ON CONFLICT(key) DO UPDATE SET value='1';
