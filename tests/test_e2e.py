"""全链路端到端测试（使用合成数据）。

步骤：
1. 合成 FITS/CSV 数据库 -> 模拟 gaia+sdss+desi 数据
2. 前处理：扫描 -> 生成任务 -> 写 HTCondor submit -> 自检
3. 计算执行：逐个本地调用 worker 运行任务
4. 后处理：结果回收 -> 科学归并 -> 可视化
5. 断言：结果存在且非空
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.db import DB
from common.tiling import all_tiles, load_tiling
from common.utils import ensure_dir
from compute.worker import run_task
from postprocessing.collector import collect_results
from postprocessing.reducer import (global_density_and_power, merge_clustering,
                                    merge_halo_catalog)
from postprocessing.visualize import make_all_plots
from preprocessing.scanner import scan_directory
from preprocessing.selfcheck import validate_tasks
from preprocessing.taskgen import generate_tasks, write_htcondor_files

WORK = ROOT / "test_work"
DB_PATH = WORK / "test.sqlite"
DATA = WORK / "test_data"
RESULTS = WORK / "test_results"


def setup_synthetic_data():
    ensure_dir(DATA)
    rng = np.random.default_rng(7)
    n_gaia = 5000
    t_gaia = Table({
        "ra": rng.uniform(0, 360, n_gaia),
        "dec": np.degrees(np.arcsin(rng.uniform(-1, 1, n_gaia))),
        "parallax": np.abs(rng.normal(2.0, 0.5, n_gaia)),
        "source_id": np.arange(n_gaia),
    })
    t_gaia.write(DATA / "gaia_dr3_sample.fits", overwrite=True)

    n_desi = 8000
    t_desi = Table({
        "ra": rng.uniform(0, 360, n_desi),
        "dec": np.degrees(np.arcsin(rng.uniform(-1, 1, n_desi))),
        "z": rng.beta(2, 3, n_desi) * 2.5 + 0.01,
        "targetid": np.arange(n_desi),
    })
    t_desi.write(DATA / "desi_dr1_sample.fits", overwrite=True)

    n_sdss = 6000
    t_sdss = Table({
        "ra": rng.uniform(0, 360, n_sdss),
        "dec": np.degrees(np.arcsin(rng.uniform(-1, 1, n_sdss))),
        "z": rng.beta(2, 3, n_sdss) * 2.5 + 0.01,
        "objid": np.arange(n_sdss),
    })
    t_sdss.write(DATA / "sdss_spec_sample.csv", overwrite=True)

    n_euclid = 4000
    t_euc = Table({
        "ra": rng.uniform(0, 360, n_euclid),
        "dec": np.degrees(np.arcsin(rng.uniform(-1, 1, n_euclid))),
        "e1": rng.normal(0, 0.05, n_euclid),
        "e2": rng.normal(0, 0.05, n_euclid),
    })
    t_euc.write(DATA / "euclid_shape_sample.fits", overwrite=True)


def run_pipeline():
    # 1) 扫描
    db = DB(DB_PATH)
    stats = scan_directory(DATA, db, ROOT / "configs" / "surveys.yaml")
    print("[e2e] 扫描:", stats)
    assert stats["scanned"] >= 4
    assert stats["identified"] >= 4

    # 2) 任务生成
    created = generate_tasks(db, ROOT / "configs" / "compute_groups.yaml", WORK)
    print("[e2e] 生成任务:", len(created))
    assert len(created) > 0

    dag = write_htcondor_files(db, WORK, worker_exe="compute_worker.bat")
    assert dag.exists()

    # 3) 自检
    bad = validate_tasks(db)
    print("[e2e] 自检问题:", len(bad))

    # 4) 本地逐个执行 worker（模拟 HTCondor 计算节点）
    pending = db.tasks(status="pending")
    print("[e2e] 计算中...")
    for t in pending[:30]:  # 测试只跑少量任务（全量过大）
        manifest = run_task(t["id"], ROOT / "configs" / "compute_groups.yaml", WORK, RESULTS)
        assert manifest.get("result")
    print(f"[e2e] 完成 {len(pending[:30])} 个本地worker任务")

    # 5) 后处理回收
    rec = collect_results(db, RESULTS)
    print("[e2e] 回收:", rec)
    assert rec["received"] > 0
    assert rec["verified"] > 0

    # 6) 科学归并
    merged = global_density_and_power(db, RESULTS / "merged" / "global_density.h5", ngrid=32)
    print("[e2e] 全局密度场:", merged)
    assert merged is not None

    cl = merge_clustering(db, RESULTS / "merged" / "global_xi.h5")
    halos = merge_halo_catalog(db, RESULTS / "merged" / "halos.csv")
    print("[e2e] 聚类/晕:", cl, halos)

    # 7) 可视化
    plots = make_all_plots(RESULTS / "merged" / "global_density.h5", RESULTS / "plots")
    print("[e2e] 可视化:", plots)
    assert Path(plots["sky"]).exists()
    assert Path(plots["slices"]).exists()
    assert Path(plots["pk"]).exists()

    print("\n=== 全链路端到端测试通过 ===")
    return True


def main():
    print("=== 创建合成测试数据 ===")
    setup_synthetic_data()
    print("=== 启动全链路测试 ===")
    ok = run_pipeline()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
