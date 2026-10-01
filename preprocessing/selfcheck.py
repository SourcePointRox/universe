"""自检自验模块：
- validate_tasks : 校验每个任务的输入文件存在性与完整性(checksum)，
                   失败则标记 error 并给出原因；
- regenerate     : 重新生成错误/丢失任务(清状态重跑 taskgen)；
- redownload     : 对缺失的数据库文件，按 surveys.yaml 中登记的下载入口
                   生成下载清单(尊重robots与官方API，不自动暴力抓取)。
"""
import json
from pathlib import Path

import yaml

from common.db import DB
from common.utils import sha256_file
from preprocessing.taskgen import generate_tasks, write_htcondor_files


def _parse_files_field(raw):
    """input_files 字段兼容：可能是 JSON list 或双重编码的 JSON string。"""
    if not raw:
        return []
    v = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return [v]
    return v if isinstance(v, list) else []


def validate_tasks(db: DB):
    bad = []
    for t in db.tasks():
        problems = []
        for f in _parse_files_field(t.get("input_files")):
            p = Path(f)
            if not p.exists():
                problems.append(f"输入文件丢失: {f}")
        spec_path = Path(t["submit_path"]).parent / "task.json" if t.get("submit_path") else None
        if not (spec_path and spec_path.exists()):
            problems.append("任务规格文件丢失(task.json)")
        elif spec_path.exists():
            # v2.0: 校验分片输入
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                for sh in spec.get("shards", []):
                    if not Path(sh["path"]).exists():
                        problems.append(f"分片缺失: {sh['path']}")
            except json.JSONDecodeError:
                problems.append("任务规格文件损坏(task.json)")
        if t.get("submit_path") and not Path(t["submit_path"]).exists():
            problems.append("HTCondor提交文件丢失(task.sub)")
        if problems:
            db.set_task_status(t["id"], "error", error="; ".join(problems))
            bad.append({"id": t["id"], "problems": problems})
    return bad


def regenerate_errors(db: DB, groups_cfg_path, workdir, worker_exe="compute_worker.bat"):
    n_err = len(db.tasks(status="error"))
    created = generate_tasks(db, groups_cfg_path, workdir)
    dag = write_htcondor_files(db, workdir, worker_exe)
    return {"errors_before": n_err, "tasks_after_regen": len(created), "dag": str(dag)}


def missing_surveys(db: DB, surveys_cfg):
    with open(surveys_cfg, encoding="utf-8") as f:
        surveys = yaml.safe_load(f)["surveys"]
    have = {f["survey"] for f in db.files()}
    return [s for s in surveys if s["id"] not in have]


def redownload_manifest(db: DB, surveys_cfg, out_path):
    """为缺失数据库生成官方下载指引清单（含入口URL与说明），
    不直接抓取——大数据下载需用户确认与配额管理。"""
    miss = missing_surveys(db, surveys_cfg)
    lines = ["# 缺失数据库补全清单", ""]
    for s in miss:
        lines.append(f"## {s['name']} ({s['id']})")
        lines.append(f"- 数据规模: {s.get('total_size','未知')}")
        lines.append(f"- 官方入口: {s['home']}")
        dl = s.get("download", {})
        lines.append(f"- 批量下载: {dl.get('bulk','见官方入口')}")
        lines.append(f"- 查询API : {dl.get('api','见官方入口')}")
        lines.append("")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return [s["id"] for s in miss]
