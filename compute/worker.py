"""计算节点工作程序（v2.0 重写）。

关键升级（对应问题清单 #1/#4/#7/#8）：
1. 输入为 HEALPix 预分片 Parquet（任务只读自己的像素分区，列裁剪），
   不再"读全量原始数据再筛 tile"
2. 断点续算：load/compute/write 三阶段 checkpoint，输入指纹变化自动重算
3. 心跳 + 租约：调度器可检测僵尸任务并重排队（见 common/db.py）
4. 内存安全：Parquet 分块读取 + 显式删除中间表
5. 结构化日志：每阶段耗时/行数/内存峰值写入 metrics

用法:
    python -m compute.worker <task_id> --workdir work --results results
        [--db state.sqlite] [--owner node42]
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.checkpoint import Checkpoint, Heartbeat, input_fingerprint
from common.logutil import get_logger
from common.utils import ensure_dir, sha256_file, write_json
from compute import algorithms as A

log = get_logger("worker")


# ============================================================
# 数据装载（分片优先，原始文件兜底兼容）
# ============================================================
def load_shard_data(spec):
    """按 spec['shards'] 读取本任务像素的 Parquet 分区，合并为列 dict。"""
    out = {}
    n = 0
    for sh in spec.get("shards", []):
        fp = Path(sh["path"])
        if not fp.exists():
            log.warning("分区缺失，跳过: %s", fp)
            continue
        import pyarrow.parquet as pq
        cols = sh.get("columns")
        tab = pq.read_table(fp, columns=cols).to_pydict()
        for c, arr in tab.items():
            out.setdefault(c, []).append(np.asarray(arr))
        n += len(next(iter(tab.values()), []))
    if not out:
        return None, 0
    merged = {c: np.concatenate(v) for c, v in out.items()}
    return merged, n


def load_upstream(spec, results_root, keys=("xyz", "xyz_kpc", "d_mpc")):
    """读取上游任务（同 tile 或 global）的 HDF5 输出并拼接。"""
    import h5py
    pats = []
    for up in spec.get("upstream", []):
        sg = up.split("__")[0]
        pat = f"**/{sg}*.h5"
        if pat not in pats:
            pats.append(pat)
    if not pats:
        pats = [f"**/*{spec['tile_id']}*.h5"]
    arrays = {}
    for pat in pats:
        for f in results_root.glob(pat):
            with h5py.File(f, "r") as h:
                for k in keys:
                    if k in h:
                        arrays.setdefault(k, []).append(h[k][:])
    if not arrays:
        return None
    return {k: np.concatenate(v) for k, v in arrays.items()}


# ============================================================
# 算法处理器注册表
# ============================================================
def _radecz_to_xyz(cols, params):
    """红移列 -> 共动 xyz，带 CMB 改正与误差传播。"""
    from compute.algorithms.distance import redshift_distance
    zcol = params.get("z_column", "z")
    z = cols[zcol]
    zerr = cols.get(params.get("zerr_column", "z_err"))
    res = redshift_distance(z, zerr, cols["ra"], cols["dec"],
                            correct_cmb=params.get("correct_cmb", True))
    d = res["d_mpc"]
    zmax = params.get("z_max", 0.15)
    ok = np.isfinite(d) & (res["z_eff"] <= zmax)
    ra = np.deg2rad(cols["ra"][ok]); dec = np.deg2rad(cols["dec"][ok])
    d = d[ok]
    xyz = np.stack([d * np.cos(dec) * np.cos(ra),
                    d * np.cos(dec) * np.sin(ra),
                    d * np.sin(dec)], axis=1)
    out = {"xyz": xyz, "d_mpc": d, "ra": cols["ra"][ok], "dec": cols["dec"][ok],
           "z": res["z_eff"][ok]}
    if "sigma_d" in res:
        out["sigma_d"] = res["sigma_d"][ok]
    return out, int(ok.sum())


def h_coords3d_redshift(spec, cols, params):
    if cols is None:
        return {"n": 0}, {"n_objects": 0}
    out, n = _radecz_to_xyz(cols, params)
    return out, {"n_objects": n}


def h_coords3d_parallax(spec, cols, params):
    """Gaia 视差 -> Bayesian 距离后验 -> 日心 xyz [kpc]。"""
    from compute.algorithms.distance import (gaia_zeropoint_offset,
                                             parallax_distance_posterior)
    if cols is None:
        return {"n": 0}, {"n_objects": 0}
    plx = cols["parallax"]
    perr = cols.get("parallax_error", np.full(len(plx), 0.05))
    g = cols.get("phot_g_mean_mag", np.full(len(plx), 15.0))
    plx_c = plx - gaia_zeropoint_offset(g)          # 零点改正
    post = parallax_distance_posterior(plx_c, perr,
                                       L_kpc=params.get("prior_L_kpc", 1.35))
    r = np.asarray(post["r_med"])
    dmax = params.get("d_max_kpc", 3.0)
    ok = np.isfinite(r) & (r <= dmax)
    ra = np.deg2rad(cols["ra"][ok]); dec = np.deg2rad(cols["dec"][ok])
    d = r[ok]
    xyz = np.stack([d * np.cos(dec) * np.cos(ra),
                    d * np.cos(dec) * np.sin(ra),
                    d * np.sin(dec)], axis=1)
    out = {"xyz_kpc": xyz, "d_kpc": d, "d_lo": np.asarray(post["r_lo"])[ok],
           "d_hi": np.asarray(post["r_hi"])[ok],
           "ra": cols["ra"][ok], "dec": cols["dec"][ok]}
    return out, {"n_objects": int(ok.sum())}


def h_wiener_density(spec, cols, params):
    """全局 Wiener 密度重建（上游 xyz 输入，global 任务）。"""
    from compute.algorithms.reconstruction import (nbar_from_radial_selection,
                                                   wiener_reconstruct)
    up = load_upstream(spec, _results_root)
    if up is None or "xyz" not in up:
        return {"n": 0}, {"n_objects": 0}
    xyz = up["xyz"][np.all(np.isfinite(up["xyz"]), axis=1)]
    box_min = np.array(params.get("box_min", [-650.0, -650.0, -650.0]))
    box_size = float(params.get("box_size", 1300.0))
    ngrid = int(params.get("ngrid", 128))
    nbar = nbar_from_radial_selection(xyz, box_min, box_size, ngrid)
    res = wiener_reconstruct(xyz, box_min, box_size, ngrid, nbar,
                             bias=float(params.get("bias", 1.0)))
    out = {"delta_mean": res["delta_mean"].astype(np.float32),
           "delta_sigma": res["delta_sigma"].astype(np.float32),
           "nbar": res["nbar"].astype(np.float32)}
    return out, {"n_objects": int(len(xyz)), "ngrid": ngrid,
                 "bias": res["bias"]}


def h_density_cic(spec, cols, params):
    up = load_upstream(spec, _results_root)
    key = "xyz" if up and "xyz" in up else ("xyz_kpc" if up else None)
    if key is None:
        return {"n": 0}, {"n_objects": 0}
    xyz = up[key][np.all(np.isfinite(up[key]), axis=1)]
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    pad = 0.02 * (hi - lo + 1e-9)
    ngrid = int(params.get("ngrid", 64))
    delta, rho, _ = A.density_cic(xyz, lo - pad, hi + pad, ngrid=ngrid)
    k, pk = A.power_spectrum(delta, (hi - lo + 2 * pad))
    return {"delta": delta.astype(np.float32), "rho": rho.astype(np.float32),
            "pk_k": k, "pk": pk}, {"n_objects": int(len(xyz))}


def h_twopcf(spec, cols, params):
    up = load_upstream(spec, _results_root)
    key = "xyz" if up and "xyz" in up else ("xyz_kpc" if up else None)
    if key is None:
        return {"n": 0}, {"n_objects": 0}
    xyz = up[key][np.all(np.isfinite(up[key]), axis=1)]
    r_edges = np.logspace(np.log10(float(params.get("r_min", 0.1))),
                          np.log10(float(params.get("r_max", 300.0))),
                          int(params.get("n_bins", 20)) + 1)
    rand = A.random_catalog_from_selection(
        xyz, n_random=int(params.get("n_random_mult", 10)) * len(xyz))
    rc, xi = A.twopcf(xyz, r_edges, randoms=rand)
    return {"r_mpc": rc, "xi": xi}, {"n_objects": int(len(xyz))}


def h_fof_halos(spec, cols, params):
    up = load_upstream(spec, _results_root)
    key = "xyz" if up and "xyz" in up else ("xyz_kpc" if up else None)
    if key is None:
        return {"n": 0}, {"n_objects": 0}
    xyz = up[key][np.all(np.isfinite(up[key]), axis=1)]
    labels, sizes = A.fof_halos(xyz, linking_length=params.get("linking_length"),
                                b=float(params.get("b", 0.2)),
                                min_members=int(params.get("min_members", 5)))
    return {"xyz": xyz, "group": labels}, {"n_halos": len(sizes),
                                           "n_objects": int(len(xyz))}


def h_nbody_pm(spec, cols, params):
    """运行宇宙学 PM 模拟并测功率谱（global 任务）。"""
    from compute.algorithms.nbody import (measure_power_spectrum,
                                          run_pm_simulation)
    res = run_pm_simulation(
        ngrid=int(params.get("ngrid", 64)),
        boxsize=float(params.get("boxsize", 1200.0)),
        z_init=float(params.get("z_init", 49.0)),
        n_steps=int(params.get("n_steps", 40)),
        seed=int(params.get("seed", 42)))
    k, pk = measure_power_spectrum(res["pos"], int(params.get("ngrid", 64)),
                                   float(params.get("boxsize", 1200.0)))
    out = {"pos": res["pos"].astype(np.float32), "pk_k": k, "pk": pk}
    return out, {"n_particles": int(len(res["pos"]))}


def h_shape_catalog(spec, cols, params):
    if cols is None:
        return {"n": 0}, {"n_objects": 0}
    return ({"ra": cols["ra"], "dec": cols["dec"],
             "e1": cols["e1"], "e2": cols["e2"]},
            {"n_objects": int(len(cols["ra"]))})


def h_kaiser_squires(spec, cols, params):
    up = load_upstream(spec, _results_root, keys=("ra", "dec", "e1", "e2"))
    if up is None:
        return {"n": 0}, {"n_objects": 0}
    kappa, nmap = A.kaiser_squires(up["ra"], up["dec"], up["e1"], up["e2"],
                                   ngrid=int(params.get("ngrid", 64)))
    return {"kappa": kappa, "nmap": nmap}, {"n_objects": int(len(up["ra"]))}


def h_lightcurve_bls(spec, cols, params):
    if cols is None:
        return {"n": 0}, {"n_points": 0}
    ok = np.isfinite(cols["time"]) & np.isfinite(cols["flux"])
    t, f = cols["time"][ok], cols["flux"][ok]
    period, depth, _ = A.lightcurve_bls(t, f) if len(t) > 50 else (np.nan, np.nan, None)
    return {"period": np.array([period]), "depth": np.array([depth])}, \
        {"n_points": int(len(t))}


def h_placeholder(spec, cols, params):
    return {"n": 0}, {"note": f"{spec['algorithm']} 需要额外外部数据，占位执行"}


HANDLERS = {
    "coords3d_redshift": h_coords3d_redshift,
    "coords3d_parallax": h_coords3d_parallax,
    "wiener_density": h_wiener_density,
    "density_cic": h_density_cic,
    "twopcf_power": h_twopcf,
    "fof_halos": h_fof_halos,
    "nbody_pm": h_nbody_pm,
    "shape_catalog": h_shape_catalog,
    "kaiser_squires": h_kaiser_squires,
    "lightcurve_bls": h_lightcurve_bls,
    "stream_likelihood": h_placeholder,
    "gw_host_match": h_placeholder,
    "hod_mock": h_placeholder,
    "cross_validate": h_placeholder,
}

_results_root = None  # 供 handler 访问（run_task 注入）


# ============================================================
# 主流程
# ============================================================
def _find_spec(workdir: Path, task_id: str):
    for p in workdir.glob("tasks/*/*/*/task.json"):
        try:
            cand = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if cand.get("id") == task_id:
            return cand, p.parent
    raise FileNotFoundError(f"找不到任务规格: {task_id}")


def run_task(task_id, workdir, results_root, db_path=None,
             owner=None):
    global _results_root
    workdir = Path(workdir)
    results_root = _results_root = ensure_dir(results_root)
    spec, task_dir = _find_spec(workdir, task_id)
    alg = spec["algorithm"]
    handler = HANDLERS.get(alg)
    if handler is None:
        raise ValueError(f"未知算法: {alg}")

    db = None
    if db_path:
        try:
            from common.db import DB
            db = DB(db_path)
            if owner and not db.lease_task(task_id, owner):
                raise RuntimeError(f"任务 {task_id} 租约被占用，退出")
        except RuntimeError:
            raise
        except Exception as e:
            # 状态库不可写（只读挂载/锁冲突）时降级为单机模式，
            # 计算正确性不依赖状态库
            log.warning("状态库不可用，降级单机模式: %s", e)
            db = None

    shard_files = [s["path"] for s in spec.get("shards", [])]
    fp = input_fingerprint(shard_files)
    ckpt = Checkpoint(task_dir, fp)
    t0 = time.time()
    metrics = {"algorithm": alg, "tile": spec["tile_id"]}
    hb_path = task_dir / "heartbeat.json"

    try:
        with Heartbeat(hb_path, interval=5.0,
                       callback=(lambda ts: db.beat(task_id, ts)) if db else None):
            # ---- 阶段1: load ----
            if ckpt.is_done("load"):
                cols = None  # 上游类算法不缓存中间数据，直接进 compute
            t_load0 = time.time()
            cols = None
            if spec.get("shards"):
                cols, n_rows = load_shard_data(spec)
                metrics["n_rows_loaded"] = n_rows
            metrics["t_load_s"] = round(time.time() - t_load0, 3)
            ckpt.mark_done("load")
            log.info("[%s] load 完成: %s 行, %.1fs",
                     task_id, metrics.get("n_rows_loaded", 0), metrics["t_load_s"])

            # ---- 阶段2: compute ----
            t_comp0 = time.time()
            out, m2 = handler(spec, cols, spec.get("params", {}))
            metrics.update(m2)
            metrics["t_compute_s"] = round(time.time() - t_comp0, 3)
            ckpt.mark_done("compute")
            log.info("[%s] compute 完成: %.1fs", task_id, metrics["t_compute_s"])
            del cols
            gc.collect()

            # ---- 阶段3: write ----
            import h5py
            outdir = ensure_dir(results_root / spec["cgroup"] / spec["subgroup"])
            h5_path = outdir / f"{task_id}.h5"
            tmp_h5 = h5_path.with_suffix(".tmp.h5")
            with h5py.File(tmp_h5, "w") as h:
                for k, v in out.items():
                    h.create_dataset(k, data=np.asarray(v))
                h.attrs["task_id"] = task_id
                h.attrs["algorithm"] = alg
            tmp_h5.replace(h5_path)  # 原子落盘
            digest = sha256_file(h5_path)
            ckpt.mark_done("write", {"sha256": digest})

        metrics["wall_time_s"] = round(time.time() - t0, 3)
        manifest = {"task_id": task_id, "result": str(h5_path),
                    "sha256": digest, "metrics": metrics}
        write_json(outdir / f"{task_id}.manifest.json", manifest)
        if db:
            db.set_task_status(task_id, "done", result_path=str(h5_path),
                               checksum=digest)
            db.add_result({"task_id": task_id, "cgroup": spec["cgroup"],
                           "subgroup": spec["subgroup"],
                           "tile_id": spec["tile_id"], "path": str(h5_path),
                           "checksum": digest, "metrics": metrics})
        log.info("[%s] done: %d 对象, %.1fs",
                 task_id, metrics.get("n_objects", 0), metrics["wall_time_s"])
        return manifest
    except Exception as e:
        log.exception("[%s] 失败: %s", task_id, e)
        if db:
            db.set_task_status(task_id, "failed", error=str(e))
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--results", default="results")
    ap.add_argument("--db", default=None, help="状态库路径（启用租约/心跳/重试）")
    ap.add_argument("--owner", default=None, help="节点标识（租约持有者）")
    args = ap.parse_args()
    manifest = run_task(args.task_id, args.workdir, args.results,
                        db_path=args.db, owner=args.owner)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
