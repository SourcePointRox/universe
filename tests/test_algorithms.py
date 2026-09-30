"""核心算法单元测试。"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from compute import algorithms as A


def test_coords3d_redshift():
    ra = np.array([0., 90., 180., 270.])
    dec = np.array([0., 0., 0., 0.])
    z = np.array([0.1, 0.5, 1.0, 2.0])
    xyz, dc = A.coords3d_redshift(ra, dec, z)
    assert xyz.shape == (4, 3)
    assert np.all(np.diff(dc) > 0)  # 共动距离随z单调增
    assert abs(dc[2] - 3395.6) < 50  # Planck18 z=1 ~3395 Mpc
    # 赤道上点应落在xy平面
    assert np.allclose(xyz[:, 2], 0, atol=1e-6)


def test_coords3d_parallax():
    xyz, valid = A.coords3d_parallax([0.], [0.], [1.0])  # 1mas -> 1kpc
    assert abs(np.linalg.norm(xyz[0]) - 1.0) < 1e-6
    xyz2, valid2 = A.coords3d_parallax([0.], [0.], [-1.0])
    assert not valid2[0]  # 负视差被标记


def test_density_cic_mass_conservation():
    rng = np.random.default_rng(1)
    pos = rng.uniform(-100, 100, (5000, 3))
    delta, rho, _ = A.density_cic(pos, [-100]*3, [100]*3, ngrid=32)
    # 质量守恒：rho总和 × cell体积 ≈ N
    cell_vol = (200/32)**3
    assert abs(rho.sum()*cell_vol - 5000) / 5000 < 0.05
    assert abs(delta.mean()) < 0.1


def test_power_spectrum_white_noise():
    # 白噪声 delta -> P(k) 应近似平坦
    rng = np.random.default_rng(2)
    delta = rng.normal(0, 1, (32, 32, 32))
    k, pk = A.power_spectrum(delta, 100.0)
    assert len(k) == len(pk)
    assert np.isfinite(pk).sum() > 5


def test_twopcf_clustered():
    # 强成团样本的 xi(r) 应在小尺度上显著为正
    rng = np.random.default_rng(3)
    centers = rng.uniform(0, 1000, (20, 3))
    pts = np.vstack([c + rng.normal(0, 5, (50, 3)) for c in centers])
    rc, xi = A.twopcf(pts, np.logspace(0, 2.5, 12))
    assert len(rc) == len(xi)
    assert np.nanmax(xi) > 0.5  # 小尺度成团信号


def test_fof_halos():
    rng = np.random.default_rng(4)
    centers = rng.uniform(0, 500, (5, 3))
    pts = np.vstack([c + rng.normal(0, 3, (20, 3)) for c in centers])
    labels, sizes = A.fof_halos(pts, linking_length=10, min_members=10)
    assert len(sizes) == 5  # 应识别出5个团
    assert (labels >= 0).sum() == 100


def test_kaiser_squires_recovers_overdensity():
    # 中心加负 shear 切向模式 -> kappa 中心应为正峰
    rng = np.random.default_rng(5)
    n = 20000
    ra = rng.normal(180, 2, n)
    dec = rng.normal(0, 2, n)
    dx = np.deg2rad(ra - 180)
    dy = np.deg2rad(dec)
    r2 = dx**2 + dy**2
    g_t = 0.1 * np.exp(-r2 / 0.01)  # 切向剪切轮廓
    e1 = -g_t * (dx**2 - dy**2) / np.maximum(r2, 1e-10)
    e2 = -g_t * 2 * dx * dy / np.maximum(r2, 1e-10)
    kappa, _ = A.kaiser_squires(ra, dec, e1, e2, ngrid=64)
    c = kappa.shape[0] // 2
    assert kappa[c, c] > 0  # 中心正峰


def test_lightcurve_bls_finds_period():
    rng = np.random.default_rng(6)
    t = np.sort(rng.uniform(0, 30, 2000))
    true_p = 3.7
    f = np.ones_like(t)
    phase = (t / true_p) % 1.0
    f[(phase > 0.4) & (phase < 0.5)] = 0.98  # 2% 凌日
    f += rng.normal(0, 0.002, len(t))
    period, depth, _ = A.lightcurve_bls(t, f, n_periods=300)
    assert abs(period - true_p) / true_p < 0.05


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n=== {len(fns)} 个单元测试全部通过 ===")
