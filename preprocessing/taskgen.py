"""计算任务生成器：
1) 按计算组/子计算组/天区分片 生成任务图(DAG)
2) 为每个任务匹配输入文件
3) 生成 HTCondor submit 文件与 DAGMan 工作流文件

任务ID: {subgroup}__{tile_id}
上游依赖任务按 requires.upstream 建立 DAG 边。
"""
import json
import time
from pathlib import Path

from common.db import DB
from common.tiling import all_tiles, load_tiling
from common.utils import ensure_dir, write_json
from preprocessing.matcher import load_groups, match_files_for_subgroup


def task_id(subgroup, tile_id):
    return f"{subgroup}__{tile_id}"


def generate_tasks(db: DB, groups_cfg_path, workdir, ra_bins=None, dec_bins=None):
    cfg = load_groups(groups_cfg_path)
    if ra_bins is None or dec_bins is None:
        ra_bins, dec_bins = load_tiling(groups_cfg_path)
    tiles = all_tiles(ra_bins, dec_bins)
    files = db.files()
    res = cfg.get("resources", {})
    workdir = ensure_dir(workdir)
    task_root = ensure_dir(workdir / "tasks")

    created = []
    for group in cfg["compute_groups"]:
        for sg in group["subgroups"]:
            if sg.get("requires", {}).get("surveys"):
                matched = match_files_for_subgroup(sg, files)
                if not matched:
                    continue  # 无数据 -> 不生成任务，等扫描到数据后再生成
            else:
                matched = {}
            for tile in tiles:
                tid = task_id(sg["id"], tile)
                input_files = [f["path"] for rows in matched.values() for f in rows]
                spec = {
                    "id": tid, "cgroup": group["id"], "subgroup": sg["id"],
                    "tile_id": tile, "algorithm": sg["algorithm"],
                    "input_files": input_files,
                    "resources": res,
                    "upstream": [task_id(u, tile) for u in sg.get("requires", {}).get("upstream", [])],
                }
                tdir = ensure_dir(task_root / group["id"] / sg["id"] / tile)
                write_json(tdir / "task.json", spec)
                db.upsert_task({**spec, "status": "pending",
                                "submit_path": str(tdir / "task.sub")})
                created.append(tid)
    return created


def _submit_text(spec, worker_exe, outdir, logdir):
    r = spec.get("resources", {})
    return f"""universe = vanilla
executable = {worker_exe}
arguments = "{spec['id']}"
initialdir = {outdir}
output = {logdir}/{spec['id']}.out
error  = {logdir}/{spec['id']}.err
log    = {logdir}/{spec['id']}.log
request_cpus = {r.get('request_cpus', 2)}
request_memory = {r.get('request_memory', '4GB')}
request_disk = {r.get('request_disk', '10GB')}
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
on_exit_remove = (ExitCode == 0)
+ProjectGroup = "{spec['cgroup']}"
+ProjectSubgroup = "{spec['subgroup']}"
queue
"""


def write_htcondor_files(db: DB, workdir, worker_exe="compute_worker.bat"):
    """为所有 pending 任务生成 .sub，并生成全局 DAGMan 文件。"""
    workdir = Path(workdir)
    logdir = ensure_dir(workdir / "logs")
    submit_root = workdir / "tasks"
    tasks = db.tasks()
    dag_lines = []
    for t in tasks:
        spec_path = submit_root / t["cgroup"] / t["subgroup"] / t["tile_id"] / "task.json"
        if not spec_path.exists():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        sub = _submit_text(spec, worker_exe, spec_path.parent, logdir)
        sub_path = spec_path.parent / "task.sub"
        sub_path.write_text(sub, encoding="utf-8")
        dag_lines.append(f"JOB {t['id']} {sub_path}")
        db.upsert_task({**t, "submit_path": str(sub_path)})
    # DAG 依赖
    for t in tasks:
        spec_path = submit_root / t["cgroup"] / t["subgroup"] / t["tile_id"] / "task.json"
        if not spec_path.exists():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for up in spec.get("upstream", []):
            dag_lines.append(f"PARENT {up} CHILD {t['id']}")
    dag_path = workdir / "workflow.dag"
    dag_path.write_text("\n".join(dag_lines) + "\n", encoding="utf-8")
    return dag_path
