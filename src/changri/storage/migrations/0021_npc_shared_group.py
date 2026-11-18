"""NPC 可以几个人绑同一个群（玩家还是一人一个群，规则在 core/roles.py 里管）。
role_groups 原来有 UNIQUE(bot_id, group_openid)，一个群只能绑一个角色；SQLite 去不掉约束，只能重建这张表。"""


def up(conn):
    conn.execute("""CREATE TABLE role_groups_v21 (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        bot_id        TEXT NOT NULL DEFAULT '',
        group_openid  TEXT,
        real_gid      TEXT NOT NULL DEFAULT '',
        member_openid TEXT NOT NULL DEFAULT '',
        kind          TEXT NOT NULL CHECK (kind IN ('main', 'backup')),
        created_ts    INTEGER NOT NULL
    )""")
    conn.execute("INSERT INTO role_groups_v21(id, role_id, bot_id, group_openid, real_gid, member_openid, kind, created_ts) "
                 "SELECT id, role_id, bot_id, group_openid, real_gid, member_openid, kind, created_ts FROM role_groups")
    conn.execute("DROP TABLE role_groups")
    conn.execute("ALTER TABLE role_groups_v21 RENAME TO role_groups")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_main_per_role ON role_groups(role_id) WHERE kind = 'main'")
    conn.execute("CREATE INDEX IF NOT EXISTS role_groups_by_group ON role_groups(bot_id, group_openid)")
