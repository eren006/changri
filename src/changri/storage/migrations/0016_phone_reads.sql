-- 0016：小手机「读到哪了」（照老系统 phone_read）：每个会话一行，打开会话就把 read_ts 记成现在。
-- key：r:角色id（真名会话）/ s:署名 / a:匿名对话id / song（点歌台）/ steal（偷盗记录）/ lovemail（心动信）。
-- 关系线用它自己的 rel_seen。管理员以玩家视角看不算已读。
CREATE TABLE IF NOT EXISTS phone_reads (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    read_ts INTEGER NOT NULL,
    PRIMARY KEY (role_id, key)
);
