"""结构化日志：统一控制台 + 文件双通道，供所有模块使用。

用法:
    from common.logutil import get_logger
    log = get_logger("worker")
    log.info("task %s stage %s done", tid, stage)

日志目录由环境变量 UNIVERSE_LOG_DIR 指定（默认 logs/）。
格式包含时间戳/级别/模块/消息，生产环境可直接被 Loki/ELK 采集。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_CONFIGURED = False


def setup(level: str | None = None):
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.environ.get("UNIVERSE_LOG_LEVEL", "INFO")).upper()
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(lvl)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logdir = Path(os.environ.get("UNIVERSE_LOG_DIR", "logs"))
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logdir / "universe.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass  # 无写权限时仅控制台输出
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
