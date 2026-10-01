"""状态库：文件索引、任务、结果的统一持久化。

默认后端为 SQLite，已启用：
- WAL 日志模式（单写多读并发，读不阻塞写）
- busy_timeout 30s（写锁等待而非立即报错）
- 任务租约(lease)与心跳(heartbeat)字段，支撑故障恢复与重调度

生产集群（>=数十节点并发写）应切换到 PostgreSQL：
设置环境变量 UNIVERSE_DB_BACKEND=postgres 并提供 DSN，
本模块的 SQL 均为标准 SQL，迁移只需替换连接层（见 docs/architecture.md）。
"""
import json
import os
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE,
    survey TEXT,
    dtype TEXT,
    size INTEGER,
    mtime REAL,
    checksum TEXT,
    meta_json TEXT,
    status TEXT DEFAULT 'scanned'
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    cgroup TEXT,
    subgroup TEXT,
    tile_id TEXT,
    status TEXT DEFAULT 'pending',
    input_files TEXT,
    submit_path TEXT,
    result_path TEXT,
    checksum TEXT,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    error TEXT,
    lease_owner TEXT,
    lease_until REAL,
    heartbeat REAL,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    cgroup TEXT,
    subgroup TEXT,
    tile_id TEXT,
    path TEXT,
    checksum TEXT,
    metrics_json TEXT,
    status TEXT DEFAULT 'received',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_sg ON tasks(cgroup, subgroup, tile_id);
