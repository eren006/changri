"""登录失败计数，存在数据库里，进程重启不清零（以前放内存，重启一下就能接着无限猜）。
接口和 dict[str, (次数, 解锁时间戳)] 一样：get / pop / [] / clear，调用方不用改。后台和小手机各用一个 scope。"""
from __future__ import annotations

import time

from .db import get_conn

KEEP_SECONDS = 30 * 86400      # 超过这么久没再错、也没在锁的记录顺手清掉


class FailStore:
    def __init__(self, scope: str):
        self.scope = scope

    def _ensure(self, conn) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS login_fails ("
                     "scope TEXT NOT NULL, k TEXT NOT NULL, n INTEGER NOT NULL, until REAL NOT NULL, updated REAL NOT NULL, "
                     "PRIMARY KEY (scope, k))")

    def get(self, key: str, default=None):
        with get_conn() as conn:
            self._ensure(conn)
            r = conn.execute("SELECT n, until FROM login_fails WHERE scope=? AND k=?", (self.scope, key)).fetchone()
        return (r["n"], r["until"]) if r else default

    def __getitem__(self, key: str):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __setitem__(self, key: str, value) -> None:
        n, until = value
        now = time.time()
        with get_conn() as conn:
            self._ensure(conn)
            conn.execute("INSERT INTO login_fails (scope, k, n, until, updated) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(scope, k) DO UPDATE SET n=excluded.n, until=excluded.until, updated=excluded.updated",
                         (self.scope, key, int(n), float(until), now))
            conn.execute("DELETE FROM login_fails WHERE updated<? AND until<?", (now - KEEP_SECONDS, now))

    def pop(self, key: str, default=None):
        v = self.get(key, default)
        with get_conn() as conn:
            self._ensure(conn)
            conn.execute("DELETE FROM login_fails WHERE scope=? AND k=?", (self.scope, key))
        return v

    def clear(self) -> None:
        with get_conn() as conn:
            self._ensure(conn)
            conn.execute("DELETE FROM login_fails WHERE scope=?", (self.scope,))
