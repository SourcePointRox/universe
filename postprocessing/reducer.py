"""科学归并器：把按 tile 分布的中间结果合并为全局科学产品。
- merge_density : 合并各tile的密度场 (按全局盒重新CIC)
- merge_catalogs: 合并三维天体目录
- global_power  : 全局功率谱
"""
import numpy as np
import h5py
from pathlib import Path

from common.db import DB
from compute import algorithms as A


def _load_xyz_from_results(db: DB, subgroups=("SG1.1", "SG1.2")):
    xyz_list, src = [], {"galaxies_mpc": 0, "stars_kpc": 0}
    for r in db.results():
        if r["subgroup"] not in subgroups or r["status"] != "verified":
            continue
        with h5py.File(r["path"], "r") as h:
            if "xyz" in h:
                xyz_list.append(h["xyz"][:])
                src["galaxies_mpc"] += len(h["xyz"])
            elif "xyz_kpc" in h:
                xyz_list.append(h["xyz_kpc"][:] / 1000.0)  # kpc -> Mpc
                src["stars_kpc"] += len(h["xyz_kpc"])
    if not xyz_list:
        return None, src
    xyz = np.vstack(xyz_list)
    return xyz[np.all(np.isfinite(xyz), axis=1)], src


def global_density_and_power(db: DB, out_h5, ngrid=96):
    from common.utils import ensure_dir
    xyz, src = _load_xyz_from_results(db)
    if xyz is None or len(xyz) == 0:
        return None
    ensure_dir(Path(out_h5).parent)
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    pad = 0.02 * (hi - lo + 1e-9)
    delta, rho, _ = A.density_cic(xyz, lo - pad, hi + pad, ngrid=ngrid)
    k, pk = A.power_spectrum(delta, (hi - lo + 2 * pad))
    with h5py.File(out_h5, "w") as h:
        h.create_dataset("xyz", data=xyz)
        h.create_dataset("delta", data=delta)
        h.create_dataset("rho", data=rho)
        h.create_dataset("pk_k", data=k)
        h.create_dataset("pk", data=pk)
        h.attrs["box_min"] = lo - pad
        h.attrs["box_max"] = hi + pad
        h.attrs["n_objects"] = len(xyz)
        for kk, vv in src.items():
            h.attrs[kk] = vv
    return {"n_objects": len(xyz), **src, "out": str(out_h5)}


def merge_clustering(db: DB, out_h5):
    """合并各tile的两点相关函数 (按DD/DR计数加权均值)。"""
    from common.utils import ensure_dir
    ensure_dir(Path(out_h5).parent)
    xi_list, r_ref = [], None
    for r in db.results():
        if r["subgroup"] != "SG3.1" or r["status"] != "verified":
            continue
        with h5py.File(r["path"], "r") as h:
            if "xi" in h and "r_mpc" in h:
                xi_list.append(h["xi"][:])
                r_ref = h["r_mpc"][:]
    if not xi_list:
        return None
    xi = np.nanmean(np.vstack(xi_list), axis=0)
    with h5py.File(out_h5, "w") as h:
        h.create_dataset("r_mpc", data=r_ref)
        h.create_dataset("xi", data=xi)
    return {"n_tiles": len(xi_list), "out": str(out_h5)}


def merge_halo_catalog(db: DB, out_csv):
    import csv
    from common.utils import ensure_dir
    ensure_dir(Path(out_csv).parent)
    rows = []
    for r in db.results():
        if r["subgroup"] != "SG3.2" or r["status"] != "verified":
            continue
        with h5py.File(r["path"], "r") as h:
            if "xyz" in h and "group" in h:
                xyz, g = h["xyz"][:], h["group"][:]
                for gid in sorted(set(g[g >= 0])):
                    m = g == gid
                    cen = xyz[m].mean(axis=0)
                    rows.append([r["tile_id"], gid, int(m.sum()),
                                 *[float(f"{v:.4f}") for v in cen]])
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tile", "group_id", "n_members", "x_mpc", "y_mpc", "z_mpc"])
        w.writerows(rows)
    return {"n_halos": len(rows), "out": str(out_csv)}
