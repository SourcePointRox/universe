"""算法演示：
1. 合成 N=200k 个河外星系的(ra, dec, z)（含红移空间畸变模拟）
2. 用 Planck18 宇宙学将红移转为共动距离并投影为三维坐标
3. CIC 网格化密度场（64^3）
4. FFT 估计功率谱
5. 两点相关函数 (Landy-Szalay)
6. 可视化：全天投影、密度切片、功率谱、相关函数

运行: python -m compute.demo.run_demo --outdir demo_out
"""
import argparse
import json
from pathlib import Path

import numpy as np
from astropy.cosmology import Planck18

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from compute import algorithms as A
from postprocessing.visualize import (plot_density_slices, plot_power_spectrum,
                                      plot_sky_mollweide, plot_xi)


def generate_mock_catalog(n=200_000, seed=42):
    rng = np.random.default_rng(seed)
    ra = rng.uniform(0, 360, n)
    dec = np.degrees(np.arcsin(rng.uniform(-1, 1, n)))
    z = np.sort(rng.power(2.2, n) * 2.5 + 0.01)
    # 简化RSD：本动速度 -> 红移扰动 v/c (平坦近似)
    sigma_v = 300.0  # km/s
    vel = rng.normal(0, sigma_v, n)
    z_rsd = z * (1.0 + vel / 299792.458)  # v/c
    z_rsd = np.maximum(1e-4, z_rsd)
    return ra, dec, z_rsd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--outdir", default="demo_out")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[demo] 生成 {args.n:,} 个模拟星系...")
    ra, dec, z = generate_mock_catalog(args.n)

    print("[demo] 红移->三维坐标 (Planck18)...")
    xyz, dc = A.coords3d_redshift(ra, dec, z)

    print("[demo] CIC 密度场...")
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    pad = 0.02 * (hi - lo + 1e-9)
    delta, rho, _edges = A.density_cic(xyz, lo - pad, hi + pad, ngrid=64)

    print("[demo] 功率谱...")
    k, pk = A.power_spectrum(delta, (hi - lo + 2 * pad))

    print("[demo] 两点相关函数...")
    r_edges = np.logspace(-1, 2.5, 20)
    rc, xi = A.twopcf(xyz, r_edges)

    print("[demo] 可视化...")
    if len(xyz) > 100000:
        idx = np.random.default_rng(0).choice(len(xyz), 100000, replace=False)
        xyz_plot = xyz[idx]
    else:
        xyz_plot = xyz
    plot_sky_mollweide(xyz_plot, out / "demo_sky.png")
    plot_density_slices(delta, out / "demo_density.png")
    plot_power_spectrum(k, pk, out / "demo_pk.png")
    plot_xi(rc, xi, out / "demo_xi.png")

    meta = {
        "n_objects": int(args.n),
        "box_size_mpc": float((hi - lo).max()),
        "comoving_range": [float(dc.min()), float(dc.max())],
        "mean_delta": float(delta.mean()),
        "std_delta": float(delta.std()),
    }
    (out / "demo_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[demo] 完成。输出目录: {out}")
    print(f"  全天投影: {out/'demo_sky.png'}")
    print(f"  密度切片: {out/'demo_density.png'}")
    print(f"  功率谱:   {out/'demo_pk.png'}")
    print(f"  两点相关: {out/'demo_xi.png'}")


if __name__ == "__main__":
    main()
