"""任务断点续算与心跳。

Checkpoint：任务按"阶段(stage)"推进，每完成一个阶段将阶段名与输入指纹
写入 checkpoint.json；重启后已完成阶段自动跳过，未完成阶段重跑。
输入指纹 = 输入文件 (路径, 大小, mtime) 的 sha256，输入变化则全部重跑。

Heartbeat：worker 每 interval 秒刷新一次心跳文件/数据库时间戳，
调度器据此检测僵尸任务并重新调度。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from common.logutil import get_logger

log = get_logger("checkpoint")


def input_fingerprint(files) -> str:
    h = hashlib.sha256()
    for fp in sorted(str(f) for f in files):
        p = Path(fp)
        try:
            st = p.stat()
            h.update(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode())
        except OSError:
            h.update(f"{p}|MISSING".encode())
    return h.hexdigest()


class Checkpoint:
    def __init__(self, task_dir, fingerprint: str):
        self.path = Path(task_dir) / "checkpoint.json"
        self.fingerprint = fingerprint
        self._load()

    def _load(self):
        self.state = {"fingerprint": self.fingerprint, "done": [], "data": {}}
        if self.path.exists():
            try:
                old = json.loads(self.path.read_text(encoding="utf-8"))
                if old.get("fingerprint") == self.fingerprint:
                    self.state = old
                    log.info("恢复 checkpoint: 已完成阶段 %s", old.get("done"))
                else:
                    log.warning("输入指纹变化，checkpoint 作废，全部重算")
            except (json.JSONDecodeError, OSError):
                log.warning("checkpoint 文件损坏，忽略")

    def is_done(self, stage: str) -> bool:
        return stage in self.state["done"]

    def mark_done(self, stage: str, data: dict | None = None):
        if stage not in self.state["done"]:
            self.state["done"].append(stage)
        if data:
            self.state["data"].update(data)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)  # 原子写，防半写状态

    def get(self, key, default=None):
        return self.state["data"].get(key, default)


class Heartbeat:
    """后台线程周期性刷新心跳。with 语句或显式 stop() 结束。"""

    def __init__(self, beat_path, interval: float = 5.0, callback=None):
        self.path = Path(beat_path)
        self.interval = interval
        self.callback = callback  # 可选：同步心跳到 DB
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.path.write_text(json.dumps(
                    {"ts": time.time(), "pid": __import__("os").getpid()}),
                    encoding="utf-8")
                if self.callback:
                    self.callback(time.time())
            except OSError:
                pass
            self._stop.wait(self.interval)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
