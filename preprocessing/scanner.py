"""本地数据库扫描器：递归扫描用户选定目录，解析文件元数据，
依据 surveys.yaml 的文件名模式与头部关键字识别所属巡天数据库。
"""
import fnmatch
import time
from pathlib import Path

import yaml

from common.db import DB
from common.utils import sha256_file
from preprocessing.parsers import PARSERS, parse_file

SUPPORTED = set(PARSERS.keys()) | {".gz", ".fits.gz"}


def load_surveys(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["surveys"]


def identify_survey(path, meta, surveys):
    name = Path(path).name.lower()
    scores = {}
    for s in surveys:
        score = 0
        for pat in s.get("file_patterns", []):
            if fnmatch.fnmatch(name, pat.lower()):
                score += 2
        hints = set(h.lower() for h in meta.get("survey_hints", []))
        kws = {k.lower() for k in s.get("header_keywords", [])}
        score += len(hints & kws) * 3
        if s["id"] in hints:
            score += 3
        if score:
            scores[s["id"]] = score
    if not scores:
        return "unknown", 0
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0], best[1]


def scan_directory(root, db: DB, surveys_cfg, checksum_limit_mb=256, progress_cb=None):
    """递归扫描 root，入库并返回统计。大文件跳过checksum以保证速度。"""
    root = Path(root)
    stats = {"scanned": 0, "identified": 0, "unknown": 0, "errors": 0, "skipped": 0}
    surveys = load_surveys(surveys_cfg)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext == ".gz" and p.name.lower().endswith(".fits.gz"):
            pass  # 支持压缩FITS
        elif ext not in SUPPORTED:
            stats["skipped"] += 1
            continue
        stats["scanned"] += 1
        meta = parse_file(p) or {}
        if meta.get("error"):
            stats["errors"] += 1
        survey, _score = identify_survey(p, meta, surveys)
        if survey == "unknown":
            stats["unknown"] += 1
        else:
            stats["identified"] += 1
        size = p.stat().st_size
        checksum = None
        if size <= checksum_limit_mb * 1024 * 1024:
            try:
                checksum = sha256_file(p)
            except OSError:
                pass
        db.upsert_file(str(p), survey, meta.get("format", ext.lstrip(".")),
                       size, p.stat().st_mtime, checksum, meta)
        if progress_cb and stats["scanned"] % 50 == 0:
            progress_cb(stats)
    return stats
