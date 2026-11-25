-- 改名 / 换简称以后，旧名字留在这里：别人不能拿去当名字或简称（不然发给旧名字的短信、旧消息下面的「回短信」按钮会送到抢了名字的人那里），
-- 写旧名字的短信、约会照样找到原来的人。角色删了跟着删（开新季清角色时一起清掉）。
CREATE TABLE IF NOT EXISTS role_former_names (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    PRIMARY KEY (role_id, name)
);
CREATE INDEX IF NOT EXISTS idx_role_former_names_name ON role_former_names(name);