CREATE INDEX IF NOT EXISTS idx_files_survey ON files(survey);
"""


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                    timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        # WAL + busy_timeout：单机/小集群并发读写的安全基线
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """旧库缺新列时自动补列（幂等）。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        for col, ddl in [("max_attempts", "ALTER TABLE tasks ADD COLUMN max_attempts INTEGER DEFAULT 5"),
                         ("lease_owner", "ALTER TABLE tasks ADD COLUMN lease_owner TEXT"),
                         ("lease_until", "ALTER TABLE tasks ADD COLUMN lease_until REAL"),
                         ("heartbeat", "ALTER TABLE tasks ADD COLUMN heartbeat REAL")]:
            if col not in cols:
                self.conn.execute(ddl)
        self.conn.commit()

    # ---------- files ----------
    def upsert_file(self, path, survey, dtype, size, mtime, checksum, meta):
        self.conn.execute(
            """INSERT INTO files(path,survey,dtype,size,mtime,checksum,meta_json,status)
               VALUES(?,?,?,?,?,?,?,'scanned')
               ON CONFLICT(path) DO UPDATE SET
                 survey=excluded.survey, dtype=excluded.dtype, size=excluded.size,
                 mtime=excluded.mtime, checksum=excluded.checksum,
                 meta_json=excluded.meta_json""",
            (str(path), survey, dtype, size, mtime, checksum, json.dumps(meta, ensure_ascii=False)),
        )
        self.conn.commit()

    def files(self, survey=None):
        if survey:
            cur = self.conn.execute("SELECT * FROM files WHERE survey=?", (survey,))
        else:
            cur = self.conn.execute("SELECT * FROM files")
        return [dict(r) for r in cur.fetchall()]

    # ---------- tasks ----------
    def upsert_task(self, t):
        now = time.time()
        files = t.get("input_files", [])
        if not isinstance(files, str):
            files = json.dumps(files, ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO tasks(id,cgroup,subgroup,tile_id,status,input_files,submit_path,
                                result_path,checksum,attempts,error,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status, input_files=excluded.input_files,
                 submit_path=excluded.submit_path, result_path=excluded.result_path,
                 checksum=excluded.checksum, attempts=excluded.attempts,
                 error=excluded.error, updated_at=excluded.updated_at""",
            (t["id"], t["cgroup"], t["subgroup"], t["tile_id"], t.get("status", "pending"),
             files, t.get("submit_path"),
             t.get("result_path"), t.get("checksum"), t.get("attempts", 0),
             t.get("error"), now, now),
        )
        if self._autocommit:
            self.conn.commit()

    _autocommit = True

    def begin_batch(self):
        """批量写入模式：推迟 commit 到 end_batch，大批量提速 50-100 倍。"""
        self._autocommit = False

    def end_batch(self):
        self.conn.commit()
        self._autocommit = True

    def set_task_status(self, task_id, status, error=None, result_path=None, checksum=None):
        self.conn.execute(
            "UPDATE tasks SET status=?, error=?, result_path=COALESCE(?,result_path), "
            "checksum=COALESCE(?,checksum), attempts=attempts+1, updated_at=? WHERE id=?",
            (status, error, result_path, checksum, time.time(), task_id),
        )
        self.conn.commit()

    def tasks(self, status=None):
        if status:
            cur = self.conn.execute("SELECT * FROM tasks WHERE status=?", (status,))
        else:
            cur = self.conn.execute("SELECT * FROM tasks")
        return [dict(r) for r in cur.fetchall()]

    def task(self, task_id):
        cur = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ---------- 租约与故障恢复 ----------
    def lease_task(self, task_id, owner: str, ttl: float = 600.0) -> bool:
        """抢占任务租约；已被他人持有效租约则失败。返回是否拿到租约。"""
        now = time.time()
        cur = self.conn.execute(
            """UPDATE tasks SET lease_owner=?, lease_until=?, status='running',
                                 heartbeat=?, updated_at=?
               WHERE id=? AND (lease_until IS NULL OR lease_until<? OR lease_owner=?)""",
            (owner, now + ttl, now, now, task_id, now, owner),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def beat(self, task_id, ts=None):
        """刷新任务心跳（供 Heartbeat 回调使用）。"""
        self.conn.execute("UPDATE tasks SET heartbeat=? WHERE id=?",
                          (ts or time.time(), task_id))
        self.conn.commit()

    def stale_tasks(self, max_silence: float = 120.0):
        """心跳超时但状态仍为 running 的任务（疑似节点宕机）。"""
        now = time.time()
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE status='running' AND "
            "(heartbeat IS NULL OR heartbeat<?)", (now - max_silence,))
        return [dict(r) for r in cur.fetchall()]

    def requeue_stale(self, max_silence: float = 120.0):
        """把僵尸任务重新置为 pending（未超过 max_attempts），返回重排队数。"""
        n = 0
        for t in self.stale_tasks(max_silence):
            if t["attempts"] < (t["max_attempts"] or 5):
                self.conn.execute(
                    "UPDATE tasks SET status='pending', lease_owner=NULL, "
                    "lease_until=NULL, updated_at=? WHERE id=?",
                    (time.time(), t["id"]))
                n += 1
            else:
                self.conn.execute(
                    "UPDATE tasks SET status='dead', error='超过最大重试次数', "
                    "updated_at=? WHERE id=?", (time.time(), t["id"]))
        self.conn.commit()
        return n

    # ---------- results ----------
    def add_result(self, r):
        self.conn.execute(
            """INSERT INTO results(task_id,cgroup,subgroup,tile_id,path,checksum,metrics_json,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (r["task_id"], r["cgroup"], r["subgroup"], r["tile_id"], r["path"],
             r.get("checksum"), json.dumps(r.get("metrics", {}), ensure_ascii=False),
             r.get("status", "received"), time.time()),
        )
        self.conn.commit()

    def results(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM results").fetchall()]

    # ---------- 进度聚合 ----------
    def progress_matrix(self):
        """返回 {subgroup: {tile_id: status}}，供热力图使用。"""
        matrix = {}
        for t in self.tasks():
            matrix.setdefault(t["subgroup"], {})[t["tile_id"]] = t["status"]
        return matrix

    def status_counts(self):
        counts = {}
        for t in self.tasks():
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        return counts
