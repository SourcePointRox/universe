"""计算任务生成器（v2.0）：基于 HEALPix 预分片数据。

任务两级结构：
- pixel 任务（scope: pixel）：每个被数据占据的 HEALPix 像素一个任务，
  输入 = 该像素在各巡天下的 Parquet 分区，I/O O(单分区)
- global 任务（scope: global）：需要全体积数据的算法（密度场/聚类/
  Wiener 重建/N-body），tile_id="global"，DAG 上依赖上游全部 pixel 任务

任务ID: {subgroup}__{tile_id}，tile_id = hpXXXXXX 或 global。
"""
from __future__ import annotations

import json
from pathlib import Path

from common.db import DB
from common.healpix import all_tiles, tile_id as hp_tile_id
from common.logutil import get_logger
from common.utils import ensure_dir, write_json
from preprocessing.matcher import load_groups

log = get_logger("taskgen")

GLOBAL_TILE = "global"


def task_id(subgroup, tile_id):
    return f"{subgroup}__{tile_id}"


def _shard_path(shards_root, survey, nside, pix):
    return str(Path(shards_root) / survey / f"Norder={nside}" / f"Npix={pix}.parquet")


def _survey_shard_meta(shards_root, survey):
    fp = Path(shards_root) / survey / "_metadata.json"
    if not fp.exists():
        return None
    return json.loads(fp.read_text(encoding="utf-8"))


def generate_tasks(db: DB, groups_cfg_path, workdir, shards_root="data/shards"):
    """从分片元数据生成任务图。返回创建的任务 id 列表。"""
    cfg = load_groups(groups_cfg_path)
    nside = cfg.get("sky_tiling", {}).get("healpix_nside", 16)
    res = cfg.get("resources", {})
    workdir = ensure_dir(workdir)
    task_root = ensure_dir(workdir / "tasks")

    created = []
    specs_by_sg: dict[str, list[str]] = {}   # subgroup -> [task_ids]
    db.begin_batch()

    def _emit(group, sg, tid, tile, shards, upstream_ids):
        spec = {
            "id": tid, "cgroup": group["id"], "subgroup": sg["id"],
            "tile_id": tile, "algorithm": sg["algorithm"],
            "nside": nside, "shards": shards,
            "params": sg.get("params", {}),
            "resources": res,
            "upstream": upstream_ids,
        }
        tdir = ensure_dir(task_root / group["id"] / sg["id"] / tile)
        write_json(tdir / "task.json", spec)
        db.upsert_task({**spec, "status": "pending",
                        "submit_path": str(tdir / "task.sub")})
        created.append(tid)
        specs_by_sg.setdefault(sg["id"], []).append(tid)

    for group in cfg["compute_groups"]:
        for sg in group["subgroups"]:
            req = sg.get("requires", {})
            scope = sg.get("scope", "pixel")
            surveys = req.get("surveys", [])
            columns = req.get("columns", [])
            upstream_sgs = req.get("upstream", [])

            if scope == "global":
                up_ids = [t for u in upstream_sgs
                          for t in specs_by_sg.get(u, [])]
                tid = task_id(sg["id"], GLOBAL_TILE)
                shards = []
                for sv in surveys:
                    meta = _survey_shard_meta(shards_root, sv)
                    if not meta:
                        continue
                    for px in meta["partition_rows"]:
                        shards.append({"survey": sv, "columns": columns,
                                       "path": _shard_path(shards_root, sv,
                                                           meta["nside"], int(px))})
                _emit(group, sg, tid, GLOBAL_TILE, shards, up_ids)
                continue

            # pixel 任务：所需巡天占据像素的并集
            if surveys:
                metas = {sv: _survey_shard_meta(shards_root, sv) for sv in surveys}
                metas = {sv: m for sv, m in metas.items() if m}
                if not metas:
                    log.info("%s: 无分片数据，跳过任务生成", sg["id"])
                    continue
                pix_union = set()
                for m in metas.values():
                    pix_union |= {int(p) for p in m["partition_rows"]}
                for px in sorted(pix_union):
                    shards = []
                    for sv, m in metas.items():
                        if str(px) in m["partition_rows"]:
                            shards.append({
                                "survey": sv, "columns": columns,
                                "path": _shard_path(shards_root, sv,
                                                    m["nside"], px)})
                    up_ids = [task_id(u, hp_tile_id(px)) for u in upstream_sgs]
                    up_ids = [u for u in up_ids
                              if u in specs_by_sg.get(u.split("__")[0], [])]
                    _emit(group, sg, task_id(sg["id"], hp_tile_id(px)),
                          hp_tile_id(px), shards, up_ids)
    log.info("生成任务 %d 个（%d 个子组）", len(created), len(specs_by_sg))
    db.end_batch()
    return created


def _submit_text(spec, worker_exe, outdir, logdir):
    r = spec.get("resources", {})
    return f"""universe = vanilla
executable = {worker_exe}
arguments = "{spec['id']} --db state.sqlite --owner $(Cluster).$(Process)"
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


def write_htcondor_files(db: DB, workdir, worker_exe="compute_worker.bat",
                         max_retry=3):
    """为所有任务生成 .sub，并生成全局 DAGMan 文件（含 RETRY 与僵尸重排队）。

    DAGMan 层 RETRY 处理瞬时失败；心跳僵死任务由
    postprocessing.collector 定期调用 db.requeue_stale() 重排队。
    """
    workdir = Path(workdir)
    logdir = ensure_dir(workdir / "logs")
    submit_root = workdir / "tasks"
    tasks = db.tasks()
    dag_lines = []
    db.begin_batch()
    for t in tasks:
        spec_path = submit_root / t["cgroup"] / t["subgroup"] / t["tile_id"] / "task.json"
        if not spec_path.exists():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        sub = _submit_text(spec, worker_exe, spec_path.parent, logdir)
        sub_path = spec_path.parent / "task.sub"
        sub_path.write_text(sub, encoding="utf-8")
        dag_lines.append(f"JOB {t['id']} {sub_path}")
        dag_lines.append(f"RETRY {t['id']} {max_retry}")
        db.upsert_task({**t, "submit_path": str(sub_path)})
    for t in tasks:
        spec_path = submit_root / t["cgroup"] / t["subgroup"] / t["tile_id"] / "task.json"
        if not spec_path.exists():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for up in spec.get("upstream", []):
            dag_lines.append(f"PARENT {up} CHILD {t['id']}")
    db.end_batch()
    dag_path = workdir / "workflow.dag"
    dag_path.write_text("\n".join(dag_lines) + "\n", encoding="utf-8")
    return dag_path
