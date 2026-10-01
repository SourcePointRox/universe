"""v2.0 核心模块单元测试：HEALPix / 距离后验 / N-body / Wiener 重建 / 分片 / DB。

运行: python -m pytest tests/test_v2.py -v
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------- HEALPix ----------------
def test_healpix_roundtrip():
    from common.healpix import ang2pix, pix2ang, nside2npix
    rng = np.random.default_rng(1)
    for nside in (1, 4, 16, 64):
        ra = rng.uniform(0, 360, 2000)
        dec = np.degrees(np.arcsin(rng.uniform(-1, 1, 2000)))
        pix = ang2pix(nside, ra, dec)
        assert pix.min() >= 0 and pix.max() < nside2npix(nside)
        ra2, dec2 = pix2ang(nside, pix)
        # 中心回算再投影像素必须一致
        assert np.array_equal(ang2pix(nside, ra2, dec2), pix)


def test_healpix_known_pixels():
    from common.healpix import ang2pix, pix2ang
    # 北极点唯一归属像素0；南极点在4个极帽像素边界上，任一均合法
    assert ang2pix(1, 0.0, 90.0) == 0
    assert ang2pix(1, 0.0, -90.0) in (8, 9, 10, 11)
    ra, dec = pix2ang(1, 0)
    # nside=1 北极帽中心 z=2/3 -> dec≈41.8°（极帽覆盖半个天区）
    assert 41.0 < dec < 43.0


# ---------------- 距离后验 ----------------
def test_parallax_posterior_high_snr():
    from compute.algorithms.distance import parallax_distance_posterior
    # 视差 10±0.5 mas -> 真距离 0.1 kpc，高信噪比下后验应贴近 1/ϖ
    post = parallax_distance_posterior(10.0, 0.5)
    assert abs(post["r_med"] - 0.1) < 0.01
    assert post["r_lo"] < post["r_med"] < post["r_hi"]


def test_parallax_posterior_negative():
    from compute.algorithms.distance import parallax_distance_posterior
    # 负视差：1/ϖ 无定义，贝叶斯后验必须仍然有限且向先验标长收缩
    post = parallax_distance_posterior(-0.5, 1.0, L_kpc=1.35)
    assert np.isfinite(post["r_med"])
    assert 0.1 < post["r_med"] < 10.0


def test_redshift_distance_propagation():
    from compute.algorithms.distance import redshift_distance
    res = redshift_distance(0.1, 0.001, 180.0, 30.0, correct_cmb=False)
    assert 400 < res["d_mpc"] < 600          # z=0.1 -> ~460 Mpc (Planck18)
    assert res["sigma_d"] > 0
    assert res["d_lo"] < res["d_mpc"] < res["d_hi"]


def test_cmb_correction():
    from compute.algorithms.distance import heliocentric_to_cmb
    # 沿偶极方向改正最大（量级 ~369 km/s）
    z = np.array([0.05])
    # 偶极方向 (l,b)=(264,48) -> (ra,dec) ≈ (168, -7)
    z_toward = heliocentric_to_cmb(z, 167.94, -6.94)
    z_away = heliocentric_to_cmb(z, 347.94, 6.94)
    assert z_toward[0] > z_away[0]


# ---------------- N-body ----------------
def test_barnes_hut_vs_direct():
    from compute.algorithms.nbody import barnes_hut_force, direct_force
    rng = np.random.default_rng(2)
    n = 800
    pos = rng.uniform(0, 10, (n, 3))
    mass = np.ones(n)
    f_bh = barnes_hut_force(pos, mass, theta=0.5, eps=0.05)
    f_dir = direct_force(pos, mass, eps=0.05)
    num = np.linalg.norm(f_bh - f_dir, axis=1)
    den = np.linalg.norm(f_dir, axis=1) + 1e-12
    rel = np.median(num / den)
    assert rel < 0.02, f"BH 中位相对误差 {rel:.4f} 超标"


def test_zeldovich_ic_power():
    """初始条件的位移场所含功率应与输入线性谱（乘 D(a_i)²）自洽。

    注：用 a_init=1（D=1）使信号远高于散粒噪声（V/N≈244 Mpc³），
    否则 z 很高时 P 低于散粒噪声、扣噪后无意义。
    """
    from compute.algorithms.nbody import zeldovich_ic, measure_power_spectrum
    from common.cosmology import pk_mpc
    ngrid, box = 32, 200.0
    pos, mom, mass = zeldovich_ic(ngrid, box, 1.0, seed=3)
    k, pk = measure_power_spectrum(pos, ngrid, box)
    ok = (k > 0.05) & (k < 0.3) & np.isfinite(pk)
    pk_lin = pk_mpc(k[ok])
    ratio = pk[ok] / pk_lin
    # CIC 离散化会压低小尺度功率，大尺度应接近 1
    assert 0.4 < np.median(ratio) < 1.6, f"IC 功率谱比值中位数 {np.median(ratio):.2f}"


def test_pm_simulation_growth():
    """小盒子 PM 模拟：大尺度功率增长应与线性理论 D(a)² 定性一致。

    注：初始红移取 z=4（而非物理上更标准的 z=49），因为 24³ 粒子
    200 Mpc 盒子的散粒噪声 V/N≈580 Mpc³，高红移下 P×D² 会低于
    散粒噪声导致无法测量；z=4 时信号/噪声 >10。
    """
    from compute.algorithms.nbody import (measure_power_spectrum,
                                          run_pm_simulation,
                                          zeldovich_ic)
    from common.cosmology import growth_factor
    ngrid, box, z_i, nsteps = 24, 200.0, 4.0, 8
    a_i = 1.0 / (1.0 + z_i)
    pos0, _, _ = zeldovich_ic(ngrid, box, a_i, seed=5)
    res = run_pm_simulation(ngrid, box, z_i, nsteps, seed=5)
    k, pk0 = measure_power_spectrum(pos0, ngrid, box)
    _, pk1 = measure_power_spectrum(res["pos"], ngrid, box)
    ok = (k > 0.04) & (k < 0.15) & np.isfinite(pk0) & np.isfinite(pk1) & (pk0 > 0)
    assert ok.sum() >= 3
    growth_meas = np.median(pk1[ok] / pk0[ok])
    growth_lin = (growth_factor(1.0) / growth_factor(a_i)) ** 2
    # 线性区增长应在理论值的 0.5-2 倍内（低分辨率+少步数的合理容差）
    assert 0.5 * growth_lin < growth_meas < 2.0 * growth_lin, \
        f"增长 {growth_meas:.1f} vs 线性 {growth_lin:.1f}"
    # 结构必须显著增长
    assert growth_meas > 2.0


# ---------------- Wiener 重建 ----------------
def test_wiener_reconstruction():
    from compute.algorithms.reconstruction import wiener_reconstruct
    rng = np.random.default_rng(4)
    box = 400.0
    ngrid = 32
    # 构造已知过密度：中心放一团 + 均匀背景
    n_bg = 20000
    pts = rng.uniform(0, box, (n_bg, 3))
    n_cl = 5000
    cl = rng.normal(box / 2, 25.0, (n_cl, 3))
    pts = np.vstack([pts, cl])
    res = wiener_reconstruct(pts, np.zeros(3), box, ngrid=ngrid, bias=1.0)
    dm = res["delta_mean"]
    center = dm[ngrid // 2, ngrid // 2, ngrid // 2]
    corner = dm[0, 0, 0]
    assert np.all(np.isfinite(dm))
    assert center > corner, "中心团块应重建为高密度"
    assert center > 0.5
    # 后验不确定度非负且整体显著非零
    assert np.all(res["delta_sigma"] >= 0)
    assert np.nanmean(res["delta_sigma"]) > 1e-3


def test_nbar_selection():
    from compute.algorithms.reconstruction import nbar_from_radial_selection
    rng = np.random.default_rng(6)
    # 径向密度随 r 下降 -> n̄(r) 应反映该趋势
    r = np.concatenate([rng.uniform(0, 100, 8000), rng.uniform(100, 400, 2000)])
    ang = rng.normal(size=(len(r), 3))
    ang /= np.linalg.norm(ang, axis=1, keepdims=True)
    pos = ang * r[:, None] + 500.0
    nbar = nbar_from_radial_selection(pos, np.zeros(3), 1000.0, ngrid=16,
                                      observer=(500.0, 500.0, 500.0))
    assert nbar[8, 8, 8] > nbar[0, 0, 0]


# ---------------- 分片管线 ----------------
def test_shard_roundtrip(tmp_path):
    from preprocessing.shard import shard_catalog, load_partition, occupied_pixels
    from astropy.table import Table
    rng = np.random.default_rng(8)
    n = 30000
    t = Table({"ra": rng.uniform(0, 360, n),
               "dec": np.degrees(np.arcsin(rng.uniform(-1, 1, n))),
               "z": rng.uniform(0.01, 0.15, n)})
    fp = tmp_path / "cat.csv"
    t.write(fp, overwrite=True)
    meta = shard_catalog(str(fp), "mock", out_root=tmp_path / "shards", nside=8)
    assert meta["n_rows"] == n
    occ = occupied_pixels(tmp_path / "shards", "mock")
    assert len(occ) > 100
    total = 0
    for px in list(occ)[:10]:
        part = load_partition(tmp_path / "shards", "mock", px, columns=["ra", "z"])
        assert set(part.keys()) == {"ra", "z"}     # 列裁剪生效
        total += len(part["ra"])
    assert total > 0


# ---------------- DB 租约/心跳 ----------------
def test_db_lease_and_requeue(tmp_path):
    from common.db import DB
    db = DB(tmp_path / "t.sqlite")
    for tid in ("SG1.1__hp000001", "SG1.1__hp000002"):
        db.upsert_task({"id": tid, "cgroup": "CG1", "subgroup": "SG1.1",
                        "tile_id": tid.split("__")[1]})
    assert db.lease_task("SG1.1__hp000001", "nodeA")
    # 同一任务，nodeB 拿不到租约
    assert not db.lease_task("SG1.1__hp000001", "nodeB")
    # nodeA 可以续租
    assert db.lease_task("SG1.1__hp000001", "nodeA")
    # 模拟心跳僵死 -> 重排队
    db.conn.execute("UPDATE tasks SET heartbeat=0 WHERE id='SG1.1__hp000001'")
    db.conn.commit()
    assert db.requeue_stale(max_silence=60) == 1
    assert db.task("SG1.1__hp000001")["status"] == "pending"


# ---------------- Checkpoint ----------------
def test_checkpoint_resume(tmp_path):
    from common.checkpoint import Checkpoint
    ck = Checkpoint(tmp_path, "fp1")
    assert not ck.is_done("load")
    ck.mark_done("load", {"n": 10})
    ck2 = Checkpoint(tmp_path, "fp1")
    assert ck2.is_done("load")
    assert ck2.get("n") == 10
    # 指纹变化 -> 全部重来
    ck3 = Checkpoint(tmp_path, "fp2")
    assert not ck3.is_done("load")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
