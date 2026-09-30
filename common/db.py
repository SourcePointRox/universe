"""SQLite 状态库：文件索引、任务、结果的统一持久化。"""
import json
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
    error TEXT,
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
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
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
        self.conn.commit()

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
