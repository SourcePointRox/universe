"""HTCondor计算节点工作程序入口。

用法: python -m compute.worker <task_id> [--config configs/compute_groups.yaml]
       [--workdir work] [--results results]

从 workdir/tasks/<cg>/<sg>/<tile>/task.json 读取规格，
加载输入文件中的 (ra,dec,z/parallax/e1,e2/time,flux)，
按 algorithm 分派执行，将结果写为 HDF5 + manifest.json(含sha256)。
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.tiling import tile_bounds, load_tiling
from common.utils import ensure_dir, sha256_file, write_json
from compute import algorithms as A


def _load_table_rows(files, want_cols):
    """从输入文件抽取想要的列，拼接为大数组 dict。"""
    from astropy.table import Table, vstack
    tables = []
    colmap = {}
    for fp in files:
        p = Path(fp)
        if not p.exists():
            continue
        ext = p.suffix.lower()
        try:
            if ext in (".fits", ".fit", ".fts") or p.name.lower().endswith(".fits.gz"):
                with __import__("astropy.io.fits", fromlist=["fits"]).open(p, memmap=True) as hdul:
                    t = None
                    for h in hdul[1:]:
                        if hasattr(h, "columns") and h.columns:
                            t = Table.read(h)
                            break
                    if t is None:
                        continue
            elif ext == ".csv":
                t = Table.read(p, format="ascii.csv")
            elif ext == ".ecsv":
                t = Table.read(p, format="ascii.ecsv")
            else:
                continue
        except Exception:
            continue
        lower = {c.lower(): c for c in t.colnames}
        picked = {}
        for want in want_cols:
            for cand in [want, *{"z": ["redshift", "z_spec", "zphot"],
                                 "parallax": ["plx"]}.get(want, [])]:
                if cand in lower:
                    picked[want] = lower[cand]
                    break
        if set(picked) >= set(want_cols):
            tables.append(t[[picked[c] for c in want_cols]])
            colmap = {picked[c]: c for c in want_cols}
    if not tables:
        return None, None
    big = vstack(tables, metadata_conflicts="silent")
    out = {}
    for orig, want in colmap.items():
        out[want] = np.asarray(big[orig], dtype=float)
    return out, len(big)


def _filter_tile(cols, tile_id, ra_bins, dec_bins):
    ra0, ra1, d0, d1 = tile_bounds(tile_id, ra_bins, dec_bins)
    m = (cols["ra"] >= ra0) & (cols["ra"] < ra1) & \
        (cols["dec"] >= d0) & (cols["dec"] < d1)
    return {k: v[m] for k, v in cols.items()}


def run_task(task_id, groups_cfg, workdir, results_root):
    workdir = Path(workdir)
    results_root = ensure_dir(results_root)
    # 定位task.json
    parts = workdir.glob(f"tasks/*/*/{task_id.split('__')[1]}/task.json")
    spec = None
    for p in parts:
        cand = json.loads(p.read_text(encoding="utf-8"))
        if cand["id"] == task_id:
            spec = cand
            break
    if spec is None:
        raise FileNotFoundError(f"找不到任务规格: {task_id}")

    ra_bins, dec_bins = load_tiling(groups_cfg)
    alg = spec["algorithm"]

    want_map = {
        "coords3d_redshift": ["ra", "dec", "z"],
        "coords3d_parallax": ["ra", "dec", "parallax"],
        "shape_catalog": ["ra", "dec", "e1", "e2"],
        "lightcurve_bls": ["time", "flux"],
    }
    upstream_out = results_root / spec["cgroup"]
    t0 = time.time()
    metrics = {"algorithm": alg, "tile": spec["tile_id"]}

    if alg in ("coords3d_redshift", "coords3d_parallax"):
        cols, n = _load_table_rows(spec["input_files"], want_map[alg])
        if cols is None:
            out = {"n": 0}
            metrics["n_objects"] = 0
        else:
            cols = _filter_tile(cols, spec["tile_id"], ra_bins, dec_bins)
            if alg == "coords3d_redshift":
                xyz, dc = A.coords3d_redshift(cols["ra"], cols["dec"], cols["z"])
                out = {"xyz": xyz, "comoving_mpc": dc, "ra": cols["ra"], "dec": cols["dec"], "z": cols["z"]}
            else:
                xyz, valid = A.coords3d_parallax(cols["ra"], cols["dec"], cols["parallax"])
                out = {"xyz_kpc": xyz, "valid_parallax": valid, "ra": cols["ra"], "dec": cols["dec"]}
            metrics["n_objects"] = int(len(cols["ra"]))
    elif alg == "density_cic":
        # 读取上游 SG*.1 的三维坐标结果
        upstream_files = list(upstream_out.glob(f"**/{spec['upstream'][0]}*.h5")) if spec.get("upstream") else []
        if not upstream_files:
            upstream_files = list(results_root.glob(f"**/*{spec['tile_id']}*.h5"))
        xyz_all = []
        for f in upstream_files:
            import h5py
            with h5py.File(f, "r") as h:
                key = "xyz" if "xyz" in h else "xyz_kpc"
                xyz_all.append(h[key][:])
        if xyz_all:
            xyz = np.vstack(xyz_all)
            xyz = xyz[np.all(np.isfinite(xyz), axis=1)]
            lo, hi = xyz.min(axis=0), xyz.max(axis=0)
            pad = 0.02 * (hi - lo + 1e-9)
            delta, rho, _edges = A.density_cic(xyz, lo - pad, hi + pad, ngrid=64)
            k, pk = A.power_spectrum(delta, (hi - lo + 2 * pad))
            out = {"delta": delta, "rho": rho, "pk_k": k, "pk": pk}
            metrics["n_objects"] = int(len(xyz))
        else:
            out = {"n": 0}
            metrics["n_objects"] = 0
    elif alg == "twopcf_power":
        upstream_files = list(results_root.glob(f"**/*{spec['tile_id']}*.h5"))
        xyz_all = []
        for f in upstream_files:
            import h5py
            with h5py.File(f, "r") as h:
                key = "xyz" if "xyz" in h else ("xyz_kpc" if "xyz_kpc" in h else None)
                if key:
                    xyz_all.append(h[key][:])
        if xyz_all:
            xyz = np.vstack(xyz_all)
            xyz = xyz[np.all(np.isfinite(xyz), axis=1)]
            r_edges = np.logspace(-1, 2.5, 20)
            rc, xi = A.twopcf(xyz, r_edges)
            out = {"r_mpc": rc, "xi": xi}
            metrics["n_objects"] = int(len(xyz))
        else:
            out = {"n": 0}
            metrics["n_objects"] = 0
    elif alg == "fof_halos":
        upstream_files = list(results_root.glob(f"**/*{spec['tile_id']}*.h5"))
        xyz_all = []
        for f in upstream_files:
            import h5py
            with h5py.File(f, "r") as h:
                key = "xyz" if "xyz" in h else ("xyz_kpc" if "xyz_kpc" in h else None)
                if key:
                    xyz_all.append(h[key][:])
        if xyz_all:
            xyz = np.vstack(xyz_all)
            xyz = xyz[np.all(np.isfinite(xyz), axis=1)]
            labels, sizes = A.fof_halos(xyz, linking_length=max(1.0, np.ptp(xyz[:, 0]) / 50), min_members=5)
            out = {"xyz": xyz, "group": labels}
            metrics["n_halos"] = len(sizes)
            metrics["n_objects"] = int(len(xyz))
        else:
            out = {"n": 0}
            metrics["n_objects"] = 0
    elif alg == "shape_catalog":
        cols, n = _load_table_rows(spec["input_files"], want_map[alg])
        if cols is None:
            out = {"n": 0}
            metrics["n_objects"] = 0
        else:
            cols = _filter_tile(cols, spec["tile_id"], ra_bins, dec_bins)
            out = {"ra": cols["ra"], "dec": cols["dec"], "e1": cols["e1"], "e2": cols["e2"]}
            metrics["n_objects"] = int(len(cols["ra"]))
    elif alg == "kaiser_squires":
        upstream_files = list(results_root.glob(f"**/SG3.1__{spec['tile_id']}*.h5"))
        ra, dec, e1, e2 = [], [], [], []
        for f in upstream_files:
            import h5py
            with h5py.File(f, "r") as h:
                ra.append(h["ra"][:]); dec.append(h["dec"][:])
                e1.append(h["e1"][:]); e2.append(h["e2"][:])
        if ra:
            kappa, nmap = A.kaiser_squires(np.concatenate(ra), np.concatenate(dec),
                                           np.concatenate(e1), np.concatenate(e2), ngrid=64)
            out = {"kappa": kappa, "nmap": nmap}
            metrics["n_objects"] = int(sum(len(x) for x in ra))
        else:
            out = {"n": 0}
            metrics["n_objects"] = 0
    elif alg == "lightcurve_bls":
        cols, n = _load_table_rows(spec["input_files"], want_map[alg])
        if cols is None:
            out = {"n": 0}
            metrics["n_objects"] = 0
        else:
            ok = np.isfinite(cols["time"]) & np.isfinite(cols["flux"])
            t, f = cols["time"][ok], cols["flux"][ok]
            period, depth, _pg = A.lightcurve_bls(t, f) if len(t) > 50 else (np.nan, np.nan, None)
            out = {"period": np.array([period]), "depth": np.array([depth])}
            metrics["n_points"] = int(len(t))
    elif alg == "stream_likelihood":
        upstream_files = list(results_root.glob(f"**/SG1.1__{spec['tile_id']}*.h5"))
        xyz_all = []
        for f in upstream_files:
            import h5py
            with h5py.File(f, "r") as h:
                if "xyz_kpc" in h:
                    xyz_all.append(h["xyz_kpc"][:])
        if xyz_all:
            xyz = np.vstack(xyz_all)
            xyz = xyz[np.all(np.isfinite(xyz), axis=1)]
            feat = A.stream_likelihood_placeholder(xyz)
            out = {k: np.atleast_1d(v) if np.isscalar(v) else np.asarray(v) for k, v in feat.items()}
            metrics.update({k: v for k, v in feat.items() if np.isscalar(v)})
        else:
            out = {"n": 0}
            metrics["n_objects"] = 0
    elif alg in ("gw_host_match", "hod_mock", "cross_validate"):
        # 占位算法：需要外部事件目录/模拟目录，无数据时输出空结果
        out = {"n": 0}
        metrics["note"] = f"{alg} 需要额外数据(GW事件/模拟目录)，当前为占位执行"
    else:
        raise ValueError(f"未知算法: {alg}")

    metrics["wall_time_s"] = round(time.time() - t0, 3)

    # 写结果 HDF5 + manifest
    import h5py
    outdir = ensure_dir(results_root / spec["cgroup"] / spec["subgroup"])
    h5_path = outdir / f"{task_id}.h5"
    with h5py.File(h5_path, "w") as h:
        for k, v in out.items():
            h.create_dataset(k, data=np.asarray(v))
        h.attrs["task_id"] = task_id
        h.attrs["algorithm"] = alg
    manifest = {"task_id": task_id, "result": str(h5_path),
                "sha256": sha256_file(h5_path), "metrics": metrics}
    write_json(outdir / f"{task_id}.manifest.json", manifest)
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--config", default="configs/compute_groups.yaml")
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--results", default="results")
    args = ap.parse_args()
    manifest = run_task(args.task_id, args.config, args.workdir, args.results)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
