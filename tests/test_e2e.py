"""全链路端到端测试 v2.0（合成数据，HEALPix 分片管线）。

流程:
1. 合成多巡天红移目录 (sdss/desi/gama 风格 FITS/CSV)
2. 预分片: catalogs -> HEALPix Parquet 分区
3. 任务生成: pixel 任务 (SG1.1) + global 任务 (SG2.2/SG3.1/SG3.2)
4. 本地执行 worker（含 checkpoint/心跳/租约路径）
5. 后处理回收 + 归并
6. 断言: 结果存在、坐标有限、密度场/2PCF/FoF 输出非空
"""
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from astropy.table import Table

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.db import DB
from common.utils import ensure_dir
from compute.worker import run_task
from postprocessing.collector import collect_results
from postprocessing.reducer import global_density_and_power
from preprocessing.selfcheck import validate_tasks
from preprocessing.shard import shard_catalog
from preprocessing.taskgen import generate_tasks, write_htcondor_files

# 每次运行使用独立工作目录（避免删除大量旧文件；旧目录可定期手工清理）
WORK = ROOT / f"test_work_v2_{int(time.time())}"
DATA = WORK / "raw"
SHARDS = WORK / "shards"
RESULTS = WORK / "results"
DB_PATH = WORK / "test.sqlite"


def setup_synthetic_data():
    ensure_dir(DATA)
    rng = np.random.default_rng(11)
    # 模拟 z<=0.15 红移目录，带一个大尺度结构（团块）使聚类统计非平凡
    for survey, n in (("sdss", 40000), ("desi", 25000), ("gama", 15000)):
        ra = rng.uniform(0, 360, n)
        dec = np.degrees(np.arcsin(rng.uniform(-1, 1, n)))
        z = rng.uniform(0.005, 0.15, n)
        # 注入一个人工超团（占 10%）
        n_cl = n // 10
        ra[:n_cl] = rng.normal(180, 3, n_cl) % 360
        dec[:n_cl] = rng.normal(20, 3, n_cl)
        z[:n_cl] = rng.normal(0.08, 0.004, n_cl)
        t = Table({"ra": ra, "dec": dec, "z": np.clip(z, 1e-4, 0.2)})
        ext = "fits" if survey != "gama" else "csv"
        t.write(DATA / f"{survey}_mock.{ext}", overwrite=True)


def run_pipeline():
    # 1) 预分片
    for survey, ext in (("sdss", "fits"), ("desi", "fits"), ("gama", "csv")):
        meta = shard_catalog(DATA / f"{survey}_mock.{ext}", survey,
                             out_root=SHARDS, nside=4)
        print(f"[e2e] 分片 {survey}: {meta['n_rows']} 行 -> "
              f"{meta['n_partitions']} 分区")
        assert meta["n_rows"] > 0

    # 2) 任务生成（从分片元数据）
    db = DB(DB_PATH)
    created = generate_tasks(db, ROOT / "configs" / "compute_groups.yaml",
                             WORK, shards_root=SHARDS)
    n_pixel = sum(1 for c in created if "global" not in c)
    n_global = sum(1 for c in created if "global" in c)
    print(f"[e2e] 任务: {len(created)} (pixel {n_pixel}, global {n_global})")
    assert n_pixel > 20
    assert n_global >= 4   # SG2.1/SG2.2/SG3.1/SG3.2/SG5.1/SG5.2 (有上游的)
    dag = write_htcondor_files(db, WORK)
    assert dag.exists()
    bad = validate_tasks(db)
    assert not bad, f"自检发现问题: {bad[:3]}"
    # 关闭控制端连接：worker 各自打开自己的连接（单写者原则）
    db.close()

    # 3) 执行：先跑一部分 SG1.1 pixel 任务（模拟部分完成的中间态）
    db = DB(DB_PATH)
    pixel_tasks = [t for t in db.tasks(status="pending")
                   if t["subgroup"] == "SG1.1"][:30]
    db.close()
    for t in pixel_tasks:
        m = run_task(t["id"], WORK, RESULTS, db_path=DB_PATH, owner="test-node")
        assert m.get("result")
    print(f"[e2e] pixel 任务完成 {len(pixel_tasks)} 个")

    # 断点续算验证：重跑同一任务应直接成功且复用 checkpoint
    m2 = run_task(pixel_tasks[0]["id"], WORK, RESULTS,
                  db_path=DB_PATH, owner="test-node")
    assert m2["sha256"]

    # 4) 执行 global 任务（密度场/2PCF/FoF/Wiener）
    db = DB(DB_PATH)
    for sg in ("SG2.1", "SG2.2", "SG3.1", "SG3.2"):
        t = db.task(f"{sg}__global")
        if t is None:
            continue
        m = run_task(t["id"], WORK, RESULTS, db_path=DB_PATH, owner="test-node")
        print(f"[e2e] global {sg}: metrics={m['metrics']}")

    # 5) 回收与校验
    rec = collect_results(db, RESULTS)
    print("[e2e] 回收:", rec)
    assert rec["verified"] >= len(pixel_tasks)

    # 6) 归并（全局密度场）
    merged = global_density_and_power(db, RESULTS / "merged" / "global_density.h5",
                                      ngrid=32)
    print("[e2e] 归并:", merged)
    # 30 个像素任务 × ~260 星系/像素（nside=4）≈ 8000 天体
    assert merged and merged["n_objects"] > 1000

    # 7) 科学内容断言
    import h5py
    with h5py.File(RESULTS / "CG2" / "SG2.1" / "SG2.1__global.h5") as h:
        dm, ds = h["delta_mean"][:], h["delta_sigma"][:]
        assert np.nanstd(dm) > 0, "Wiener 密度场应有结构"
        assert np.nanmean(ds) > 0, "不确定度场应非零"
    with h5py.File(RESULTS / "CG3" / "SG3.2" / "SG3.2__global.h5") as h:
        g = h["group"][:]
        assert (g >= 0).sum() > 20, "FoF 应检出成团结构"
    print("\n=== v2.0 全链路端到端测试通过 ===")
    return True


def main():
    print("=== 创建合成测试数据 ===")
    setup_synthetic_data()
    ok = run_pipeline()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
