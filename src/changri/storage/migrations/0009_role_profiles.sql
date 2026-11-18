-- 0009：角色档案（性别 / 年龄 / 皮相 / 签名 / 简称），玩家用「修改性别」等指令自己改。
-- 没有这一行 = 全用默认值（女、18、按性别的默认皮相、没签名、没简称）。
-- short 是简称：跟所有人的本名、简称都不能重复，填名字的地方都能拿它代替本名。
CREATE TABLE IF NOT EXISTS role_profiles (
    role_id   INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    gender    TEXT NOT NULL DEFAULT '女',
    age       INTEGER NOT NULL DEFAULT 18,
    look      TEXT NOT NULL DEFAULT '',
    bio       TEXT NOT NULL DEFAULT '',
    bio_ts    INTEGER NOT NULL DEFAULT 0,
    short     TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS role_profiles_short ON role_profiles(short) WHERE short != '';
